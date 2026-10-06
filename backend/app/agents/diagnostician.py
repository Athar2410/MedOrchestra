"""Diagnostician: hypothesis-driven retrieval-augmented differential diagnosis.

  1. hypotheses — the LLM proposes up to 5 candidate conditions, each with a PubMed
     search query (on a Critique re-route, the critique's questions steer this step)
  2. retrieval  — the case query plus every hypothesis query go through MedCPT hybrid
     search + reranking (services/retrieval.py); retrieving per hypothesis finds
     evidence for conditions the symptom text alone would not surface
  3. differential — the LLM ranks the top 3 using only the numbered abstracts; every
     citation is validated against what was actually retrieved (invented ones dropped)
  4. ICD-11 coding in parallel (services/icd.py)

Without an LLM there is no differential (an empty list, which the Critique flags);
without retrieval the LLM still answers but every diagnosis is uncited.
"""

import asyncio
import logging

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, agent_node
from app.agents.case_text import describe_case, search_query
from app.graph.state import ClinicalState
from app.schemas import Citation, Diagnosis, Evidence
from app.services.icd import get_icd
from app.services.llm import LLMError, get_llm
from app.services.retrieval import get_retriever

logger = logging.getLogger(__name__)

# Runs in parallel with Drug Safety (8 s budget), after Triage.
HYPOTHESIS_BUDGET_SECONDS = 5.0
RETRIEVAL_BUDGET_SECONDS = 8.0
DIFFERENTIAL_BUDGET_SECONDS = 8.0
ICD_BUDGET_SECONDS = 3.0
MAX_HYPOTHESES = 5
MAX_DIAGNOSES = 3

HYPOTHESIS_PROMPT = """You are an internal medicine specialist forming a differential diagnosis.
List up to 5 candidate conditions for this presentation, most likely first, including any
dangerous condition that must not be missed. For each, write a short PubMed search query
(under 12 words) that would find clinical evidence about diagnosing that condition in a
patient like this one. Use standard condition names."""

DIFFERENTIAL_PROMPT = """You are an internal medicine specialist. Produce the top 3 differential
diagnoses for the case, most likely first.

Rules:
- Use ONLY the numbered PubMed abstracts provided as evidence. Cite them by their numbers in
  `evidence_ids`. Never cite a number that is not in the list.
- If no abstract supports a diagnosis, you may still list it when clinically important, but
  give it an empty `evidence_ids` list and a lower confidence.
- `confidence` is your probability (0-1) that the condition is the correct primary diagnosis.
  The values do not need to sum to 1. Be calibrated: a vague presentation should get low
  confidence.
- `condition`: a standard disease name as used in ICD-11 (e.g. "Dengue fever",
  "Bacterial meningitis"), with no parentheses, examples or hedging; put nuance in the rationale.
- `rationale`: 1-3 sentences linking case findings to the evidence, citing as [E1], [E2]."""


class Hypothesis(BaseModel):
    condition: str
    search_query: str


class Hypotheses(BaseModel):
    hypotheses: list[Hypothesis]


class DiagnosisDraft(BaseModel):
    condition: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence_ids: list[int]


class Differential(BaseModel):
    diagnoses: list[DiagnosisDraft]


def _feedback(state: ClinicalState) -> str:
    critique = state.get("critique")
    if not (critique and critique.reroute_requested):
        return ""
    previous = ", ".join(f"{d.condition} ({d.confidence:.2f})" for d in state.get("diagnoses", []))
    concerns = "\n".join(f"- {x}" for x in [*critique.flags, *critique.clarification_questions])
    return (
        "\n\nA senior reviewer rejected the previous differential "
        f"[{previous or 'none'}] and raised:\n{concerns}\n"
        "Address these concerns; reconsider alternatives rather than repeating the same list."
    )


def _evidence_block(evidence: list[Evidence]) -> str:
    if not evidence:
        return "No PubMed evidence could be retrieved for this case."
    return "\n\n".join(
        f"[E{i}] PMID {e.pmid} — {e.title} ({e.journal or 'journal n/a'}, {e.pub_year or 'n.d.'})\n"
        f"{e.abstract}"
        for i, e in enumerate(evidence, start=1)
    )


def _citations(ids: list[int], evidence: list[Evidence]) -> tuple[list[Citation], int]:
    """Valid citations for 1-based evidence ids, and how many ids were invalid."""
    citations, invalid = [], 0
    for i in dict.fromkeys(ids):
        if 1 <= i <= len(evidence):
            e = evidence[i - 1]
            citations.append(
                Citation(
                    id=str(e.pmid),
                    title=e.title,
                    url=e.url,
                    snippet=e.abstract[:300],
                    journal=e.journal,
                    year=e.pub_year,
                )
            )
        else:
            invalid += 1
    return citations, invalid


async def _retrieve(queries: list[str], ctx: AgentContext) -> list[Evidence]:
    retriever = get_retriever()
    if retriever is None:
        ctx.think("Evidence retrieval not configured — differential will be uncited")
        return []
    ctx.think(f"Searching PubMed with {len(queries)} queries")
    try:
        return await asyncio.wait_for(retriever.search(queries), RETRIEVAL_BUDGET_SECONDS)
    except Exception as exc:  # retrieval is best-effort: database, model or timeout
        logger.warning("PubMed retrieval failed: %s", exc)
        ctx.think("Evidence retrieval failed — differential will be uncited")
        return []


async def _code_icd(diagnoses: list[Diagnosis], ctx: AgentContext) -> list[Diagnosis]:
    icd = get_icd()
    if icd is None or not diagnoses:
        return diagnoses
    ctx.think("Coding diagnoses in ICD-11")
    try:
        matches = await asyncio.wait_for(
            asyncio.gather(*(icd.code(d.condition) for d in diagnoses)), ICD_BUDGET_SECONDS
        )
    except TimeoutError:
        logger.warning("ICD-11 coding timed out")
        return diagnoses
    return [
        d.model_copy(update={"icd11_code": m.code, "icd11_title": m.title}) if m else d
        for d, m in zip(diagnoses, matches, strict=True)
    ]


@agent_node("diagnostician")
async def diagnostician_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    llm = get_llm()
    if llm is None:
        ctx.think("No LLM configured — cannot form a differential")
        return {"diagnoses": [], "evidence": []}

    case = state["case"]
    triage = state.get("triage")
    feedback = _feedback(state)
    if feedback:
        ctx.think("Re-examining the case with the reviewer's concerns")
    case_text = describe_case(case) + (
        f"\nTriage: {triage.urgency} urgency; red flags: {', '.join(triage.red_flags) or 'none'}"
        if triage
        else ""
    )

    ctx.think("Generating diagnostic hypotheses")
    try:
        hypotheses = (
            await llm.structured(
                system=HYPOTHESIS_PROMPT,
                user=case_text + feedback,
                schema=Hypotheses,
                budget_seconds=HYPOTHESIS_BUDGET_SECONDS,
            )
        ).hypotheses[:MAX_HYPOTHESES]
    except LLMError as exc:
        logger.warning("Hypothesis generation failed: %s", exc)
        hypotheses = []
    if hypotheses:
        ctx.think("Considering: " + ", ".join(h.condition for h in hypotheses))

    evidence = await _retrieve([search_query(case), *(h.search_query for h in hypotheses)], ctx)

    ctx.think(f"Weighing {len(evidence)} abstracts")
    try:
        differential = await llm.structured(
            system=DIFFERENTIAL_PROMPT,
            user=f"{case_text}{feedback}\n\nEvidence:\n{_evidence_block(evidence)}",
            schema=Differential,
            budget_seconds=DIFFERENTIAL_BUDGET_SECONDS,
        )
    except LLMError as exc:
        logger.warning("Differential generation failed: %s", exc)
        ctx.think("LLM unavailable — no differential produced")
        return {"diagnoses": [], "evidence": evidence}

    diagnoses = []
    invalid_total = 0
    for draft in sorted(differential.diagnoses, key=lambda d: d.confidence, reverse=True):
        citations, invalid = _citations(draft.evidence_ids, evidence)
        invalid_total += invalid
        diagnoses.append(
            Diagnosis(
                condition=draft.condition,
                confidence=draft.confidence,
                rationale=draft.rationale,
                citations=citations,
            )
        )
    if invalid_total:
        ctx.think(f"Dropped {invalid_total} citation(s) to abstracts that were not retrieved")

    diagnoses = await _code_icd(diagnoses[:MAX_DIAGNOSES], ctx)
    return {"diagnoses": diagnoses, "evidence": evidence}

"""Critique: adversarial review of the differential (the paper's novel contribution).

Runs concurrently:
  - an LLM "senior attending" review on `critique_model` (a different model family from
    the Diagnostician) -> confidence, per-diagnosis concerns, missed diagnoses, targeted
    questions, and likely treatments for the leading diagnosis
  - a code-side citation check: the MedCPT cross-encoder scores each (diagnosis,
    cited abstract) pair, so a citation that doesn't support its diagnosis is caught
    without trusting either LLM
Then likely treatments are checked against current medications in the DDInter graph.

Without an LLM (or on LLM failure) it falls back to the deterministic checks below.
Re-route rule: confidence below `critique_threshold`, at most `max_reroutes` times.
"""

import asyncio
import logging

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, agent_node
from app.agents.case_text import describe_case
from app.agents.drug_safety import _rxnorm
from app.config import get_settings
from app.graph.state import ClinicalState
from app.schemas import CitationCheck, Critique, Diagnosis, DrugInteraction
from app.services.drug_graph import get_drug_graph
from app.services.drug_names import DrugNameResolver
from app.services.llm import LLMError, get_llm
from app.services.retrieval import get_retriever

logger = logging.getLogger(__name__)

LLM_BUDGET_SECONDS = 7.0
# MedCPT cross-encoder logit for (condition name, abstract). Live: on-topic citations
# 12-16; citations the LLM reviewer judged irrelevant scored 2-7.
# ponytail: one global threshold from a handful of cases; calibrate in the Phase 6 evaluation.
SUPPORT_THRESHOLD = 8.0
# An uncited / unsupported leading diagnosis can't be trusted above this.
UNSUPPORTED_CONFIDENCE_CAP = 0.55

SYSTEM_PROMPT = """You are a senior attending physician reviewing a junior colleague's
differential diagnosis. Be adversarial: challenge unsupported claims, anchoring, and
dangerous conditions that were not considered. Judge the evidence only from the abstracts
shown with each diagnosis.

Return:
- confidence: your probability (0-1) that the leading diagnosis is correct and adequately
  supported. Be calibrated; vague presentations and weak evidence deserve low confidence.
- assessments: for each diagnosis, whether the cited evidence supports it, and the main
  concern in ONE short sentence ("" if none).
- missed_diagnoses: important conditions the differential omitted (empty if none).
- clarification_questions: up to 3 specific questions (history, examination or tests) that
  would best discriminate between the candidates.
- likely_treatments: generic names of first-line drugs likely to be started for the
  leading diagnosis (empty if not applicable).
- summary: 2-3 sentences for the treating clinician."""


class DiagnosisAssessment(BaseModel):
    condition: str
    evidence_supports: bool
    concern: str


class CritiqueReview(BaseModel):
    confidence: float = Field(ge=0, le=1)
    assessments: list[DiagnosisAssessment]
    missed_diagnoses: list[str]
    clarification_questions: list[str]
    likely_treatments: list[str]
    summary: str


def _review_prompt(state: ClinicalState) -> str:
    evidence = {e.pmid: e for e in state.get("evidence", [])}
    lines = []
    for i, d in enumerate(state.get("diagnoses", []), start=1):
        lines.append(f"{i}. {d.condition} (confidence {d.confidence:.2f}): {d.rationale}")
        for c in d.citations:
            e = evidence.get(int(c.id))
            text = e.abstract if e else (c.snippet or "")
            lines.append(f"   Cited PMID {c.id} — {c.title}: {text[:1200]}")
        if not d.citations:
            lines.append("   No citations.")
    meds = ", ".join(i.input_a + " + " + i.input_b for i in state.get("drug_interactions", []))
    triage = state["triage"]
    return (
        f"{describe_case(state['case'])}\n"
        f"Triage: {triage.urgency}; red flags: {', '.join(triage.red_flags) or 'none'}\n"
        f"Known drug interactions: {meds or 'none'}\n\n"
        "Differential under review:\n" + ("\n".join(lines) or "No diagnoses were produced.")
    )


async def _check_citations(diagnoses: list[Diagnosis], state: ClinicalState) -> list[CitationCheck]:
    retriever = get_retriever()
    evidence = {e.pmid: e for e in state.get("evidence", [])}
    pairs = [
        (d.condition, c.id, evidence[int(c.id)])
        for d in diagnoses
        for c in d.citations
        if int(c.id) in evidence
    ]
    if retriever is None or not pairs:
        return []
    try:
        scores = await retriever.score([(cond, f"{e.title}. {e.abstract}") for cond, _, e in pairs])
    except Exception as exc:  # the check is best-effort
        logger.warning("Citation support check failed: %s", exc)
        return []
    return [
        CitationCheck(condition=cond, pmid=pmid, score=float(s), supported=s >= SUPPORT_THRESHOLD)
        for (cond, pmid, _), s in zip(pairs, scores, strict=True)
    ]


async def _treatment_cautions(treatments: list[str], state: ClinicalState) -> list[DrugInteraction]:
    """Interactions between likely new treatments and the patient's current medications."""
    current = {n: m.input for m in state.get("medication_matches", []) for n in m.resolved}
    if not treatments or not current:
        return []
    graph = await asyncio.to_thread(get_drug_graph)
    if graph is None:
        return []
    proposed = {
        n: m.input
        for m in await DrugNameResolver(graph, _rxnorm()).resolve_all(treatments)
        for n in m.resolved
        if n not in current
    }
    cautions = []
    for edge in graph.interactions_among([*current, *proposed]):
        pair = {edge.drug_a, edge.drug_b}
        new = pair & proposed.keys()
        old = pair & current.keys()
        # Ungraded DDInter pairs are noise for a "before you prescribe" warning.
        if new and old and edge.severity != "unknown":
            (n,), (o,) = new, old
            cautions.append(
                DrugInteraction(
                    drug_a=n,
                    drug_b=o,
                    input_a=proposed[n],
                    input_b=current[o],
                    severity=edge.severity,
                    source_ids=list(edge.ddinter_ids),
                )  # fmt: skip
            )
    return cautions


def _rules_review(state: ClinicalState) -> tuple[float, list[str], list[str]]:
    """Deterministic fallback: confidence, flags, questions."""
    settings = get_settings()
    diagnoses = state.get("diagnoses", [])
    triage = state["triage"]
    flags = [f"'{d.condition}' has no supporting citations" for d in diagnoses if not d.citations]
    questions: list[str] = []
    confidence = max((d.confidence for d in diagnoses), default=0.0)
    if confidence < settings.critique_threshold:
        flags.append(f"Top diagnosis confidence {confidence:.2f} is below threshold")
        questions.append("Which findings discriminate between the leading differentials?")
        if triage.missing_vitals:
            questions.append(
                f"Missing vitals ({', '.join(triage.missing_vitals)}) — consider obtaining them"
            )
    return confidence, flags, questions


@agent_node("critique")
async def critique_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    settings = get_settings()
    diagnoses = state.get("diagnoses", [])
    triage = state["triage"]
    reroutes = state.get("reroute_count", 0)

    llm = get_llm()
    ctx.think("Challenging the differential and verifying citations")
    review_call = (
        llm.structured(
            system=SYSTEM_PROMPT,
            user=_review_prompt(state),
            schema=CritiqueReview,
            model=settings.critique_model,
            budget_seconds=LLM_BUDGET_SECONDS,
        )
        if llm and diagnoses
        else None
    )
    results = await asyncio.gather(
        review_call or asyncio.sleep(0),
        _check_citations(diagnoses, state),
        return_exceptions=True,
    )
    review = results[0] if isinstance(results[0], CritiqueReview) else None
    if isinstance(results[0], LLMError):
        logger.warning("Critique LLM failed, using rules: %s", results[0])
        ctx.think("Critique LLM unavailable — falling back to rule checks")
    checks = results[1] if isinstance(results[1], list) else []

    if review:
        confidence = review.confidence
        flags = [f"{a.condition}: {a.concern}" for a in review.assessments if a.concern.strip()] + [
            f"Not considered: {m}" for m in review.missed_diagnoses
        ]
        questions = review.clarification_questions[:3]
        summary = review.summary
    else:
        confidence, flags, questions = _rules_review(state)
        top = diagnoses[0].condition if diagnoses else "no diagnosis"
        summary = (
            f"Leading diagnosis: {top} (confidence {confidence:.2f}), urgency {triage.urgency}."
        )

    unsupported = [c for c in checks if not c.supported]
    for c in unsupported:
        flags.append(f"PMID {c.pmid} does not appear to support '{c.condition}'")
    # A leading diagnosis without a supporting citation caps confidence (forces a re-route).
    if diagnoses:
        top = diagnoses[0].condition
        top_checks = [c for c in checks if c.condition == top]
        top_supported = (
            any(c.supported for c in top_checks) if top_checks else bool(diagnoses[0].citations)
        )
        if not top_supported and confidence > UNSUPPORTED_CONFIDENCE_CAP:
            flags.append(f"Leading diagnosis '{top}' lacks supporting evidence")
            confidence = UNSUPPORTED_CONFIDENCE_CAP

    cautions = await _treatment_cautions(review.likely_treatments if review else [], state)
    if cautions:
        ctx.think(f"{len(cautions)} likely treatment(s) interact with current medications")
    major = [i for i in state.get("drug_interactions", []) if i.severity == "major"]
    if major:
        flags.append(f"{len(major)} major drug interaction(s) need review before treatment")
    if triage.urgency == "HIGH":
        flags.append("HIGH urgency — prioritise immediate assessment over workup")

    reroute = confidence < settings.critique_threshold and reroutes < settings.max_reroutes
    if reroute:
        ctx.emit(
            "reroute",
            reason=f"Confidence {confidence:.2f} below threshold {settings.critique_threshold:.2f}",
            questions=questions,
        )
    elif confidence < settings.critique_threshold:
        ctx.think("Confidence still low but re-route limit reached — finalising with flags")

    update: dict = {
        "critique": Critique(
            confidence_score=confidence,
            flags=flags,
            clarification_questions=questions,
            summary=summary,
            reroute_requested=reroute,
            method="llm" if review else "rules",
            missed_diagnoses=review.missed_diagnoses if review else [],
            citation_checks=checks,
            treatment_cautions=cautions,
        )
    }
    if reroute:
        update["reroute_count"] = reroutes + 1
    return update

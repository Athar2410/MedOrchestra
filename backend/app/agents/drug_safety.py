import asyncio
import logging
from functools import lru_cache

from pydantic import BaseModel

from app.agents.base import AgentContext, agent_node
from app.config import get_settings
from app.graph.state import ClinicalState
from app.schemas import DrugInteraction, MedicationMatch
from app.services.drug_graph import get_drug_graph
from app.services.drug_names import DrugNameResolver, RxNormClient
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

# Explaining every pair would slow the pipeline for long medication lists; the most
# severe graded pairs (already sorted) get explanations, the rest show severity only.
MAX_EXPLAINED = 10
# Runs in parallel with the Diagnostician, after Triage (see triage.LLM_BUDGET_SECONDS).
LLM_BUDGET_SECONDS = 8.0

SYSTEM_PROMPT = """You are a clinical pharmacist advising a doctor.
For each numbered drug pair, the interaction and its severity come from the DDInter
database; do not dispute or change the severity.
For each pair give:
- explanation: 1-2 plain-English sentences on the likely mechanism and the clinical risk.
  If the mechanism is not well established, say so rather than guessing.
- clinical_action: one concrete, practical management or monitoring step.
Return one item per pair, using the pair's number as `pair`."""


class PairExplanation(BaseModel):
    pair: int
    explanation: str
    clinical_action: str


class InteractionExplanations(BaseModel):
    items: list[PairExplanation]


@lru_cache
def _rxnorm() -> RxNormClient | None:
    settings = get_settings()
    return RxNormClient(settings) if settings.rxnorm_enabled else None


async def _explain(
    interactions: list[DrugInteraction], complaint: str, ctx: AgentContext
) -> list[DrugInteraction]:
    # Only graded pairs are explained: for DDInter "unknown" pairs the LLM mostly answers
    # "not well established", which adds latency and noise without information.
    subset = [it for it in interactions if it.severity != "unknown"][:MAX_EXPLAINED]
    llm = get_llm()
    if llm is None or not subset:
        return interactions
    ctx.think(f"Explaining {len(subset)} interaction(s) for the clinician")
    pairs = "\n".join(
        f"{i}. {it.drug_a} + {it.drug_b} — severity: {it.severity}"
        for i, it in enumerate(subset, start=1)
    )
    try:
        result = await llm.structured(
            system=SYSTEM_PROMPT,
            user=f"Presenting complaint: {complaint}\n\nDrug pairs:\n{pairs}",
            schema=InteractionExplanations,
            budget_seconds=LLM_BUDGET_SECONDS,
        )
    except LLMError as exc:
        logger.warning("Drug interaction explanations failed: %s", exc)
        return interactions
    explained: dict[tuple[str, str], DrugInteraction] = {}
    for item in result.items:
        if 1 <= item.pair <= len(subset):
            it = subset[item.pair - 1]
            explained[(it.drug_a, it.drug_b)] = it.model_copy(
                update={
                    "explanation": item.explanation,
                    "clinical_action": item.clinical_action,
                    "explanation_source": "llm",
                }
            )
    return [explained.get((it.drug_a, it.drug_b), it) for it in interactions]


@agent_node("drug_safety")
async def drug_safety_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    case = state["case"]
    meds = list(dict.fromkeys(m.strip() for m in case.medications if m.strip()))
    if not meds:
        ctx.think("No medications listed")
        return {"drug_interactions": [], "medication_matches": []}

    ctx.think("Loading DDInter interaction graph")
    graph = await asyncio.to_thread(get_drug_graph)
    if graph is None:
        ctx.think("DDInter data missing — run `python -m pipelines.download_ddinter`")
        unresolved = [MedicationMatch(input=m, resolved=[], method=None) for m in meds]
        return {"drug_interactions": [], "medication_matches": unresolved}

    ctx.think(f"Normalising {len(meds)} medication name(s)")
    matches = await DrugNameResolver(graph, _rxnorm()).resolve_all(meds)

    # Canonical DDInter name -> the entry the user typed (first one wins for duplicates).
    origin: dict[str, str] = {}
    for match in matches:
        for name in match.resolved:
            origin.setdefault(name, match.input)

    ctx.think(f"Traversing interaction graph across {len(origin)} drug(s)")
    interactions = [
        DrugInteraction(
            drug_a=edge.drug_a,
            drug_b=edge.drug_b,
            input_a=origin[edge.drug_a],
            input_b=origin[edge.drug_b],
            severity=edge.severity,
            source_ids=list(edge.ddinter_ids),
        )
        for edge in graph.interactions_among(origin)
    ]
    interactions = await _explain(interactions, case.chief_complaint, ctx)
    return {"drug_interactions": interactions, "medication_matches": matches}

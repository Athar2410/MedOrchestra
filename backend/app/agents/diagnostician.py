import asyncio

from app.agents.base import AgentContext, agent_node
from app.agents.placeholder_data import KEYWORD_DIFFERENTIALS
from app.config import get_settings
from app.graph.state import ClinicalState
from app.schemas import Diagnosis


@agent_node("diagnostician")
async def diagnostician_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    # Phase 3 replaces the keyword table with PubMed hybrid retrieval + LLM generation
    # that must cite retrieved chunk ids; on a re-route the critique's clarification
    # questions become extra retrieval queries.
    case = state["case"]
    critique = state.get("critique")
    if critique and critique.reroute_requested:
        ctx.think(f"Re-examining after critique: {'; '.join(critique.clarification_questions)}")

    ctx.think("Matching presentation against differential table (placeholder)")
    await asyncio.sleep(get_settings().stub_delay_seconds)

    complaint = case.chief_complaint.lower()
    candidates: dict[str, float] = {}
    for keywords, conditions in KEYWORD_DIFFERENTIALS:
        if any(k in complaint for k in keywords):
            for condition, confidence in conditions:
                candidates[condition] = max(candidates.get(condition, 0), confidence)

    if not candidates:
        candidates = {"Undifferentiated presentation": 0.35}

    ranked = sorted(candidates.items(), key=lambda item: item[1], reverse=True)[:3]
    diagnoses = [
        Diagnosis(
            condition=condition,
            confidence=confidence,
            rationale="Placeholder keyword match; evidence retrieval arrives in Phase 3.",
        )
        for condition, confidence in ranked
    ]
    return {"diagnoses": diagnoses}

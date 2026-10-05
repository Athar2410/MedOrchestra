import asyncio

from app.agents.base import AgentContext, agent_node
from app.agents.news2 import score_news2, urgency_from_news2
from app.config import get_settings
from app.graph.state import ClinicalState
from app.schemas import TriageResult


@agent_node("triage")
async def triage_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    # Phase 2 adds an LLM pass that can escalate urgency from the complaint text
    # (e.g. chest pain with normal vitals); NEWS2 stays as the deterministic floor/fallback.
    case = state["case"]
    ctx.think("Scoring vital signs with NEWS2")
    await asyncio.sleep(get_settings().stub_delay_seconds)

    total, breakdown, missing = score_news2(case.vitals)
    urgency = urgency_from_news2(total, breakdown)
    abnormal = [f"{name} (+{pts})" for name, pts in breakdown.items() if pts]
    reasoning = f"NEWS2 total {total}" + (
        f"; contributing: {', '.join(abnormal)}" if abnormal else "; all recorded vitals in range"
    )
    if missing:
        reasoning += f". Not recorded: {', '.join(missing)}"

    return {
        "triage": TriageResult(
            urgency=urgency,
            news2_score=total,
            news2_breakdown=breakdown,
            missing_vitals=missing,
            reasoning=reasoning,
            method="rules",
        )
    }

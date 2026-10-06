import asyncio
import logging

from app.agents.base import AgentContext, agent_node
from app.graph.state import ClinicalState
from app.schemas import ClinicalReport, Diagnosis
from app.services.icd import get_icd

logger = logging.getLogger(__name__)

ICD_BUDGET_SECONDS = 3.0


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


@agent_node("report")
async def report_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    # ICD-11 codes only the final differential: coding each Diagnostician attempt wasted
    # 1-3 s on re-routed cases.
    diagnoses = await _code_icd(state.get("diagnoses", []), ctx)
    ctx.think("Assembling clinical report")
    return {
        "report": ClinicalReport(
            urgency=state["triage"].urgency,
            triage=state["triage"],
            diagnoses=diagnoses,
            drug_interactions=state.get("drug_interactions", []),
            medication_matches=state.get("medication_matches", []),
            critique=state["critique"],
            reroutes=state.get("reroute_count", 0),
        )
    }

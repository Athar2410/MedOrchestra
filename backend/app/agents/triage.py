import logging

from pydantic import BaseModel

from app.agents.base import AgentContext, agent_node
from app.agents.case_text import describe_patient, describe_vitals
from app.agents.news2 import score_news2, urgency_from_news2
from app.agents.red_flags import find_red_flags
from app.graph.state import ClinicalState
from app.schemas import CaseInput, TriageResult, Urgency
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

_RANK: dict[Urgency, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

# Triage runs before every other agent, so a slow LLM delays the whole case; keep its
# budget small (rules take over on timeout). Worst case end to end: this + Drug Safety's.
LLM_BUDGET_SECONDS = 6.0

SYSTEM_PROMPT = """You are an experienced emergency department triage nurse.
Classify the urgency of the presentation as LOW, MEDIUM or HIGH.

- HIGH: possible time-critical or life-threatening condition (e.g. acute coronary syndrome,
  stroke, sepsis, pulmonary embolism, anaphylaxis, meningitis, GI bleed, ectopic pregnancy),
  or physiological instability.
- MEDIUM: needs prompt assessment but stable.
- LOW: stable, non-urgent.

The NEWS2 early-warning score has already been calculated from the vitals and is given to you.
Use the complaint text to identify red-flag features the vital signs cannot capture.
List red flags as short phrases (empty list if none). Keep reasoning to 1-3 sentences."""


class TriageAssessment(BaseModel):
    urgency: Urgency
    red_flags: list[str]
    reasoning: str


def _case_prompt(case: CaseInput, news2: int, news2_urgency: Urgency, missing: list[str]) -> str:
    return (
        f"Patient: {describe_patient(case)}\n"
        f"Chief complaint: {case.chief_complaint}\n"
        f"Vitals: {describe_vitals(case)}\n"
        f"Not recorded: {', '.join(missing) or 'none'}\n"
        f"NEWS2 score: {news2} (NEWS2 urgency: {news2_urgency})"
    )


def _rules_triage(news2_result: TriageResult, complaint: str) -> TriageResult:
    """Fallback without an LLM: NEWS2, escalated to HIGH on any red-flag phrase."""
    flags = find_red_flags(complaint)
    if not flags:
        return news2_result
    return news2_result.model_copy(
        update={
            "urgency": "HIGH",
            "red_flags": flags,
            "reasoning": f"Red-flag features in complaint: {', '.join(flags)}. "
            f"[{news2_result.reasoning}]",
        }
    )


@agent_node("triage")
async def triage_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    case = state["case"]
    ctx.think("Scoring vital signs with NEWS2")
    total, breakdown, missing = score_news2(case.vitals)
    news2_urgency = urgency_from_news2(total, breakdown)
    abnormal = [f"{name} (+{pts})" for name, pts in breakdown.items() if pts]
    news2_reasoning = f"NEWS2 {total}" + (
        f" ({', '.join(abnormal)})" if abnormal else " (recorded vitals in range)"
    )

    result = TriageResult(
        urgency=news2_urgency,
        news2_score=total,
        news2_breakdown=breakdown,
        news2_urgency=news2_urgency,
        missing_vitals=missing,
        reasoning=news2_reasoning,
        method="rules",
    )

    llm = get_llm()
    if llm is None:
        ctx.think("No LLM configured — using NEWS2 and red-flag rules")
        return {"triage": _rules_triage(result, case.chief_complaint)}

    ctx.think("Assessing complaint for red flags")
    try:
        assessment = await llm.structured(
            system=SYSTEM_PROMPT,
            user=_case_prompt(case, total, news2_urgency, missing),
            schema=TriageAssessment,
            budget_seconds=LLM_BUDGET_SECONDS,
        )
    except LLMError as exc:
        # PRD: fall back to rule-based triage if the LLM fails.
        logger.warning("Triage LLM failed, using rules: %s", exc)
        ctx.think("LLM unavailable — falling back to NEWS2 and red-flag rules")
        return {"triage": _rules_triage(result, case.chief_complaint)}

    # Safety floor: the LLM may escalate urgency but never lower it below NEWS2.
    final = max(news2_urgency, assessment.urgency, key=_RANK.__getitem__)
    reasoning = f"{assessment.reasoning} [{news2_reasoning}]"
    if _RANK[assessment.urgency] < _RANK[news2_urgency]:
        reasoning += f" LLM suggested {assessment.urgency}; kept NEWS2 floor {news2_urgency}."
    return {
        "triage": result.model_copy(
            update={
                "urgency": final,
                "llm_urgency": assessment.urgency,
                "red_flags": assessment.red_flags,
                "reasoning": reasoning,
                "method": "llm",
            }
        )
    }

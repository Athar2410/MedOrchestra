import asyncio

from app.agents.base import AgentContext, agent_node
from app.config import get_settings
from app.graph.state import ClinicalState
from app.schemas import Critique


@agent_node("critique")
async def critique_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    # Phase 4 adds the adversarial LLM review (different model family from the
    # Diagnostician) on top of these deterministic checks.
    settings = get_settings()
    diagnoses = state.get("diagnoses", [])
    interactions = state.get("drug_interactions", [])
    triage = state["triage"]
    reroutes = state.get("reroute_count", 0)

    ctx.think("Checking evidence support for each diagnosis")
    await asyncio.sleep(settings.stub_delay_seconds)

    flags: list[str] = []
    questions: list[str] = []
    for d in diagnoses:
        if not d.citations:
            flags.append(f"'{d.condition}' has no supporting citations")

    confidence = max((d.confidence for d in diagnoses), default=0.0)
    if confidence < settings.critique_threshold:
        flags.append(f"Top diagnosis confidence {confidence:.2f} is below threshold")
        questions.append("Which findings discriminate between the leading differentials?")
        if triage.missing_vitals:
            questions.append(
                f"Missing vitals ({', '.join(triage.missing_vitals)}) — consider obtaining them"
            )

    major = [i for i in interactions if i.severity == "major"]
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

    top = diagnoses[0].condition if diagnoses else "no diagnosis"
    summary = (
        f"Leading diagnosis: {top} (confidence {confidence:.2f}), urgency {triage.urgency}, "
        f"{len(interactions)} drug interaction alert(s)."
    )
    update: dict = {
        "critique": Critique(
            confidence_score=confidence,
            flags=flags,
            clarification_questions=questions,
            summary=summary,
            reroute_requested=reroute,
        )
    }
    if reroute:
        update["reroute_count"] = reroutes + 1
    return update

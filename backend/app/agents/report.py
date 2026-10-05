from app.agents.base import AgentContext, agent_node
from app.graph.state import ClinicalState
from app.schemas import ClinicalReport


@agent_node("report")
async def report_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    ctx.think("Assembling clinical report")
    return {
        "report": ClinicalReport(
            urgency=state["triage"].urgency,
            triage=state["triage"],
            diagnoses=state.get("diagnoses", []),
            drug_interactions=state.get("drug_interactions", []),
            unrecognized_medications=state.get("unrecognized_medications", []),
            critique=state["critique"],
            reroutes=state.get("reroute_count", 0),
        )
    }

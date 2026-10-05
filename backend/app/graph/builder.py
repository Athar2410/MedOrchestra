"""LangGraph wiring.

    START -> triage -+-> diagnostician -+-> critique -> report -> END
                     +-> drug_safety ---+      |
                             diagnostician <---+  (reroute: confidence below
                                                   threshold, max_reroutes cap)

Diagnostician and Drug Safety run in parallel in one superstep; critique runs
once after both. On a re-route only the Diagnostician re-runs — Drug Safety's
result for the current medication list does not change.
"""

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.critique import critique_agent
from app.agents.diagnostician import diagnostician_agent
from app.agents.drug_safety import drug_safety_agent
from app.agents.report import report_agent
from app.agents.triage import triage_agent
from app.graph.state import ClinicalState


def route_after_critique(state: ClinicalState) -> Literal["diagnostician", "report"]:
    return "diagnostician" if state["critique"].reroute_requested else "report"


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(ClinicalState)
    graph.add_node("triage", triage_agent)
    graph.add_node("diagnostician", diagnostician_agent)
    graph.add_node("drug_safety", drug_safety_agent)
    graph.add_node("critique", critique_agent)
    graph.add_node("report", report_agent)

    graph.add_edge(START, "triage")
    graph.add_edge("triage", "diagnostician")
    graph.add_edge("triage", "drug_safety")
    graph.add_edge("diagnostician", "critique")
    graph.add_edge("drug_safety", "critique")
    graph.add_conditional_edges("critique", route_after_critique)
    graph.add_edge("report", END)
    return graph.compile()

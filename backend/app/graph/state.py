import operator
from typing import Annotated, TypedDict

from app.schemas import (
    CaseInput,
    ClinicalReport,
    Critique,
    Diagnosis,
    DrugInteraction,
    TriageResult,
)


class ClinicalState(TypedDict, total=False):
    """Shared state flowing through the LangGraph pipeline (PRD §4.2).

    Diagnostician and Drug Safety run in the same superstep, so any key both of
    them write must have a reducer. Today only `agent_logs` is shared.
    """

    case: CaseInput
    triage: TriageResult
    diagnoses: list[Diagnosis]
    drug_interactions: list[DrugInteraction]
    unrecognized_medications: list[str]
    critique: Critique
    # Number of Critique -> Diagnostician re-routes so far; capped by settings.max_reroutes.
    reroute_count: int
    agent_logs: Annotated[list[str], operator.add]
    report: ClinicalReport

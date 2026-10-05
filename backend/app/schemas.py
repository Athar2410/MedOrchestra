from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Urgency = Literal["LOW", "MEDIUM", "HIGH"]
Severity = Literal["minor", "moderate", "major"]
# ACVPU scale used by NEWS2: Alert, new Confusion, responds to Voice, Pain, Unresponsive.
Consciousness = Literal["A", "C", "V", "P", "U"]
AgentName = Literal["triage", "diagnostician", "drug_safety", "critique", "report"]


class Vitals(BaseModel):
    heart_rate: int | None = Field(None, ge=20, le=250)
    systolic_bp: int | None = Field(None, ge=40, le=300)
    diastolic_bp: int | None = Field(None, ge=20, le=200)
    respiratory_rate: int | None = Field(None, ge=4, le=80)
    spo2: int | None = Field(None, ge=50, le=100)
    temperature_c: float | None = Field(None, ge=30, le=45)
    consciousness: Consciousness = "A"
    on_supplemental_o2: bool = False


class CaseInput(BaseModel):
    chief_complaint: str = Field(min_length=3, max_length=4000)
    vitals: Vitals = Field(default_factory=Vitals)
    medications: list[str] = Field(default_factory=list, max_length=50)
    age: int | None = Field(None, ge=0, le=120)
    sex: Literal["male", "female", "other"] | None = None


class TriageResult(BaseModel):
    urgency: Urgency
    news2_score: int
    news2_breakdown: dict[str, int]
    missing_vitals: list[str]
    reasoning: str
    method: Literal["rules", "llm"]


class Citation(BaseModel):
    id: str
    title: str
    url: str | None = None
    snippet: str | None = None


class Diagnosis(BaseModel):
    condition: str
    confidence: float = Field(ge=0, le=1)
    icd11_code: str | None = None
    rationale: str
    citations: list[Citation] = Field(default_factory=list)


class DrugInteraction(BaseModel):
    drug_a: str
    drug_b: str
    severity: Severity
    mechanism: str
    explanation: str
    evidence: str | None = None


class Critique(BaseModel):
    confidence_score: float = Field(ge=0, le=1)
    flags: list[str]
    clarification_questions: list[str]
    summary: str
    reroute_requested: bool


class ClinicalReport(BaseModel):
    urgency: Urgency
    triage: TriageResult
    diagnoses: list[Diagnosis]
    drug_interactions: list[DrugInteraction]
    unrecognized_medications: list[str]
    critique: Critique
    reroutes: int
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    disclaimer: str = "Research prototype using synthetic/open data. Not for clinical use."

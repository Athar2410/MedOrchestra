from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Urgency = Literal["LOW", "MEDIUM", "HIGH"]
# DDInter levels; "unknown" = interaction documented but not graded.
Severity = Literal["minor", "moderate", "major", "unknown"]
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
    news2_urgency: Urgency
    # The LLM's own call; final urgency is max(news2_urgency, llm_urgency) — never lower.
    llm_urgency: Urgency | None = None
    red_flags: list[str] = Field(default_factory=list)
    missing_vitals: list[str]
    reasoning: str
    method: Literal["rules", "llm"]


class Citation(BaseModel):
    id: str
    title: str
    url: str | None = None
    snippet: str | None = None
    journal: str | None = None
    year: int | None = None


class Evidence(BaseModel):
    """A retrieved PubMed abstract, as shown to the Diagnostician LLM."""

    pmid: int
    title: str
    abstract: str
    journal: str | None = None
    pub_year: int | None = None
    # The search query that retrieved it and its MedCPT cross-encoder score (higher = better).
    query: str
    score: float

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"


class Diagnosis(BaseModel):
    condition: str
    confidence: float = Field(ge=0, le=1)
    icd11_code: str | None = None
    icd11_title: str | None = None
    rationale: str
    citations: list[Citation] = Field(default_factory=list)


class MedicationMatch(BaseModel):
    input: str
    # DDInter drug names; several for combination products (e.g. Percocet).
    resolved: list[str]
    method: Literal["exact", "synonym", "rxnorm", "fuzzy"] | None


class DrugInteraction(BaseModel):
    drug_a: str
    drug_b: str
    # Medication entries as the user typed them that resolved to drug_a / drug_b.
    input_a: str
    input_b: str
    # Always from DDInter, never from the LLM.
    severity: Severity
    explanation: str | None = None
    clinical_action: str | None = None
    explanation_source: Literal["llm", "none"] = "none"
    source: str = "DDInter 2.0"
    source_ids: list[str] = Field(default_factory=list)


class CitationCheck(BaseModel):
    """Code-side check: does the cited abstract support the diagnosis it is cited for?"""

    condition: str
    pmid: str
    score: float  # MedCPT cross-encoder logit for (condition, abstract)
    supported: bool


class Critique(BaseModel):
    confidence_score: float = Field(ge=0, le=1)
    flags: list[str]
    clarification_questions: list[str]
    summary: str
    reroute_requested: bool
    method: Literal["llm", "rules"] = "rules"
    missed_diagnoses: list[str] = Field(default_factory=list)
    citation_checks: list[CitationCheck] = Field(default_factory=list)
    # Interactions between likely treatments for the leading diagnosis and current meds.
    treatment_cautions: list[DrugInteraction] = Field(default_factory=list)


class ClinicalReport(BaseModel):
    urgency: Urgency
    triage: TriageResult
    diagnoses: list[Diagnosis]
    drug_interactions: list[DrugInteraction]
    medication_matches: list[MedicationMatch]
    critique: Critique
    reroutes: int
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    disclaimer: str = "Research prototype using synthetic/open data. Not for clinical use."

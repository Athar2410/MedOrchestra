// Mirrors backend/app/schemas.py and the SSE event contract in backend/app/agents/base.py.

export type Urgency = "LOW" | "MEDIUM" | "HIGH";
// DDInter levels; "unknown" = interaction documented but not graded.
export type Severity = "minor" | "moderate" | "major" | "unknown";
export type Consciousness = "A" | "C" | "V" | "P" | "U";
export type AgentName = "triage" | "diagnostician" | "drug_safety" | "critique" | "report";

export interface Vitals {
  heart_rate: number | null;
  systolic_bp: number | null;
  diastolic_bp: number | null;
  respiratory_rate: number | null;
  spo2: number | null;
  temperature_c: number | null;
  consciousness: Consciousness;
  on_supplemental_o2: boolean;
}

export interface CaseInput {
  chief_complaint: string;
  vitals: Vitals;
  medications: string[];
  age: number | null;
  sex: "male" | "female" | "other" | null;
}

export interface TriageResult {
  urgency: Urgency;
  news2_score: number;
  news2_breakdown: Record<string, number>;
  news2_urgency: Urgency;
  llm_urgency: Urgency | null;
  red_flags: string[];
  missing_vitals: string[];
  reasoning: string;
  method: "rules" | "llm";
}

export interface Citation {
  id: string;
  title: string;
  url: string | null;
  snippet: string | null;
  journal: string | null;
  year: number | null;
}

export interface Diagnosis {
  condition: string;
  confidence: number;
  icd11_code: string | null;
  icd11_title: string | null;
  rationale: string;
  citations: Citation[];
}

export interface MedicationMatch {
  input: string;
  resolved: string[];
  method: "exact" | "synonym" | "rxnorm" | "fuzzy" | null;
}

export interface DrugInteraction {
  drug_a: string;
  drug_b: string;
  input_a: string;
  input_b: string;
  severity: Severity;
  explanation: string | null;
  clinical_action: string | null;
  explanation_source: "llm" | "none";
  source: string;
  source_ids: string[];
}

export interface CitationCheck {
  condition: string;
  pmid: string;
  score: number;
  supported: boolean;
}

export interface Critique {
  confidence_score: number;
  flags: string[];
  clarification_questions: string[];
  summary: string;
  reroute_requested: boolean;
  method: "llm" | "rules";
  missed_diagnoses: string[];
  citation_checks: CitationCheck[];
  treatment_cautions: DrugInteraction[];
}

export interface ClinicalReport {
  urgency: Urgency;
  triage: TriageResult;
  diagnoses: Diagnosis[];
  drug_interactions: DrugInteraction[];
  medication_matches: MedicationMatch[];
  critique: Critique;
  reroutes: number;
  generated_at: string;
  disclaimer: string;
}

interface AgentEventBase {
  agent: AgentName;
  attempt: number;
}

export type RunEvent =
  | { type: "run_started"; run_id: string }
  | ({ type: "agent_started" } & AgentEventBase)
  | ({ type: "agent_thinking"; message: string } & AgentEventBase)
  | ({
      type: "agent_completed";
      duration_ms: number;
      output: Record<string, unknown>;
    } & AgentEventBase)
  | ({ type: "agent_failed"; error: string } & AgentEventBase)
  | ({ type: "reroute"; reason: string; questions: string[] } & AgentEventBase)
  | { type: "report"; report: ClinicalReport }
  | { type: "error"; message: string }
  | { type: "done"; status: "completed" | "failed" };

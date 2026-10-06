"""Plain-text case descriptions shared by the LLM prompts."""

from app.schemas import CaseInput


def describe_vitals(case: CaseInput) -> str:
    v = case.vitals
    vitals = {
        "heart_rate": v.heart_rate,
        "blood_pressure": f"{v.systolic_bp}/{v.diastolic_bp}" if v.systolic_bp else None,
        "respiratory_rate": v.respiratory_rate,
        "spo2": v.spo2,
        "temperature_c": v.temperature_c,
        "consciousness_acvpu": v.consciousness,
        "on_supplemental_o2": v.on_supplemental_o2,
    }
    return ", ".join(f"{k}={val}" for k, val in vitals.items() if val is not None)


def describe_patient(case: CaseInput) -> str:
    parts = [f"{case.age}y" if case.age is not None else "", case.sex or ""]
    return ", ".join(p for p in parts if p) or "not stated"


def describe_case(case: CaseInput) -> str:
    meds = ", ".join(case.medications) or "none listed"
    return (
        f"Patient: {describe_patient(case)}\n"
        f"Chief complaint: {case.chief_complaint}\n"
        f"Vitals: {describe_vitals(case) or 'not recorded'}\n"
        f"Current medications: {meds}"
    )


def search_query(case: CaseInput) -> str:
    """Short query for the MedCPT query encoder (max 64 tokens) and the reranker."""
    who = " ".join(p for p in (f"{case.age} year old" if case.age else "", case.sex or "") if p)
    return f"{who} {case.chief_complaint}".strip()[:300]

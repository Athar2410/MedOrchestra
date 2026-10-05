from app.agents.triage import triage_agent
from app.schemas import CaseInput, Vitals
from app.services.llm import LLMError

NORMAL_VITALS = Vitals(
    heart_rate=80, systolic_bp=130, respiratory_rate=16, spo2=98, temperature_c=36.8
)
UNSTABLE_VITALS = Vitals(
    heart_rate=135, systolic_bp=85, respiratory_rate=28, spo2=90, temperature_c=39.5
)


def case(complaint: str, vitals: Vitals) -> dict:
    return {"case": CaseInput(chief_complaint=complaint, vitals=vitals)}


async def test_no_llm_uses_news2_only():
    triage = (await triage_agent(case("ankle sprain", NORMAL_VITALS)))["triage"]
    assert (triage.urgency, triage.method, triage.llm_urgency) == ("LOW", "rules", None)


async def test_llm_escalates_on_red_flags(fake_llm):
    fake = fake_llm(
        {
            "TriageAssessment": {
                "urgency": "HIGH",
                "red_flags": ["chest pain radiating to arm", "diaphoresis"],
                "reasoning": "Possible ACS.",
            }
        }
    )
    triage = (await triage_agent(case("crushing chest pain, sweaty", NORMAL_VITALS)))["triage"]
    assert (triage.urgency, triage.news2_urgency, triage.method) == ("HIGH", "LOW", "llm")
    assert triage.red_flags == ["chest pain radiating to arm", "diaphoresis"]
    assert "NEWS2 score: 0" in fake.calls[0]["user"]


async def test_llm_cannot_downgrade_below_news2(fake_llm):
    fake_llm({"TriageAssessment": {"urgency": "LOW", "red_flags": [], "reasoning": "Looks fine."}})
    triage = (await triage_agent(case("feels hot", UNSTABLE_VITALS)))["triage"]
    assert (triage.urgency, triage.llm_urgency, triage.news2_urgency) == ("HIGH", "LOW", "HIGH")
    assert "kept NEWS2 floor HIGH" in triage.reasoning


async def test_llm_failure_falls_back_to_news2(fake_llm):
    fake_llm({"TriageAssessment": LLMError("rate limited")})
    triage = (await triage_agent(case("feels hot", UNSTABLE_VITALS)))["triage"]
    assert (triage.urgency, triage.method) == ("HIGH", "rules")


async def test_rules_fallback_escalates_red_flags_with_normal_vitals(fake_llm):
    # Regression: with the LLM down, NEWS2 alone triaged classic ACS as LOW.
    fake_llm({"TriageAssessment": LLMError("503")})
    triage = (
        await triage_agent(
            case("Crushing chest pain radiating to the jaw, sweating", NORMAL_VITALS)
        )
    )["triage"]
    assert (triage.urgency, triage.news2_urgency, triage.method) == ("HIGH", "LOW", "rules")
    assert triage.red_flags == ["chest pain", "pain radiating to arm/jaw", "diaphoresis"]


async def test_rules_without_red_flags_keep_news2():
    triage = (await triage_agent(case("Mild sore throat for two days", NORMAL_VITALS)))["triage"]
    assert (triage.urgency, triage.red_flags) == ("LOW", [])


async def test_triage_llm_call_uses_small_budget(fake_llm):
    fake = fake_llm({"TriageAssessment": {"urgency": "LOW", "red_flags": [], "reasoning": "ok"}})
    await triage_agent(case("sore throat", NORMAL_VITALS))
    assert fake.calls[0]["budget_seconds"] == 6.0

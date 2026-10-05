from app.agents.drug_safety import drug_safety_agent
from app.schemas import CaseInput


def state(*meds: str) -> dict:
    return {"case": CaseInput(chief_complaint="chest pain", medications=list(meds))}


async def test_finds_interactions_via_synonyms_without_llm():
    result = await drug_safety_agent(state("Aspirin 75mg", "warfarin", "paracetamol", "metoprolol"))
    found = [(i.input_a, i.input_b, i.severity) for i in result["drug_interactions"]]
    assert found == [("Aspirin 75mg", "warfarin", "major"), ("paracetamol", "warfarin", "minor")]
    first = result["drug_interactions"][0]
    assert (first.drug_a, first.explanation, first.explanation_source) == (
        "acetylsalicylic acid",
        None,
        "none",
    )
    assert first.source_ids == ["DDInter2", "DDInter1"]


async def test_unresolved_medications_are_reported():
    result = await drug_safety_agent(state("warfarin", "unobtainium"))
    matches = {m.input: (m.resolved, m.method) for m in result["medication_matches"]}
    assert matches == {"warfarin": (["warfarin"], "exact"), "unobtainium": ([], None)}
    assert result["drug_interactions"] == []


async def test_duplicate_entries_do_not_self_interact():
    result = await drug_safety_agent(state("aspirin", "acetylsalicylic acid", "warfarin"))
    assert len(result["drug_interactions"]) == 1


async def test_llm_explanations_attached_by_pair_number(fake_llm):
    fake = fake_llm(
        {
            "InteractionExplanations": {
                "items": [
                    {"pair": 2, "explanation": "Minor effect.", "clinical_action": "Monitor INR."},
                    {"pair": 1, "explanation": "Bleeding risk.", "clinical_action": "Avoid."},
                ]
            }
        }
    )
    result = await drug_safety_agent(state("aspirin", "warfarin", "paracetamol"))
    major, minor = result["drug_interactions"]
    assert (major.explanation, major.clinical_action, major.explanation_source) == (
        "Bleeding risk.",
        "Avoid.",
        "llm",
    )
    assert minor.explanation == "Minor effect."
    # The LLM is told the DDInter severity and must not change it.
    assert "severity: major" in fake.calls[0]["user"]
    assert major.severity == "major"


async def test_unknown_severity_pairs_are_not_sent_to_llm(fake_llm):
    fake = fake_llm(
        {
            "InteractionExplanations": {
                "items": [{"pair": 1, "explanation": "x", "clinical_action": "y"}]
            }
        }
    )
    # metoprolol + amlodipine is "unknown" in the fixture; warfarin + aspirin is major.
    result = await drug_safety_agent(state("metoprolol", "amlodipine", "warfarin", "aspirin"))
    assert "amlodipine" not in fake.calls[0]["user"]
    unknown = [i for i in result["drug_interactions"] if i.severity == "unknown"]
    assert len(unknown) == 1 and unknown[0].explanation is None


async def test_no_llm_call_when_only_unknown_pairs(fake_llm):
    fake = fake_llm({})
    await drug_safety_agent(state("metoprolol", "amlodipine"))
    assert fake.calls == []

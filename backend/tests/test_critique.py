from app.agents.critique import critique_agent
from app.schemas import (
    CaseInput,
    Citation,
    Diagnosis,
    MedicationMatch,
    TriageResult,
)
from app.services.llm import LLMError
from tests.conftest import make_evidence

TRIAGE = TriageResult(
    urgency="MEDIUM", news2_score=3, news2_breakdown={}, news2_urgency="MEDIUM",
    missing_vitals=[], reasoning="", method="rules",
)  # fmt: skip


def state(*, cited_title="Pneumonia review", meds=()):
    evidence = [make_evidence(1, cited_title)]
    return {
        "case": CaseInput(chief_complaint="fever and cough"),
        "triage": TRIAGE,
        "evidence": evidence,
        "diagnoses": [
            Diagnosis(
                condition="Community-acquired pneumonia",
                confidence=0.8,
                rationale="x",
                citations=[Citation(id="1", title=cited_title)],
            )
        ],
        "medication_matches": [
            MedicationMatch(input=m, resolved=[m], method="exact") for m in meds
        ],
        "reroute_count": 0,
    }


def review(confidence=0.8, treatments=()):
    return {
        "confidence": confidence,
        "assessments": [
            {"condition": "Community-acquired pneumonia", "evidence_supports": True, "concern": ""}
        ],
        "missed_diagnoses": ["Pulmonary embolism"],
        "clarification_questions": ["Any calf swelling?", "Sputum colour?", "Travel?", "Extra?"],
        "likely_treatments": list(treatments),
        "summary": "Plausible CAP.",
    }


async def test_llm_review_drives_critique_on_critique_model(fake_llm, fake_retriever):
    llm = fake_llm({"CritiqueReview": review()})
    fake_retriever([])
    c = (await critique_agent(state()))["critique"]
    assert (c.method, c.confidence_score, c.reroute_requested) == ("llm", 0.8, False)
    assert c.missed_diagnoses == ["Pulmonary embolism"]
    assert "Not considered: Pulmonary embolism" in c.flags
    assert len(c.clarification_questions) == 3
    assert llm.calls[0]["model"] == "qwen/qwen3.8-27b"
    assert "Cited PMID 1" in llm.calls[0]["user"]
    assert c.citation_checks[0].supported


async def test_unsupported_citation_caps_confidence_and_reroutes(fake_llm, fake_retriever):
    fake_llm({"CritiqueReview": review(confidence=0.9)})
    fake_retriever([])
    result = await critique_agent(state(cited_title="unrelated paper"))
    c = result["critique"]
    assert c.confidence_score == 0.55
    assert c.reroute_requested and result["reroute_count"] == 1
    assert any("does not appear to support" in f for f in c.flags)


async def test_llm_failure_falls_back_to_rules(fake_llm):
    fake_llm({"CritiqueReview": LLMError("429")})
    c = (await critique_agent(state()))["critique"]
    assert (c.method, c.confidence_score) == ("rules", 0.8)


async def test_likely_treatment_interacting_with_current_med(fake_llm, fake_retriever):
    # Fixture DDInter: ibuprofen + warfarin is major.
    fake_llm({"CritiqueReview": review(treatments=["Ibuprofen"])})
    fake_retriever([])
    c = (await critique_agent(state(meds=["warfarin"])))["critique"]
    assert [(t.input_a, t.input_b, t.severity) for t in c.treatment_cautions] == [
        ("Ibuprofen", "warfarin", "major")
    ]

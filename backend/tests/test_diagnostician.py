from app.agents.diagnostician import diagnostician_agent
from app.schemas import CaseInput, Critique, Diagnosis
from app.services.llm import LLMError
from tests.conftest import make_evidence

CASE = CaseInput(chief_complaint="Crushing chest pain radiating to the jaw", age=58, sex="male")

HYPOTHESES = {
    "hypotheses": [
        {"condition": "Acute coronary syndrome", "search_query": "ACS chest pain diagnosis"},
        {"condition": "Aortic dissection", "search_query": "aortic dissection chest pain"},
    ]
}
EVIDENCE = [make_evidence(111, "Troponin in ACS"), make_evidence(222, "Dissection imaging")]


def differential(*drafts: tuple) -> dict:
    """drafts: (condition, confidence, rationale, evidence_ids) tuples."""
    keys = ("condition", "confidence", "rationale", "evidence_ids")
    return {"diagnoses": [dict(zip(keys, d, strict=True)) for d in drafts]}


async def test_no_llm_returns_empty_differential():
    result = await diagnostician_agent({"case": CASE})
    assert (result["diagnoses"], result["evidence"]) == ([], [])


async def test_hypothesis_queries_drive_retrieval_and_citations_are_mapped(
    fake_llm, fake_retriever
):
    llm = fake_llm(
        {
            "Hypotheses": HYPOTHESES,
            "Differential": differential(
                ("Aortic dissection", 0.2, "Less likely [E2].", [2]),
                ("Acute coronary syndrome", 0.7, "Classic [E1].", [1]),
            ),
        }
    )
    retriever = fake_retriever(EVIDENCE)
    result = await diagnostician_agent({"case": CASE})

    assert retriever.calls == [
        [
            "58 year old male Crushing chest pain radiating to the jaw",
            "ACS chest pain diagnosis",
            "aortic dissection chest pain",
        ]
    ]
    # The evidence block shown to the LLM is numbered and carries PMIDs.
    assert "[E1] PMID 111" in llm.calls[1]["user"]
    acs, dissection = result["diagnoses"]  # sorted by confidence
    assert (acs.condition, acs.confidence) == ("Acute coronary syndrome", 0.7)
    assert [(c.id, c.url) for c in acs.citations] == [
        ("111", "https://pubmed.ncbi.nlm.nih.gov/111/")
    ]
    assert dissection.citations[0].id == "222"
    assert result["evidence"] == EVIDENCE


async def test_invented_citations_are_dropped(fake_llm, fake_retriever):
    fake_llm(
        {
            "Hypotheses": HYPOTHESES,
            "Differential": differential(("ACS", 0.7, "x", [1, 7, 0, 1])),
        }
    )
    fake_retriever(EVIDENCE)
    (acs,) = (await diagnostician_agent({"case": CASE}))["diagnoses"]
    assert [c.id for c in acs.citations] == ["111"]  # 7 and 0 invalid, duplicate 1 removed


async def test_top_three_only(fake_llm, fake_retriever):
    fake_llm(
        {
            "Hypotheses": HYPOTHESES,
            "Differential": differential(*[(f"D{i}", i / 10, "x", []) for i in range(1, 6)]),
        }
    )
    fake_retriever([])
    diagnoses = (await diagnostician_agent({"case": CASE}))["diagnoses"]
    assert [d.condition for d in diagnoses] == ["D5", "D4", "D3"]


async def test_retrieval_failure_still_produces_uncited_differential(fake_llm, fake_retriever):
    llm = fake_llm({"Hypotheses": HYPOTHESES, "Differential": differential(("ACS", 0.6, "x", [1]))})
    fake_retriever(RuntimeError("database down"))
    result = await diagnostician_agent({"case": CASE})
    assert result["diagnoses"][0].citations == []
    assert "No PubMed evidence could be retrieved" in llm.calls[1]["user"]


async def test_hypothesis_failure_falls_back_to_case_query(fake_llm, fake_retriever):
    fake_llm({"Hypotheses": LLMError("503"), "Differential": differential(("ACS", 0.6, "x", []))})
    retriever = fake_retriever([])
    await diagnostician_agent({"case": CASE})
    assert retriever.calls == [["58 year old male Crushing chest pain radiating to the jaw"]]


async def test_differential_failure_returns_empty_but_keeps_evidence(fake_llm, fake_retriever):
    fake_llm({"Hypotheses": HYPOTHESES, "Differential": LLMError("503")})
    fake_retriever(EVIDENCE)
    result = await diagnostician_agent({"case": CASE})
    assert (result["diagnoses"], result["evidence"]) == ([], EVIDENCE)


async def test_reroute_passes_critique_concerns_to_both_prompts(fake_llm, fake_retriever):
    llm = fake_llm({"Hypotheses": HYPOTHESES, "Differential": differential(("PE", 0.5, "x", []))})
    fake_retriever([])
    state = {
        "case": CASE,
        "reroute_count": 1,
        "diagnoses": [Diagnosis(condition="GERD", confidence=0.3, rationale="x")],
        "critique": Critique(
            confidence_score=0.3,
            flags=["'GERD' has no supporting citations"],
            clarification_questions=["Was the pain exertional?"],
            summary="",
            reroute_requested=True,
        ),
    }
    await diagnostician_agent(state)
    for call in llm.calls:
        assert "rejected the previous differential [GERD (0.30)]" in call["user"]
        assert "Was the pain exertional?" in call["user"]


async def test_reroute_reuses_evidence_and_searches_only_new_queries(fake_llm, fake_retriever):
    fake_llm({"Hypotheses": HYPOTHESES, "Differential": differential(("PE", 0.5, "x", [1, 2]))})
    retriever = fake_retriever([make_evidence(333, "New dissection paper")])
    first = make_evidence(111, "Troponin in ACS", query="ACS chest pain diagnosis")
    state = {
        "case": CASE,
        "reroute_count": 1,
        "evidence": [first],
        "diagnoses": [],
        "critique": Critique(
            confidence_score=0.3,
            flags=[],
            clarification_questions=[],
            summary="",
            reroute_requested=True,
        ),  # fmt: skip
    }
    result = await diagnostician_agent(state)
    # Case query and the already-run ACS query are skipped; only the new one is searched.
    assert retriever.calls == [["aortic dissection chest pain"]]
    assert [e.pmid for e in result["evidence"]] == [111, 333]

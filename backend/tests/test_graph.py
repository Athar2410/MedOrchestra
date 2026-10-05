from app.graph.builder import build_graph


async def test_confident_case_runs_once_and_finds_interaction(chest_pain_case):
    state = await build_graph().ainvoke({"case": chest_pain_case, "reroute_count": 0})

    report = state["report"]
    assert report.reroutes == 0
    assert report.urgency == "HIGH"
    assert report.diagnoses[0].condition == "Acute coronary syndrome"
    assert [(i.input_a, i.drug_a, i.drug_b, i.severity) for i in report.drug_interactions] == [
        ("aspirin 75mg od", "acetylsalicylic acid", "warfarin", "major")
    ]
    assert not report.critique.reroute_requested
    assert sum("diagnostician" in log for log in state["agent_logs"]) == 1


async def test_low_confidence_reroutes_exactly_once(vague_case):
    state = await build_graph().ainvoke({"case": vague_case, "reroute_count": 0})

    report = state["report"]
    assert report.reroutes == 1
    # Still below threshold after the retry, but the cap stops further loops.
    assert report.critique.confidence_score < 0.6
    assert not report.critique.reroute_requested
    assert sum("diagnostician" in log for log in state["agent_logs"]) == 2
    assert sum("critique" in log for log in state["agent_logs"]) == 2
    assert sum("drug_safety" in log for log in state["agent_logs"]) == 1

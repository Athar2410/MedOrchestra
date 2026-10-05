from app.services.drug_graph import get_drug_graph


def test_duplicate_pairs_keep_most_severe_level():
    graph = get_drug_graph()
    # Moderate in file A, Major (reversed order) in file B.
    assert graph.graph.edges["warfarin", "acetylsalicylic acid"]["severity"] == "major"
    # A graded level beats "unknown" regardless of file order.
    assert graph.graph.edges["metformin", "furosemide"]["severity"] == "minor"


def test_interactions_among_finds_pairs_within_set_sorted_by_severity():
    graph = get_drug_graph()
    edges = graph.interactions_among(
        ["warfarin", "acetylsalicylic acid", "acetaminophen", "ibuprofen", "not-a-drug"]
    )
    assert [(e.drug_a, e.drug_b, e.severity) for e in edges] == [
        ("acetylsalicylic acid", "warfarin", "major"),
        ("ibuprofen", "warfarin", "major"),
        ("acetaminophen", "warfarin", "minor"),
    ]
    assert edges[0].ddinter_ids == ("DDInter2", "DDInter1")


def test_no_pairs_for_non_interacting_drugs():
    assert get_drug_graph().interactions_among(["simvastatin", "warfarin"]) == []

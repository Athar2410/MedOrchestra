from app.services.retrieval import pool_candidates, select_evidence


def row(pmid: int) -> tuple:
    return (pmid, f"T{pmid}", f"A{pmid}", "J", 2024)


def test_pool_interleaves_by_rank_dedupes_and_caps():
    per_query = [
        [("case", row(1)), ("case", row(2)), ("case", row(3))],
        [("acs", row(2)), ("acs", row(4))],
    ]
    pool = pool_candidates(per_query, cap=10)
    # Rank 1 of every query comes first, so PMID 2 goes to "acs" (its rank 1), not to
    # "case" (its rank 2); the duplicate is dropped.
    assert [(q, r[0]) for q, r in pool] == [("case", 1), ("acs", 2), ("acs", 4), ("case", 3)]
    assert len(pool_candidates(per_query, cap=2)) == 2


def test_select_round_robins_across_queries_by_score():
    pool = [("case", row(1)), ("case", row(2)), ("case", row(3)), ("pe", row(9))]
    scores = [0.1, 5.0, 3.0, -1.0]
    selected = select_evidence(["case", "pe"], pool, scores, k=3)
    # Best "case" hit, then the only "pe" hit (despite its low score), then next "case".
    assert [(e.query, e.pmid) for e in selected] == [("case", 2), ("pe", 9), ("case", 3)]
    assert selected[0].url == "https://pubmed.ncbi.nlm.nih.gov/2/"


def test_low_scoring_abstracts_are_dropped_and_free_their_slots():
    pool = [("case", row(1)), ("acs", row(2)), ("acs", row(3)), ("acs", row(4))]
    scores = [-7.8, 15.9, 14.0, 12.0]
    selected = select_evidence(["case", "acs"], pool, scores, k=3, min_score=0.0)
    assert [e.pmid for e in selected] == [2, 3, 4]

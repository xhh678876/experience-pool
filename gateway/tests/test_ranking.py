from app.ranking import Candidate, score_candidates, q_scalar


def make(eid: str, sim: float, q: float, visit: int) -> Candidate:
    return Candidate(
        experience_id=eid,
        similarity=sim,
        q_outcome=q, q_intent=q, q_execution=q, q_orchestration=q, q_expression=q,
        visit_count=visit,
    )


def test_q_scalar_weighted_sum():
    c = Candidate("x", 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0)
    # weight outcome = 0.30
    assert abs(q_scalar(c) - 0.30) < 1e-6


def test_score_orders_by_combined():
    cands = [
        make("a", 0.9, 0.8, 100),  # high sim, high Q, lots of visits -> low UCB
        make("b", 0.85, 0.4, 1),   # lower sim, lower Q, but high UCB
        make("c", 0.8, 0.7, 50),
    ]
    ranked = score_candidates(cands, w_similarity=0.55, w_q=0.35, c_exploration=0.10)
    ids = [r[0].experience_id for r in ranked]
    assert ids[0] == "a", f"top should be a, got {ids}"
    # b's UCB should at least pull it ahead of where a pure similarity sort would put it
    pure_sim = sorted(cands, key=lambda c: c.similarity, reverse=True)
    assert pure_sim[2].experience_id == "c"
    # In mixed: b has high UCB so it should beat c.
    assert ids.index("b") < ids.index("c"), f"UCB should lift b above c, got {ids}"


def test_empty_input():
    assert score_candidates([], w_similarity=0.5, w_q=0.3, c_exploration=0.1) == []


def test_zscore_handles_zero_variance():
    # All identical similarities -> z-score should not blow up.
    cands = [make(f"x{i}", 0.5, 0.5, 10) for i in range(3)]
    ranked = score_candidates(cands, w_similarity=0.5, w_q=0.5, c_exploration=0.0)
    assert len(ranked) == 3
    for _, total, *_ in ranked:
        assert total == 0.0  # all z-scores are 0, no UCB

"""Pure-math test of the delayed credit assignment update rule.

We don't want a postgres dep here, so we extract the formula into a small helper
and unit-test it. The same formula lives in workers/credit_assigner.py; if it
ever drifts the two implementations should be reconciled.
"""

from __future__ import annotations

ALPHA = 0.2


def update_q(old_q: dict[str, float], reward: dict[str, float], confidence: float) -> dict[str, float]:
    eff = ALPHA * confidence
    return {d: (1 - eff) * old_q[d] + eff * reward[d] for d in old_q}


def test_zero_confidence_is_no_op():
    old = {"a": 0.5, "b": -0.2}
    r = {"a": 1.0, "b": 1.0}
    new = update_q(old, r, 0.0)
    assert new == old


def test_full_confidence_moves_toward_reward():
    old = {"a": 0.0}
    r = {"a": 1.0}
    new = update_q(old, r, 1.0)
    # eff = 0.2, so new = 0.8*0 + 0.2*1 = 0.2
    assert abs(new["a"] - 0.2) < 1e-9


def test_negative_reward_pulls_q_down():
    old = {"a": 0.5}
    r = {"a": -1.0}
    new = update_q(old, r, 0.8)
    eff = 0.2 * 0.8  # 0.16
    expected = (1 - eff) * 0.5 + eff * -1.0
    assert abs(new["a"] - expected) < 1e-9
    assert new["a"] < old["a"]


def test_repeated_updates_converge_toward_reward():
    q = {"a": 0.0}
    r = {"a": 0.5}
    for _ in range(50):
        q = update_q(q, r, 1.0)
    # geometric series toward 0.5
    assert abs(q["a"] - 0.5) < 1e-3


def test_one_hop_intuition():
    # Parent saw reward 1.0 first. Then child uses parent and child gets reward -1.0.
    # Parent's Q should drop, but not below child's reward.
    parent_q = {"a": 1.0}  # already absorbed parent's own reward
    child_r = {"a": -1.0}
    parent_q = update_q(parent_q, child_r, 0.9)
    assert -1.0 < parent_q["a"] < 1.0

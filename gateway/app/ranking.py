"""Mixed ranking from day one.

Score = w_s * sim_z  +  w_q * q_hat  +  c * sqrt(ln(N + 1) / max(n_i, 1))

  sim_z   : z-score normalized cosine similarity across the candidate set
  q_hat   : standardized scalar Q (weighted sum of the 5 dims, then z-scored)
  UCB term: classic UCB1 exploration; N = sum visit_count over candidates

Q starts at the judge's initial scalar; CreditAssigner refines it later.
This means M2 already exposes the final ranking; only the *quality* of Q
improves over time. No behavior change at M5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


Q_WEIGHTS = {
    "outcome": 0.30,
    "intent": 0.20,
    "execution": 0.20,
    "orchestration": 0.15,
    "expression": 0.15,
}


@dataclass
class Candidate:
    experience_id: str
    similarity: float
    q_outcome: float
    q_intent: float
    q_execution: float
    q_orchestration: float
    q_expression: float
    visit_count: int


def q_scalar(c: Candidate) -> float:
    return (
        Q_WEIGHTS["outcome"] * c.q_outcome
        + Q_WEIGHTS["intent"] * c.q_intent
        + Q_WEIGHTS["execution"] * c.q_execution
        + Q_WEIGHTS["orchestration"] * c.q_orchestration
        + Q_WEIGHTS["expression"] * c.q_expression
    )


def _zscore(values: list[float]) -> list[float]:
    if not values:
        return []
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / max(len(values), 1)
    std = math.sqrt(var) or 1.0
    return [(v - mean) / std for v in values]


def score_candidates(
    candidates: list[Candidate],
    *,
    w_similarity: float,
    w_q: float,
    c_exploration: float,
) -> list[tuple[Candidate, float, float, float, float]]:
    """Returns (candidate, total_score, sim_component, q_component, ucb_component)."""
    if not candidates:
        return []

    sim_z = _zscore([c.similarity for c in candidates])
    q_z = _zscore([q_scalar(c) for c in candidates])
    total_visits = sum(c.visit_count for c in candidates)

    out: list[tuple[Candidate, float, float, float, float]] = []
    for cand, sz, qz in zip(candidates, sim_z, q_z, strict=True):
        ucb = c_exploration * math.sqrt(
            math.log(total_visits + 1) / max(cand.visit_count, 1)
        )
        sim_comp = w_similarity * sz
        q_comp = w_q * qz
        total = sim_comp + q_comp + ucb
        out.append((cand, total, sim_comp, q_comp, ucb))
    out.sort(key=lambda x: x[1], reverse=True)
    return out

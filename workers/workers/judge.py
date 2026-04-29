"""5-dim Judge with cost-aware routing.

Routing rule (cost-aware):
  - low sensitivity AND short trajectory  -> Haiku, single shot
  - medium sensitivity                    -> Sonnet, 3-shot self-consistency (median)
  - high sensitivity OR top-N reused      -> Sonnet, 3-shot + secondary model ensemble

Internal storage is continuous in [-1, 1]. Discretization happens at export only.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from typing import Any

from .shared import pg_pool

JUDGE_VERSION = "judge-v1"

# Cheap routing thresholds.
SHORT_TRAJ_TURNS = 4
ENSEMBLE_REUSE_THRESHOLD = 10


async def _shot(model: str, trajectory: dict[str, Any], card: dict[str, Any]) -> dict[str, float]:
    """Stub: deterministic mock so the pipeline runs without API keys.
    Real impl: anthropic.messages.create with a structured-output prompt."""
    n = len(trajectory.get("trajectory", []))
    return {
        "outcome": min(1.0, 0.4 + 0.05 * n),
        "intent": 0.7,
        "execution": 0.6,
        "orchestration": 0.5,
        "expression": 0.7,
        "confidence": 0.8 if model == "sonnet" else 0.65,
    }


def _median_aggregate(shots: list[dict[str, float]]) -> dict[str, float]:
    keys = ("outcome", "intent", "execution", "orchestration", "expression", "confidence")
    return {k: statistics.median(s[k] for s in shots) for k in keys}


def _variance(shots: list[dict[str, float]]) -> float:
    if len(shots) < 2:
        return 0.0
    dims = ("outcome", "intent", "execution", "orchestration", "expression")
    return statistics.mean(
        statistics.variance([s[d] for s in shots]) for d in dims
    )


def _route(sensitivity: str, traj_turns: int, reuse_count: int) -> tuple[str, int, str | None]:
    if sensitivity == "high" or reuse_count >= ENSEMBLE_REUSE_THRESHOLD:
        return ("sonnet", 3, "haiku")  # 3-shot sonnet + 1 haiku check
    if sensitivity == "low" and traj_turns <= SHORT_TRAJ_TURNS:
        return ("haiku", 1, None)
    return ("sonnet", 3, None)


async def judge(
    experience_id: str,
    trajectory: dict[str, Any],
    card: dict[str, Any],
    sensitivity: str,
) -> dict[str, Any]:
    pool = await pg_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reuse_count FROM experiences WHERE experience_id = $1::uuid",
                experience_id,
            )
        reuse = (row or {"reuse_count": 0})["reuse_count"] or 0
    finally:
        await pool.close()

    primary, shots, ensemble = _route(sensitivity, len(trajectory.get("trajectory", [])), reuse)
    runs = await asyncio.gather(*[_shot(primary, trajectory, card) for _ in range(shots)])
    aggregated = _median_aggregate(runs)
    variance = _variance(runs)

    if ensemble is not None:
        check = await _shot(ensemble, trajectory, card)
        # Reduce confidence if cross-model disagrees materially.
        avg_disagree = statistics.mean(
            abs(aggregated[d] - check[d])
            for d in ("outcome", "intent", "execution", "orchestration", "expression")
        )
        if avg_disagree > 0.4:
            aggregated["confidence"] *= 0.6
            unstable = True
        else:
            unstable = False
    else:
        unstable = variance > 0.05

    return {
        "judge_version": JUDGE_VERSION,
        "judge_model": primary,
        "scores": aggregated,
        "self_consistency_variance": variance,
        "is_unstable": unstable,
        "rationale": json.dumps({"shots": shots, "ensemble": ensemble}),
    }


async def persist(experience_id: str, result: dict[str, Any]) -> None:
    pool = await pg_pool()
    try:
        async with pool.acquire() as conn:
            s = result["scores"]
            await conn.execute(
                """
                INSERT INTO rewards (
                    experience_id, judge_version, judge_model,
                    r_outcome, r_intent, r_execution, r_orchestration, r_expression,
                    confidence, self_consistency_variance, is_unstable, rationale
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                experience_id,
                result["judge_version"],
                result["judge_model"],
                s["outcome"],
                s["intent"],
                s["execution"],
                s["orchestration"],
                s["expression"],
                s["confidence"],
                result["self_consistency_variance"],
                result["is_unstable"],
                result["rationale"],
            )
            # Initial Q = first reward. Real refinement happens in CreditAssigner.
            await conn.execute(
                """
                UPDATE experiences SET
                  q_outcome = $2, q_intent = $3, q_execution = $4,
                  q_orchestration = $5, q_expression = $6,
                  q_update_count = 1
                WHERE experience_id = $1::uuid
                """,
                experience_id,
                s["outcome"],
                s["intent"],
                s["execution"],
                s["orchestration"],
                s["expression"],
            )
    finally:
        await pool.close()

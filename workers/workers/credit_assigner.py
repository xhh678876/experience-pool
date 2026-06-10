"""Delayed credit assignment, one-hop only.

Triggered when a child experience finishes scoring. Walks experience_edges to
find direct parents and updates each parent's Q values via:

    Q_i^(d) <- (1 - alpha * c_t) * Q_i^(d) + alpha * c_t * r_t^(d)

  alpha   : learning rate (0.2)
  c_t     : confidence reported by the judge for the child
  r_t^(d) : the child's reward in dimension d

NEVER recurses to grandparents. This keeps cycles harmless even when UI editing
introduces back-edges, and matches Synergy's experience-replay behavior.
"""

from __future__ import annotations

import json

from .shared import pg_pool

ALPHA = 0.2
DIMS = ("outcome", "intent", "execution", "orchestration", "expression")


async def apply_credit(child_id: str) -> int:
    """Returns the number of parents that received an update."""
    pool = await pg_pool()
    updated = 0
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                child_reward = await conn.fetchrow(
                    """
                    SELECT r_outcome, r_intent, r_execution, r_orchestration, r_expression,
                           confidence
                    FROM rewards
                    WHERE experience_id = $1::uuid
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    child_id,
                )
                if child_reward is None:
                    return 0

                edges = await conn.fetch(
                    """
                    SELECT parent_id FROM experience_edges
                    WHERE child_id = $1::uuid AND credit_applied = FALSE
                    """,
                    child_id,
                )

                conf = float(child_reward["confidence"])
                for edge in edges:
                    parent_id = edge["parent_id"]
                    parent = await conn.fetchrow(
                        """
                        SELECT q_outcome, q_intent, q_execution, q_orchestration,
                               q_expression, q_update_count
                        FROM experiences WHERE experience_id = $1::uuid
                        """,
                        parent_id,
                    )
                    if parent is None:
                        continue

                    eff_alpha = ALPHA * conf
                    new_q = {}
                    deltas = {}
                    for d in DIMS:
                        old = float(parent[f"q_{d}"] or 0.0)
                        r = float(child_reward[f"r_{d}"])
                        new = (1 - eff_alpha) * old + eff_alpha * r
                        new_q[d] = new
                        deltas[d] = new - old

                    await conn.execute(
                        """
                        UPDATE experiences SET
                          q_outcome = $2, q_intent = $3, q_execution = $4,
                          q_orchestration = $5, q_expression = $6,
                          q_update_count = q_update_count + 1
                        WHERE experience_id = $1::uuid
                        """,
                        parent_id,
                        new_q["outcome"],
                        new_q["intent"],
                        new_q["execution"],
                        new_q["orchestration"],
                        new_q["expression"],
                    )
                    await conn.execute(
                        """
                        INSERT INTO q_updates (
                            experience_id, triggered_by_child, alpha, confidence,
                            delta_outcome, delta_intent, delta_execution,
                            delta_orchestration, delta_expression
                        ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        parent_id,
                        child_id,
                        ALPHA,
                        conf,
                        deltas["outcome"],
                        deltas["intent"],
                        deltas["execution"],
                        deltas["orchestration"],
                        deltas["expression"],
                    )
                    await conn.execute(
                        """
                        UPDATE experience_edges SET credit_applied = TRUE
                        WHERE parent_id = $1::uuid AND child_id = $2::uuid
                        """,
                        parent_id,
                        child_id,
                    )
                    updated += 1

                await conn.execute(
                    """
                    INSERT INTO audit_log (actor, actor_kind, action, target_id, payload)
                    VALUES ('credit_assigner', 'system', 'credit_applied', $1::uuid, $2)
                    """,
                    child_id,
                    json.dumps({"parents_updated": updated}),
                )
    finally:
        await pool.close()
    return updated

"""Deduplicator: merge experiences when intent AND script are both similar.

Synergy-style: high cosine on intent alone is not enough. Same intent can have
genuinely different scripts that are both worth keeping.

When a duplicate is found, we keep the row with higher q_scalar and absorb
visit_count + reuse_count from the other. The loser is soft-deleted via
review_status='rejected' with a reason payload.
"""

from __future__ import annotations

from qdrant_client.http import models as qm

from .embedder import _embed
from .shared import INTENT_COLLECTION, SCRIPT_COLLECTION, pg_pool, qdrant

INTENT_THRESHOLD = 0.92
SCRIPT_THRESHOLD = 0.88


async def find_duplicate(experience_id: str, intent_text: str, script_text: str) -> str | None:
    client = await qdrant()
    intent_vec = _embed(intent_text)
    script_vec = _embed(script_text or intent_text)

    # Find candidates by intent first.
    intent_hits = await client.search(
        collection_name=INTENT_COLLECTION,
        query_vector=intent_vec,
        limit=10,
        score_threshold=INTENT_THRESHOLD,
    )
    candidates = [h.id for h in intent_hits if h.id != experience_id]
    if not candidates:
        await client.close()
        return None

    # Confirm with script similarity.
    script_hits = await client.search(
        collection_name=SCRIPT_COLLECTION,
        query_vector=script_vec,
        limit=20,
        score_threshold=SCRIPT_THRESHOLD,
        query_filter=qm.Filter(
            must=[qm.HasIdCondition(has_id=candidates)]
        ),
    )
    await client.close()
    if not script_hits:
        return None
    return script_hits[0].id  # type: ignore[return-value]


async def merge(winner_id: str, loser_id: str) -> None:
    pool = await pg_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                loser = await conn.fetchrow(
                    "SELECT visit_count, reuse_count FROM experiences WHERE experience_id = $1::uuid",
                    loser_id,
                )
                if loser is None:
                    return
                await conn.execute(
                    """
                    UPDATE experiences SET
                      visit_count = visit_count + $2,
                      reuse_count = reuse_count + $3
                    WHERE experience_id = $1::uuid
                    """,
                    winner_id,
                    loser["visit_count"] or 0,
                    loser["reuse_count"] or 0,
                )
                await conn.execute(
                    """
                    UPDATE experiences SET
                      review_status = 'rejected',
                      tags = array_append(tags, 'merged_into:' || $2)
                    WHERE experience_id = $1::uuid
                    """,
                    loser_id,
                    winner_id,
                )
    finally:
        await pool.close()

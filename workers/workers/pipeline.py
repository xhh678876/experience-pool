"""Single-process pipeline runner for M1/M2.

Consumes raw_trajectories. Runs sanitize -> extract -> judge -> embed -> dedup ->
credit. In production these become independent workers; for the smoke test
running them inline is fine.

Run:
    uv run python -m workers.pipeline
"""

from __future__ import annotations

import asyncio
import json

import structlog

from .credit_assigner import apply_credit
from .dedup import find_duplicate, merge
from .embedder import index
from .extractor import extract, persist as persist_card
from .judge import judge, persist as persist_reward
from .shared import consume, load_trajectory, s3_client

log = structlog.get_logger()


async def _sanitize(payload: dict) -> None:
    # M3 will replace this with the three-layer sanitizer. For M1 it's a no-op.
    return None


async def handle(experience_id: str, payload: dict) -> None:
    s3 = s3_client()
    trajectory = load_trajectory(s3, experience_id)

    await _sanitize(payload)

    card = await extract(experience_id)
    await persist_card(experience_id, card)
    if card["_unstable"]:
        log.warning("extraction_unstable", experience_id=experience_id, divergence=card["_divergence"])
        return

    judge_result = await judge(experience_id, trajectory, card, payload["sensitivity"])
    await persist_reward(experience_id, judge_result)

    await index(
        experience_id,
        card,
        meta={
            "task_type": payload["task_type"],
            "acl": "private",
        },
    )

    script_text = " ".join(
        f"{s['step']} {s['why']} {s['how']}" for s in card.get("script_steps", [])
    )
    dup = await find_duplicate(experience_id, card["intent_text"], script_text)
    if dup:
        log.info("duplicate_found", experience_id=experience_id, duplicate=dup)
        await merge(winner_id=dup, loser_id=experience_id)
        return

    parents_updated = await apply_credit(experience_id)
    log.info(
        "experience_ready",
        experience_id=experience_id,
        parents_updated=parents_updated,
    )


async def main() -> None:
    log.info("pipeline_start")
    async for r, msg_id, payload in consume("raw_trajectories", "pipeline", "worker-1"):
        try:
            await handle(payload["experience_id"], payload)
            await r.xack("raw_trajectories", "pipeline", msg_id)
        except Exception as e:  # noqa: BLE001
            log.exception("pipeline_error", error=str(e), payload=json.dumps(payload))


if __name__ == "__main__":
    asyncio.run(main())

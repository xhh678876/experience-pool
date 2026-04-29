"""Extractor: trajectory -> (intent, script, digest).

Double-run for stability:
  1. Run prompt-A and prompt-B in parallel (different framings).
  2. Compare structural fingerprints (step count, capability set, pitfall count).
  3. If diff above threshold -> mark extraction_status='unstable' and route to review.
  4. Otherwise pick the run with higher self-reported confidence.

In M1/M2 the LLM call is stubbed. Replace `_call_llm` with a real Anthropic call
once API keys are wired through env.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog

from .shared import load_trajectory, pg_pool, s3_client

log = structlog.get_logger()

EXTRACTION_DIVERGENCE_THRESHOLD = 0.5
EXTRACTOR_VERSION = "extractor-v1"


async def _call_llm(prompt_id: str, trajectory: dict[str, Any]) -> dict[str, Any]:
    """Stub. In production: anthropic.messages.create(...).
    Returns a structured experience card. For now, a deterministic mock based on
    trajectory length so the rest of the pipeline can be validated end-to-end."""
    steps = trajectory.get("trajectory", [])
    n = len(steps)
    base_intent = "summarize and act on a multi-turn task"
    if prompt_id == "B":
        base_intent = "complete a multi-step task using available tools"
    return {
        "intent_text": base_intent,
        "preconditions": "input is a coherent conversation",
        "script_steps": [
            {"step": f"step {i + 1}", "why": "stub", "how": "stub"} for i in range(min(n, 5))
        ],
        "tool_capabilities": ["text_processing"],
        "key_decisions": [],
        "pitfalls": [],
        "summary": f"trajectory of {n} turns",
        "confidence": 0.85 if prompt_id == "A" else 0.78,
    }


def _fingerprint(card: dict[str, Any]) -> tuple[int, int, int]:
    return (
        len(card.get("script_steps") or []),
        len(card.get("tool_capabilities") or []),
        len(card.get("pitfalls") or []),
    )


def _divergence(a: dict[str, Any], b: dict[str, Any]) -> float:
    fa, fb = _fingerprint(a), _fingerprint(b)
    diff = sum(abs(x - y) for x, y in zip(fa, fb, strict=True))
    return diff / max(sum(fa) + sum(fb), 1)


async def extract(experience_id: str) -> dict[str, Any]:
    s3 = s3_client()
    traj = load_trajectory(s3, experience_id)
    a, b = await asyncio.gather(_call_llm("A", traj), _call_llm("B", traj))

    div = _divergence(a, b)
    if div >= EXTRACTION_DIVERGENCE_THRESHOLD:
        chosen = a if a["confidence"] >= b["confidence"] else b
        chosen["_unstable"] = True
        chosen["_divergence"] = div
        return chosen

    chosen = a if a["confidence"] >= b["confidence"] else b
    chosen["_unstable"] = False
    chosen["_divergence"] = div
    return chosen


async def persist(experience_id: str, card: dict[str, Any]) -> None:
    pool = await pg_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE experiences SET
                  intent_text = $2,
                  preconditions = $3,
                  script_steps = $4::jsonb,
                  tool_capabilities = $5::jsonb,
                  key_decisions = $6::jsonb,
                  pitfalls = $7::jsonb,
                  summary = $8,
                  extraction_status = $9
                WHERE experience_id = $1::uuid
                """,
                experience_id,
                card["intent_text"],
                card["preconditions"],
                json.dumps(card["script_steps"]),
                json.dumps(card["tool_capabilities"]),
                json.dumps(card["key_decisions"]),
                json.dumps(card["pitfalls"]),
                card["summary"],
                "unstable" if card["_unstable"] else "done",
            )
    finally:
        await pool.close()

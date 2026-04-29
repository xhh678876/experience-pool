from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from .config import settings

_client: redis.Redis | None = None

# Stream names (single source of truth).
RAW = "raw_trajectories"
SANITIZED = "sanitized_trajectories"
EXTRACTED = "extracted_experiences"
SCORED = "scored_experiences"
CREDIT = "credit_signals"


async def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def publish(stream: str, payload: dict[str, Any]) -> str:
    r = await get_redis()
    return await r.xadd(stream, {"data": json.dumps(payload, default=str)})

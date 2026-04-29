"""Embedder: index intent + script vectors in Qdrant.

In M1 we reuse the same hash-based stub the gateway uses, so search and indexing
agree. Real impl will call BGE-large-zh / jina-embeddings-v3.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any
from uuid import UUID

from qdrant_client.http import models as qm

from .shared import EMBED_DIM, INTENT_COLLECTION, SCRIPT_COLLECTION, qdrant


def _embed(text: str) -> list[float]:
    if not text:
        text = "<empty>"
    out: list[float] = []
    seed = text.encode("utf-8")
    counter = 0
    while len(out) < EMBED_DIM:
        h = hashlib.sha256(seed + counter.to_bytes(4, "little")).digest()
        for b in h:
            out.append((b - 127.5) / 127.5)
            if len(out) == EMBED_DIM:
                break
        counter += 1
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / norm for v in out]


async def index(experience_id: str, card: dict[str, Any], meta: dict[str, Any]) -> None:
    client = await qdrant()
    intent_vec = _embed(card["intent_text"])
    script_text = " ".join(
        f"{s['step']} {s['why']} {s['how']}" for s in card.get("script_steps", [])
    )
    script_vec = _embed(script_text or card["intent_text"])

    payload = {
        "task_type": meta["task_type"],
        "acl": meta.get("acl", "private"),
        "tags": meta.get("tags", []),
    }
    await client.upsert(
        collection_name=INTENT_COLLECTION,
        points=[qm.PointStruct(id=experience_id, vector=intent_vec, payload=payload)],
    )
    await client.upsert(
        collection_name=SCRIPT_COLLECTION,
        points=[qm.PointStruct(id=experience_id, vector=script_vec, payload=payload)],
    )
    await client.close()

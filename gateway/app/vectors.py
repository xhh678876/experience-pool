from __future__ import annotations

from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from .config import settings

_client: AsyncQdrantClient | None = None


async def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.qdrant_url)
    return _client


async def ensure_collections() -> None:
    client = await get_qdrant()
    existing = {c.name for c in (await client.get_collections()).collections}
    for name in (settings.intent_collection, settings.script_collection):
        if name not in existing:
            await client.create_collection(
                collection_name=name,
                vectors_config=qm.VectorParams(
                    size=settings.embedding_dim, distance=qm.Distance.COSINE
                ),
            )


async def upsert_intent(experience_id: UUID, vector: list[float], payload: dict[str, Any]) -> None:
    client = await get_qdrant()
    await client.upsert(
        collection_name=settings.intent_collection,
        points=[qm.PointStruct(id=str(experience_id), vector=vector, payload=payload)],
    )


async def upsert_script(experience_id: UUID, vector: list[float], payload: dict[str, Any]) -> None:
    client = await get_qdrant()
    await client.upsert(
        collection_name=settings.script_collection,
        points=[qm.PointStruct(id=str(experience_id), vector=vector, payload=payload)],
    )


async def search_intent(
    vector: list[float],
    *,
    limit: int,
    acl_filter: list[str] | None = None,
    task_type: str | None = None,
) -> list[qm.ScoredPoint]:
    client = await get_qdrant()
    must: list[qm.FieldCondition] = []
    if acl_filter:
        must.append(qm.FieldCondition(key="acl", match=qm.MatchAny(any=acl_filter)))
    if task_type:
        must.append(qm.FieldCondition(key="task_type", match=qm.MatchValue(value=task_type)))
    flt = qm.Filter(must=must) if must else None
    return await client.search(
        collection_name=settings.intent_collection,
        query_vector=vector,
        limit=limit,
        query_filter=flt,
        with_payload=True,
    )

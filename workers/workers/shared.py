from __future__ import annotations

import json
import os
from typing import Any

import asyncpg
import boto3
import redis.asyncio as redis
from botocore.client import Config
from qdrant_client import AsyncQdrantClient


PG_DSN = os.getenv("EXP_PG_DSN", "postgresql://exp:exp@localhost:5432/experience")
REDIS_URL = os.getenv("EXP_REDIS_URL", "redis://localhost:6379/0")
QDRANT_URL = os.getenv("EXP_QDRANT_URL", "http://localhost:6333")
S3_ENDPOINT = os.getenv("EXP_S3_ENDPOINT", "http://localhost:9000")
S3_KEY = os.getenv("EXP_S3_KEY", "minioadmin")
S3_SECRET = os.getenv("EXP_S3_SECRET", "minioadmin")
S3_BUCKET = os.getenv("EXP_S3_BUCKET", "trajectories")

INTENT_COLLECTION = os.getenv("EXP_INTENT_COLLECTION", "intent_v1")
SCRIPT_COLLECTION = os.getenv("EXP_SCRIPT_COLLECTION", "script_v1")
EMBED_DIM = int(os.getenv("EXP_EMBED_DIM", "1024"))


async def pg_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)


def redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


async def qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=QDRANT_URL)


def load_trajectory(s3, experience_id: str) -> dict[str, Any]:
    obj = s3.get_object(Bucket=S3_BUCKET, Key=f"raw/{experience_id}.json")
    return json.loads(obj["Body"].read())


async def consume(stream: str, group: str, consumer: str, count: int = 1):
    """Async generator yielding (msg_id, payload). Creates the group lazily."""
    r = redis_client()
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:  # type: ignore[attr-defined]
        if "BUSYGROUP" not in str(e):
            raise
    while True:
        resp = await r.xreadgroup(
            group, consumer, streams={stream: ">"}, count=count, block=5000
        )
        if not resp:
            continue
        for _stream_name, entries in resp:
            for msg_id, fields in entries:
                payload = json.loads(fields["data"])
                yield r, msg_id, payload

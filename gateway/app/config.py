from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pg_dsn: str = os.getenv(
        "EXP_PG_DSN", "postgresql://exp:exp@localhost:5432/experience"
    )
    redis_url: str = os.getenv("EXP_REDIS_URL", "redis://localhost:6379/0")
    qdrant_url: str = os.getenv("EXP_QDRANT_URL", "http://localhost:6333")
    s3_endpoint: str = os.getenv("EXP_S3_ENDPOINT", "http://localhost:9000")
    s3_key: str = os.getenv("EXP_S3_KEY", "minioadmin")
    s3_secret: str = os.getenv("EXP_S3_SECRET", "minioadmin")
    s3_bucket: str = os.getenv("EXP_S3_BUCKET", "trajectories")

    intent_collection: str = os.getenv("EXP_INTENT_COLLECTION", "intent_v1")
    script_collection: str = os.getenv("EXP_SCRIPT_COLLECTION", "script_v1")
    embedding_dim: int = int(os.getenv("EXP_EMBED_DIM", "1024"))

    # Mixed ranking weights. See score_search().
    w_similarity: float = float(os.getenv("EXP_W_SIM", "0.55"))
    w_q: float = float(os.getenv("EXP_W_Q", "0.35"))
    c_exploration: float = float(os.getenv("EXP_C_EXPLORE", "0.10"))


settings = Settings()

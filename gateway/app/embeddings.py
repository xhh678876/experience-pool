"""Embedding stub.

In M1 we deterministically hash text to a vector so the rest of the pipeline
can run end-to-end without an embedding provider. The real embedder lives in
workers/embedder.py and replaces this for production.
"""

from __future__ import annotations

import hashlib
import math

from .config import settings


def embed(text: str) -> list[float]:
    if not text:
        text = "<empty>"
    dim = settings.embedding_dim
    out: list[float] = []
    seed = text.encode("utf-8")
    counter = 0
    while len(out) < dim:
        h = hashlib.sha256(seed + counter.to_bytes(4, "little")).digest()
        for b in h:
            out.append((b - 127.5) / 127.5)
            if len(out) == dim:
                break
        counter += 1
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / norm for v in out]

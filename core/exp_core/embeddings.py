"""Tiny deterministic embedding so the loop runs without an embedding provider.

Hash-based bag-of-character-trigrams projection into a fixed-dim float vector.
Cosine-comparable but obviously not semantically meaningful. Real impl plugs in
BGE-large-zh or similar; this exists so we can demo the closed loop today.
"""

from __future__ import annotations

import array
import hashlib
import math
import struct

DIM = 256


def _hash_to_index(token: str) -> tuple[int, int]:
    h = hashlib.sha256(token.encode("utf-8")).digest()
    idx = struct.unpack("<I", h[:4])[0] % DIM
    sign = 1 if h[4] & 1 else -1
    return idx, sign


def embed(text: str) -> array.array:
    """Returns L2-normalized float32 vector of length DIM."""
    if not text:
        text = "<empty>"
    text = text.lower().strip()
    vec = array.array("f", [0.0] * DIM)
    n = len(text)
    if n < 3:
        text = text + "  "
        n = len(text)
    for i in range(n - 2):
        idx, sign = _hash_to_index(text[i : i + 3])
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    for i in range(DIM):
        vec[i] = vec[i] / norm
    return vec


def cosine(a: array.array, b: array.array) -> float:
    return sum(x * y for x, y in zip(a, b))


def to_blob(v: array.array) -> bytes:
    return v.tobytes()


def from_blob(b: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(b)
    return a

"""Helpers for FTS5 keyword search.

Two utilities:

* ``escape_query`` rewrites a free-text query into a safe FTS5 MATCH expression.
  We OR the tokens together and quote each one to avoid the user accidentally
  triggering the FTS5 query language (NEAR, ^, *, etc.).
* ``rank_to_signal`` converts BM25 ranks to a [0, 1] signal that we blend
  into the vector similarity before z-scoring. Rank-based instead of raw BM25
  so the magnitude is bounded and stable across query types.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9一-鿿]{2,}")


def escape_query(text: str) -> str:
    """Build a safe FTS5 MATCH expression. Returns '' if no tokens."""
    if not text:
        return ""
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return ""
    # Quote each token so FTS5 treats it as a literal, not a column name.
    return " OR ".join(f'"{t}"' for t in tokens)


def rank_to_signal(rank: int, total: int) -> float:
    """Best rank (0) -> 1.0, worst rank -> small positive. Linear decay."""
    if total <= 0:
        return 0.0
    return max(0.0, 1.0 - (rank / total))

#!/usr/bin/env python3
"""Offline RAG retrieval evaluation wrapper.

Examples:
  python3 scripts/rag_eval.py make-silver --out eval/silver.jsonl --limit 200
  python3 scripts/rag_eval.py eval --gold eval/gold.jsonl --viewer user-xhh666 --details
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from exp_core.rag_eval import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

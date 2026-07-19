#!/usr/bin/env python3
"""Run Experience Pool RAG index maintenance outside the query hot path."""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path

from exp_core import rag


def _default_db() -> Path:
    if os.getenv("EXP_DB_PATH"):
        return Path(os.environ["EXP_DB_PATH"])
    root = Path(os.getenv("EXP_ROOT", ".experience-pool"))
    return root / "pool.db"


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=_default_db())
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--rebuild-all",
        action="store_true",
        help="re-split and re-embed every live experience before refreshing metadata",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    rag.ensure_schema(conn)
    started = time.perf_counter()

    pruned_experiences = 0
    pruned_chunks = 0
    while True:
        experience_count, chunk_count = rag.prune_stale_experience_indexes(
            conn, limit=max(1, args.batch_size)
        )
        pruned_experiences += experience_count
        pruned_chunks += chunk_count
        if experience_count == 0:
            break
        print(
            "[rag-maintenance] pruned stale indexes: "
            f"experiences={pruned_experiences} chunks={pruned_chunks}",
            flush=True,
        )

    provenance_total = 0
    while True:
        updated = rag.backfill_experience_provenance(
            conn, limit=max(1, args.batch_size)
        )
        provenance_total += updated
        if updated:
            print(
                f"[rag-maintenance] backfilled session provenance: {provenance_total}",
                flush=True,
            )
        if updated == 0:
            break

    if args.rebuild_all:
        rows = conn.execute(
            """
            SELECT experience_id
            FROM experiences
            WHERE review_status IN ('approved', 'auto_approved', 'edited')
              AND extraction_status = 'done'
              AND COALESCE(revoked, 0) = 0
            ORDER BY created_at
            """
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            rag.rebuild_experience(conn, row["experience_id"])
            if index == 1 or index % 25 == 0 or index == len(rows):
                print(f"[rag-maintenance] rebuilt {index}/{len(rows)} experiences", flush=True)

    while True:
        before = _count(
            conn,
            """
            SELECT COUNT(*)
            FROM experiences e
            LEFT JOIN rag_chunks c ON c.experience_id = e.experience_id
            WHERE c.chunk_id IS NULL
              AND e.review_status IN ('approved', 'auto_approved', 'edited')
              AND e.extraction_status = 'done'
              AND COALESCE(e.revoked, 0) = 0
            """,
        )
        if before == 0:
            break
        rag.backfill_missing_chunks(conn, limit=max(1, args.batch_size))
        after = _count(
            conn,
            """
            SELECT COUNT(*)
            FROM experiences e
            LEFT JOIN rag_chunks c ON c.experience_id = e.experience_id
            WHERE c.chunk_id IS NULL
              AND e.review_status IN ('approved', 'auto_approved', 'edited')
              AND e.extraction_status = 'done'
              AND COALESCE(e.revoked, 0) = 0
            """,
        )
        print(f"[rag-maintenance] missing experiences {before} -> {after}", flush=True)
        if after >= before:
            print("[rag-maintenance] no backfill progress; leaving empty experiences untouched")
            break

    refreshed_total = 0
    while True:
        refreshed = rag.refresh_stale_retrieval_text(
            conn, limit=max(1, args.batch_size)
        )
        refreshed_total += refreshed
        if refreshed:
            print(
                f"[rag-maintenance] refreshed retrieval metadata: {refreshed_total}",
                flush=True,
            )
        if refreshed == 0:
            break

    chunks = _count(conn, "SELECT COUNT(*) FROM rag_chunks")
    vectors = _count(conn, "SELECT COUNT(*) FROM rag_vectors WHERE model = 'trigram-256'")
    stale = _count(
        conn,
        """
        SELECT COUNT(*) FROM rag_chunks
        WHERE search_text IS NULL OR search_text = ''
           OR lexical_terms IS NULL OR lexical_terms = ''
           OR substr(ltrim(lexical_terms), 1, 1) != '{'
        """,
    )
    elapsed = time.perf_counter() - started
    print(
        f"[rag-maintenance] done chunks={chunks} vectors={vectors} stale={stale} "
        f"elapsed={elapsed:.1f}s"
    )
    conn.close()
    return 0 if stale == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

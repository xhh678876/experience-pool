#!/usr/bin/env python3
"""Backfill OPF sanitization on rows the push path deferred.

Architecture: when EXP_DEFER_OPF=1 (or remote OPF was unreachable),
push_lite stores rows with sanitization_status='layer1_only'. This
worker iterates such rows, calls the remote OPF service to redact the
trajectory file in place, and updates the row to 'done' with the
recorded redactions.

Designed to run as a long-lived background process (systemd timer or
a `while true; sleep 30; done` loop). Idempotent — same row never gets
double-processed thanks to the row-level state machine.

Run:
    EXP_OPF_REMOTE_URL=http://<opf-host>:8085 \\
    EXP_DB_PATH=/tmp/exp-mvp/pool.db \\
    EXP_TRAJECTORIES_DIR=/tmp/exp-mvp/trajectories \\
    python3 scripts/exp_opf_worker.py [--limit 50] [--once] [--verbose]

Env:
    EXP_OPF_REMOTE_URL     URL of opf_service (required)
    EXP_OPF_AUTH_TOKEN     optional auth token for OPF service
    EXP_OPF_TIMEOUT_SECONDS per-trajectory call timeout (default 60)
    EXP_DB_PATH            pool.db path (default /tmp/exp-mvp/pool.db)
    EXP_TRAJECTORIES_DIR   trajectories dir (default <db dir>/trajectories)
    EXP_OPF_WORKER_INTERVAL  seconds between polls (default 15)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOG = logging.getLogger("opf_worker")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _db_path() -> Path:
    p = _env("EXP_DB_PATH") or "/tmp/exp-mvp/pool.db"
    return Path(p)


def _traj_dir() -> Path:
    explicit = _env("EXP_TRAJECTORIES_DIR")
    if explicit:
        return Path(explicit)
    return _db_path().parent / "trajectories"


def _redact_via_remote(trajectory: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int], bool]:
    """POST to /redact-trajectory. Returns (cleaned, hits, triggered_high).
    Raises on any HTTP / network error."""
    base = _env("EXP_OPF_REMOTE_URL").rstrip("/")
    if not base:
        raise RuntimeError("EXP_OPF_REMOTE_URL is required")
    timeout = float(_env("EXP_OPF_TIMEOUT_SECONDS") or "60")
    body = json.dumps({"trajectory": trajectory}).encode("utf-8")
    headers = {"content-type": "application/json"}
    token = _env("EXP_OPF_AUTH_TOKEN")
    if token:
        headers["x-opf-token"] = token
    req = urllib.request.Request(f"{base}/redact-trajectory", data=body,
                                  headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return (
        data.get("trajectory", trajectory),
        data.get("hits", {}) or {},
        bool(data.get("triggered_high")),
    )


def _sanitizer_blocks_review(*, acl: str, triggered_high: bool) -> bool:
    return bool(triggered_high and acl != "private")


def _rebuild_rag(conn: sqlite3.Connection, eid: str) -> None:
    try:
        root = Path(__file__).resolve().parents[1]
        core = root / "core"
        if str(core) not in sys.path:
            sys.path.insert(0, str(core))
        from exp_core import rag  # noqa: PLC0415

        rag.ensure_schema(conn)
        rag.rebuild_experience(conn, eid)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("eid=%s failed to rebuild RAG chunks: %s", eid[:8], exc)


def _process_row(conn: sqlite3.Connection, row: sqlite3.Row, *, dry_run: bool) -> str:
    """Process one row. Returns one of: 'done', 'no-trajectory', 'failed', 'skip'.
    Caller commits."""
    eid = row["experience_id"]
    traj_path = row["trajectory_path"]
    if not traj_path:
        # No raw trajectory to scan — just mark layer1_only as final.
        conn.execute(
            "UPDATE experiences SET sanitization_status='done' "
            "WHERE experience_id=? AND sanitization_status='layer1_only'",
            (eid,),
        )
        return "no-trajectory"

    p = Path(traj_path)
    if not p.exists():
        # File gone (revoked elsewhere) — drop sanitization_status to 'done'
        # so we don't keep retrying.
        conn.execute(
            "UPDATE experiences SET sanitization_status='done' "
            "WHERE experience_id=? AND sanitization_status='layer1_only'",
            (eid,),
        )
        return "no-trajectory"

    try:
        sidecar = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("eid=%s failed to read sidecar %s: %s", eid[:8], p, exc)
        return "failed"

    trajectory = sidecar.get("trajectory") or []
    if not trajectory:
        conn.execute(
            "UPDATE experiences SET sanitization_status='done' "
            "WHERE experience_id=? AND sanitization_status='layer1_only'",
            (eid,),
        )
        return "no-trajectory"

    try:
        cleaned, hits, triggered_high = _redact_via_remote(trajectory)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        LOG.warning("eid=%s remote OPF call failed: %s", eid[:8], exc)
        return "failed"

    if dry_run:
        LOG.info("[dry-run] eid=%s would update with hits=%s triggered_high=%s",
                 eid[:8], hits, triggered_high)
        return "done"

    # Re-write trajectory file with cleaned content
    sidecar["trajectory"] = cleaned
    p.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2),
                 encoding="utf-8")

    # Merge OPF hits into existing redactions on the row
    existing = {}
    cur = conn.execute(
        "SELECT structural_metrics FROM experiences WHERE experience_id=?", (eid,)
    ).fetchone()
    # We don't have a clean redactions column; OPF hits become a
    # "post-sanitize" entry in audit_log, plus we simply update status.
    new_status = "human_review" if triggered_high else "done"
    blocks_review = _sanitizer_blocks_review(
        acl=row["acl"] or "private",
        triggered_high=triggered_high,
    )
    new_review = "pending" if blocks_review else "auto_approved"
    conn.execute(
        """UPDATE experiences SET sanitization_status=?, review_status=?
           WHERE experience_id=? AND sanitization_status='layer1_only'""",
        (new_status, new_review, eid),
    )
    conn.execute(
        """INSERT INTO audit_log (actor, actor_kind, action, target_id, payload)
           VALUES (?, 'system', 'opf_backfill', ?, ?)""",
        ("opf_worker", eid,
         json.dumps({
             "hits": hits,
             "triggered_high": triggered_high,
             "review_status": new_review,
             "sanitizer_blocked_review": blocks_review,
             "acl": row["acl"] or "private",
         })),
    )
    if new_review == "auto_approved":
        _rebuild_rag(conn, eid)
    return "done"


def run_once(*, limit: int, dry_run: bool) -> dict[str, int]:
    db = _db_path()
    if not db.exists():
        LOG.error("db not found: %s", db)
        return {"db_missing": 1}
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")

    rows = conn.execute(
        """SELECT experience_id, trajectory_path, acl
           FROM experiences
           WHERE sanitization_status='layer1_only'
             AND COALESCE(revoked, 0) = 0
           ORDER BY created_at ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    counts = {"done": 0, "no-trajectory": 0, "failed": 0}
    for row in rows:
        outcome = _process_row(conn, row, dry_run=dry_run)
        counts[outcome] = counts.get(outcome, 0) + 1
        if not dry_run:
            conn.commit()
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--limit", type=int, default=50,
                   help="rows per poll (default 50)")
    p.add_argument("--once", action="store_true",
                   help="run a single pass and exit")
    p.add_argument("--dry-run", action="store_true",
                   help="don't update db / files; just print")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    interval = float(_env("EXP_OPF_WORKER_INTERVAL") or "15")
    if args.once:
        counts = run_once(limit=args.limit, dry_run=args.dry_run)
        LOG.info("once: %s", counts)
        return 0
    LOG.info("starting OPF worker (interval=%ss, limit=%d, remote=%s)",
             interval, args.limit, _env("EXP_OPF_REMOTE_URL") or "(unset!)")
    while True:
        try:
            counts = run_once(limit=args.limit, dry_run=args.dry_run)
            if any(counts.values()):
                LOG.info("tick: %s", counts)
        except KeyboardInterrupt:
            LOG.info("interrupted")
            return 0
        except Exception as exc:
            LOG.exception("tick error: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())

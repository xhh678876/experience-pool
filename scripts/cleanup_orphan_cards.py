#!/usr/bin/env python3
"""Delete experiences whose trajectory_path is NULL/missing.

These are cards uploaded without the raw trajectory — only the
{query, intent, steps, outcome} summary. They are searchable but not
auditable, can't be re-judged, and aren't useful as SFT material. After
the v1 server enforces EXP_REQUIRE_TRAJECTORY, no new orphans should
appear, but the historical ones need cleaning.

Default mode is dry-run (just prints what would be deleted). Pass
--apply to actually remove the rows + any related entries (FTS,
audit_log refs, vectors, edges, rewards).

Usage:
    python3 scripts/cleanup_orphan_cards.py [--db PATH] [--apply]

Safe to run multiple times. Will not delete revoked rows (those are
already cleaned up by the revoke flow).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def find_orphans(conn: sqlite3.Connection) -> list[dict]:
    """Return rows where trajectory_path is null/empty AND not revoked."""
    cur = conn.execute(
        """
        SELECT e.experience_id, e.task_type, e.acl, e.publish_status,
               e.created_at, a.name AS agent_name, a.owner
        FROM experiences e LEFT JOIN agents a ON a.agent_id = e.agent_id
        WHERE (e.trajectory_path IS NULL OR e.trajectory_path = '')
          AND COALESCE(e.revoked, 0) = 0
        ORDER BY e.created_at DESC
        """
    )
    return [dict(r) for r in cur.fetchall()]


def delete_experience(conn: sqlite3.Connection, eid: str) -> None:
    """Cascade-delete every row keyed on this experience_id.

    SQLite without ON DELETE CASCADE — we do the sweep ourselves so we
    don't leave orphan FTS entries / vectors / edges. Order doesn't
    matter much; we just need to hit every table.
    """
    # Try to clean the FTS5 index. For external-content FTS this is a
    # plain DELETE; for contentless it needs the 'delete' command which
    # requires the original values. Either way, leaving stale FTS rows
    # is harmless (the experience row itself is deleted, so JOINs miss),
    # so we swallow errors here.
    try:
        conn.execute(
            "DELETE FROM experiences_fts WHERE experience_id = ?", (eid,)
        )
    except sqlite3.OperationalError:
        pass
    for table in (
        "experience_edges",
        "experience_skill_uses",
        "cluster_membership",
        "content_fingerprints",
        "rewards",
        "lite_rewards",
        "turn_rewards",
        "q_updates",
        "search_log",
        "vectors",
        "pending_reembed",
        "pending_rejudge",
    ):
        try:
            if table == "experience_edges":
                conn.execute(
                    "DELETE FROM experience_edges WHERE parent_id = ? OR child_id = ?",
                    (eid, eid),
                )
            else:
                conn.execute(f"DELETE FROM {table} WHERE experience_id = ?", (eid,))
        except sqlite3.OperationalError as exc:
            # Table may not exist on older pools — skip.
            if "no such table" in str(exc):
                continue
            raise
    conn.execute("DELETE FROM experiences WHERE experience_id = ?", (eid,))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--db",
        default=os.environ.get("EXP_DB_PATH")
                or str(Path(os.environ.get("EXP_ROOT", "/tmp/exp-mvp")) / "pool.db"),
        help="path to pool.db (default: $EXP_DB_PATH or $EXP_ROOT/pool.db)",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="actually delete (default: dry-run, just print what would go)",
    )
    p.add_argument(
        "--owner", default=None,
        help="only consider rows owned by this email/agent (safety filter)",
    )
    args = p.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = find_orphans(conn)
    if args.owner:
        rows = [r for r in rows if r["owner"] == args.owner or r["agent_name"] == args.owner]

    print(f"orphan cards (no trajectory_path): {len(rows)}")
    print(f"db: {db_path}  apply={args.apply}  owner={args.owner or '(any)'}")
    print()
    for r in rows:
        print(
            f"  {r['experience_id'][:8]}  "
            f"task={r['task_type']:20s}  "
            f"acl={r['acl']:8s}  "
            f"agent={(r['agent_name'] or '?')[:30]:30s}  "
            f"created={r['created_at']}"
        )

    if not args.apply:
        print()
        print(f"DRY-RUN — pass --apply to delete the {len(rows)} row(s) above.")
        return 0

    if not rows:
        return 0

    print()
    print(f"Deleting {len(rows)} row(s) ...")
    for r in rows:
        delete_experience(conn, r["experience_id"])
    conn.commit()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

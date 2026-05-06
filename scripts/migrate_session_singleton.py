#!/usr/bin/env python3
"""一次性 migration:回填 session_id / turn_count,合并同 session 的重复 row。

历史背景:在加 session-singleton 去重之前,SessionEnd hook 在长会话里多次
触发,每次落一个新 row(同 session 不同时间点的快照)。这个脚本扫所有未
revoked / 未 superseded 的 row,把同 (agent_id, session_id) 组里 turn_count
最大的留下,其它标 superseded=1。

幂等;重跑只会扫,不会重复合并(已经 superseded 的不动)。

用法:
  EXP_DB_PATH=/tmp/exp-mvp/pool.db \\
  python3 scripts/migrate_session_singleton.py [--dry-run] [--owner xhh666@sii.edu.cn]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def _backfill_columns(conn: sqlite3.Connection, *, dry_run: bool) -> tuple[int, int]:
    """从 trajectory_path 文件读 meta.session_id + len(trajectory),回填到
    DB 的 session_id / turn_count 列。返回 (filled, missing_file)。"""
    rows = conn.execute(
        """SELECT experience_id, trajectory_path,
                  COALESCE(session_id, '') AS session_id,
                  COALESCE(turn_count, 0) AS turn_count
           FROM experiences
           WHERE COALESCE(revoked, 0) = 0 AND COALESCE(superseded, 0) = 0"""
    ).fetchall()
    filled = 0
    missing = 0
    for r in rows:
        eid = r[0]
        path = r[1]
        sid_db = r[2]
        tc_db = r[3]
        if sid_db and tc_db:
            continue  # 已经填过
        if not path or not Path(path).is_file():
            missing += 1
            continue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            missing += 1
            continue
        traj = data.get("trajectory") if isinstance(data, dict) else data
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        new_sid = (meta.get("session_id") or "").strip()
        new_tc = len(traj) if isinstance(traj, list) else 0
        if not dry_run:
            conn.execute(
                "UPDATE experiences SET session_id = ?, turn_count = ? "
                "WHERE experience_id = ?",
                (new_sid or None, new_tc, eid),
            )
        filled += 1
    if not dry_run:
        conn.commit()
    return filled, missing


def _collapse_duplicates(
    conn: sqlite3.Connection, *, dry_run: bool, owner: str | None
) -> dict[str, int]:
    """同一 (agent_id, session_id) 多行 → 留 turn_count 最高,其它 superseded=1。"""
    where = ["COALESCE(e.revoked, 0) = 0", "COALESCE(e.superseded, 0) = 0",
             "e.session_id IS NOT NULL", "e.session_id != ''"]
    params: list = []
    if owner:
        where.append("(a.owner = ? OR (a.owner IS NULL AND a.name = ?))")
        params.extend([owner, owner])
    sql = (
        "SELECT e.experience_id, e.agent_id, e.session_id, "
        "       COALESCE(e.turn_count, 0) AS turn_count, e.created_at "
        "FROM experiences e JOIN agents a USING(agent_id) "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY e.agent_id, e.session_id, turn_count DESC, e.created_at DESC"
    )
    rows = conn.execute(sql, params).fetchall()

    groups: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for r in rows:
        eid, agent_id, sid, tc, ts = r
        groups[(agent_id, sid)].append((eid, tc, ts))

    superseded = 0
    kept = 0
    singleton_groups = 0
    duplicate_groups = 0
    for (agent_id, sid), members in groups.items():
        if len(members) == 1:
            singleton_groups += 1
            kept += 1
            continue
        duplicate_groups += 1
        # members 已按 turn_count desc 排,第 0 个保留
        winner = members[0]
        kept += 1
        loser_eids = [m[0] for m in members[1:]]
        superseded += len(loser_eids)
        if not dry_run:
            placeholders = ",".join("?" for _ in loser_eids)
            conn.execute(
                f"UPDATE experiences SET superseded = 1 "
                f"WHERE experience_id IN ({placeholders})",
                loser_eids,
            )
            print(
                f"  group ({sid[:32]}…): kept {winner[0][:8]} ({winner[1]} turns), "
                f"superseded {len(loser_eids)} smaller versions"
            )
    if not dry_run:
        conn.commit()
    return {
        "groups_total": len(groups),
        "groups_singleton": singleton_groups,
        "groups_duplicate": duplicate_groups,
        "kept": kept,
        "superseded": superseded,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.environ.get("EXP_DB_PATH", "/tmp/exp-mvp/pool.db"))
    p.add_argument("--owner", help="只对这个 owner 做 collapse(可选)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not Path(args.db).is_file():
        print(f"db 不存在: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    print(f"db: {args.db}")
    print(f"dry_run: {args.dry_run}")
    if args.owner:
        print(f"owner: {args.owner}")
    print()
    print("=== Phase 1: 回填 session_id + turn_count ===")
    filled, missing = _backfill_columns(conn, dry_run=args.dry_run)
    print(f"  filled: {filled}, missing_file: {missing}")
    print()
    print("=== Phase 2: 合并同 (agent_id, session_id) 重复 row ===")
    stats = _collapse_duplicates(conn, dry_run=args.dry_run, owner=args.owner)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

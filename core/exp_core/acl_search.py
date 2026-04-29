"""ACL-aware search and detail-fetch helpers.

Wraps ExperiencePool.search() + a direct DB read to filter rows the viewer
can't see. Lives in its own module so we don't risk a merge conflict with the
sanitizer worker editing pool.py concurrently.
"""

from __future__ import annotations

from typing import Any

from .identity import can_read
from .pool import ExperiencePool


def _viewer_context(pool: ExperiencePool, viewer_name: str) -> tuple[str, str]:
    cur = pool.conn.execute(
        "SELECT agent_id, team FROM agents WHERE name = ?", (viewer_name,)
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown viewer agent: {viewer_name}")
    return row["agent_id"], row["team"]


def _row_acl(pool: ExperiencePool, experience_id: str) -> tuple[str, str]:
    cur = pool.conn.execute(
        "SELECT agent_id, acl FROM experiences WHERE experience_id = ?",
        (experience_id,),
    )
    row = cur.fetchone()
    if row is None:
        return ("", "private")
    return (row["agent_id"], row["acl"])


def search_with_acl(
    pool: ExperiencePool,
    viewer_name: str,
    query: str,
    *,
    top_k: int = 5,
    task_type: str | None = None,
    sort: str = "score",
    exploration: float | None = None,
) -> list[dict[str, Any]]:
    """Run search, then drop rows the viewer can't see, then trim to top_k."""
    viewer_id, viewer_team = _viewer_context(pool, viewer_name)
    # Over-fetch so post-filter doesn't starve the result.
    raw = pool.search(
        viewer_name, query, top_k=top_k * 4,
        task_type=task_type, sort=sort, exploration=exploration,
    )
    visible = []
    for hit in raw:
        owner_id, acl = _row_acl(pool, hit["experience_id"])
        if can_read(viewer_id, viewer_team, owner_id, acl):
            visible.append(hit)
        if len(visible) >= top_k:
            break
    return visible


def get_with_acl(
    pool: ExperiencePool, viewer_name: str, experience_id: str
) -> dict[str, Any] | None:
    viewer_id, viewer_team = _viewer_context(pool, viewer_name)
    owner_id, acl = _row_acl(pool, experience_id)
    if not owner_id:
        return None
    if not can_read(viewer_id, viewer_team, owner_id, acl):
        # Audit denied access so we can spot probing.
        pool.conn.execute(
            "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
            (viewer_name, "agent", "read_denied", experience_id,
             '{"reason":"acl"}'),
        )
        pool.conn.commit()
        return None
    cur = pool.conn.execute(
        "SELECT * FROM experiences WHERE experience_id = ?", (experience_id,)
    )
    row = cur.fetchone()
    return dict(row) if row else None

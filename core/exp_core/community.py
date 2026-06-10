"""Community pool helpers — owner / quota / publish / unpublish.

The two-tier model:

  Personal pool (default)
    - acl='private', publish_status='private'
    - readable by any agent registered to the same `owner`
    - all uploads land here first
    - lenient sanitize (regex replacement, not blocking)

  Community pool (opt-in, contribution-required)
    - acl='public', publish_status='published'
    - readable by anyone whose owner has publish_count >= COMMUNITY_THRESHOLD
    - publishing requires strict_public_check to pass (no file://, no
      local resources, no localhost URLs, no UUIDs that map back to
      private rows)
    - publish_count never decreases — unpublish puts the row back to
      private but the contribution stays counted.

Why owner (not agent or team)?
  A single user runs multiple agents (claude-code at home, cursor at
  work, hermes on a side machine). We want all those agents to share
  one personal pool and one community quota. `agents.team` is already
  used for org-level grouping, so we add a separate `owner` column to
  group multiple agents into one personal account.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import dataclass
from typing import Any


# ----- Tunables --------------------------------------------------------------

COMMUNITY_THRESHOLD = 3   # publish_count >= this unlocks community pool reads
DEFAULT_OWNER_FALLBACK = "name"  # used when registering an agent without --owner


# ----- Owner resolution ------------------------------------------------------


def get_owner(conn: sqlite3.Connection, agent_name: str) -> str | None:
    """Return the owner handle for an agent, or None if the agent doesn't
    exist."""
    cur = conn.execute(
        "SELECT owner, name FROM agents WHERE name = ?", (agent_name,)
    )
    row = cur.fetchone()
    if row is None:
        return None
    # Lazy back-fill: if owner was never set, return the agent name as
    # a stand-in (matches the migration in quality.ensure_quality_columns).
    if row["owner"] is None or row["owner"] == "":
        return row["name"]
    return row["owner"]


def list_agents_for_owner(conn: sqlite3.Connection, owner: str) -> list[str]:
    """Return all agent names belonging to one owner."""
    cur = conn.execute(
        "SELECT name FROM agents WHERE owner = ? OR (owner IS NULL AND name = ?)",
        (owner, owner),
    )
    return [r["name"] for r in cur.fetchall()]


# ----- Quota -----------------------------------------------------------------


@dataclass(frozen=True)
class Quota:
    owner: str
    publish_count: int
    threshold: int
    community_unlocked: bool
    last_publish_at: str | None
    hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "publish_count": self.publish_count,
            "threshold": self.threshold,
            "community_unlocked": self.community_unlocked,
            "last_publish_at": self.last_publish_at,
            "hint": self.hint,
        }


def _ensure_quota_row(conn: sqlite3.Connection, owner: str) -> None:
    # First check whether the row already exists using the shared read
    # connection. If it does (the common case after first use), we skip
    # the write entirely — eliminating the most frequent source of
    # "database is locked" under multi-worker load.
    row = conn.execute(
        "SELECT 1 FROM owner_quotas WHERE owner = ?", (owner,)
    ).fetchone()
    if row is not None:
        return
    # Row missing — open a short-lived write connection so the implicit
    # transaction doesn't outlive this call.
    from . import api_keys as _ak  # late import to avoid cycles
    try:
        with _ak._write_conn(conn) as w:
            w.execute(
                "INSERT OR IGNORE INTO owner_quotas (owner) VALUES (?)", (owner,)
            )
    except sqlite3.OperationalError:
        # Best-effort: another worker likely inserted the row concurrently.
        # Subsequent SELECTs will find it.
        pass


def get_quota(conn: sqlite3.Connection, owner: str,
              threshold: int = COMMUNITY_THRESHOLD) -> Quota:
    _ensure_quota_row(conn, owner)
    cur = conn.execute(
        "SELECT publish_count, last_publish_at FROM owner_quotas WHERE owner = ?",
        (owner,),
    )
    row = cur.fetchone()
    publish_count = row["publish_count"] if row else 0
    last_publish_at = row["last_publish_at"] if row else None
    unlocked = publish_count >= threshold
    if unlocked:
        hint = "community pool unlocked"
    else:
        remaining = threshold - publish_count
        hint = f"publish {remaining} more experience(s) to unlock the community pool"
    return Quota(
        owner=owner,
        publish_count=publish_count,
        threshold=threshold,
        community_unlocked=unlocked,
        last_publish_at=last_publish_at,
        hint=hint,
    )


def is_community_unlocked(conn: sqlite3.Connection, owner: str,
                          threshold: int = COMMUNITY_THRESHOLD) -> bool:
    return get_quota(conn, owner, threshold).community_unlocked


def bump_publish_count(conn: sqlite3.Connection, owner: str) -> int:
    _ensure_quota_row(conn, owner)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE owner_quotas
        SET publish_count = publish_count + 1,
            last_publish_at = ?,
            updated_at = ?
        WHERE owner = ?
        """,
        (now, now, owner),
    )
    cur = conn.execute(
        "SELECT publish_count FROM owner_quotas WHERE owner = ?", (owner,)
    )
    return cur.fetchone()["publish_count"]


def bump_unpublish_count(conn: sqlite3.Connection, owner: str) -> None:
    _ensure_quota_row(conn, owner)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE owner_quotas
        SET unpublished_count = unpublished_count + 1,
            updated_at = ?
        WHERE owner = ?
        """,
        (now, owner),
    )


# ----- Publish flow ----------------------------------------------------------


@dataclass
class PublishResult:
    ok: bool
    status: str               # 'published' | 'already_public' | 'blocked' | 'not_found' | 'forbidden'
    experience_id: str
    quota_after: Quota | None
    strict_summary: dict[str, int]
    blocking_hits: list[dict[str, Any]]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "status": self.status,
            "experience_id": self.experience_id,
            "strict_summary": self.strict_summary,
        }
        if self.quota_after:
            out["quota"] = self.quota_after.to_dict()
        if self.blocking_hits:
            out["blocking_hits"] = self.blocking_hits[:50]
        if self.error:
            out["error"] = self.error
        return out


def _load_experience_for_publish(
    conn: sqlite3.Connection, experience_id: str
) -> dict[str, Any] | None:
    cur = conn.execute(
        """
        SELECT e.experience_id, e.agent_id, e.acl, e.publish_status,
               e.trajectory_path, e.query, e.intent_text, e.script_steps,
               e.outcome, e.summary,
               COALESCE(e.revoked, 0) AS revoked,
               a.name AS agent_name, a.owner AS owner
        FROM experiences e JOIN agents a USING(agent_id)
        WHERE e.experience_id = ?
        """,
        (experience_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _read_trajectory_sidecar(path: str | None) -> dict[str, Any] | None:
    """Load the JSON sidecar (trajectory + system + tools + meta) saved
    next to the DB row at upload time. Returns None when the file is
    missing or unreadable; the publish flow tolerates that since the
    card fields are scanned independently."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def publish_experience(
    conn: sqlite3.Connection,
    *,
    experience_id: str,
    actor_name: str,
) -> PublishResult:
    """Run strict-public sanitize on an experience and, if clean, set
    acl='public' + publish_status='published' + bump owner.publish_count.

    Lazy import of sanitize_public to avoid a circular import with the
    larger sanitize.py."""
    from . import sanitize_public

    row = _load_experience_for_publish(conn, experience_id)
    if row is None:
        return PublishResult(
            ok=False, status="not_found", experience_id=experience_id,
            quota_after=None, strict_summary={}, blocking_hits=[],
            error=f"experience not found: {experience_id}",
        )
    if row["agent_name"] != actor_name:
        return PublishResult(
            ok=False, status="forbidden", experience_id=experience_id,
            quota_after=None, strict_summary={}, blocking_hits=[],
            error=f"actor {actor_name!r} does not own experience",
        )
    if row["revoked"]:
        return PublishResult(
            ok=False, status="forbidden", experience_id=experience_id,
            quota_after=None, strict_summary={}, blocking_hits=[],
            error="cannot publish a revoked experience",
        )
    if row["publish_status"] == "published":
        owner = row["owner"] or row["agent_name"]
        return PublishResult(
            ok=True, status="already_public", experience_id=experience_id,
            quota_after=get_quota(conn, owner), strict_summary={},
            blocking_hits=[],
        )

    sidecar = _read_trajectory_sidecar(row["trajectory_path"])
    card = {
        "query": row["query"] or "",
        "intent": row["intent_text"] or "",
        "outcome": row["outcome"] or "",
        "summary": row["summary"] or "",
        "steps": json.loads(row["script_steps"] or "[]"),
    }
    check = sanitize_public.strict_public_check(
        card=card,
        trajectory=(sidecar or {}).get("trajectory"),
        system=(sidecar or {}).get("system"),
        tools=(sidecar or {}).get("tools"),
        meta=(sidecar or {}).get("meta"),
    )

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()

    if not check.ok:
        # Reject — write strict_redactions for audit but DO NOT publish.
        conn.execute(
            """
            UPDATE experiences
            SET publish_status = 'rejected',
                strict_redactions = ?
            WHERE experience_id = ?
            """,
            (
                json.dumps({
                    "summary": check.summary,
                    "blocked_at": now_iso,
                }),
                experience_id,
            ),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                actor_name, "agent", "publish_rejected", experience_id,
                json.dumps(check.reject_payload(), ensure_ascii=False),
            ),
        )
        conn.commit()
        owner = row["owner"] or row["agent_name"]
        return PublishResult(
            ok=False,
            status="blocked",
            experience_id=experience_id,
            quota_after=get_quota(conn, owner),
            strict_summary=check.summary,
            blocking_hits=check.reject_payload()["hits"],
            error="strict_public_sanitize blocked publication",
        )

    # Pass — flip ACL + bump quota in one transaction.
    owner = row["owner"] or row["agent_name"]
    conn.execute(
        """
        UPDATE experiences
        SET acl = 'public',
            publish_status = 'published',
            published_at = ?
        WHERE experience_id = ?
        """,
        (now_iso, experience_id),
    )
    new_count = bump_publish_count(conn, owner)
    conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            actor_name, "agent", "publish", experience_id,
            json.dumps({
                "owner": owner,
                "publish_count_after": new_count,
                "ts": now_iso,
            }),
        ),
    )
    conn.commit()

    return PublishResult(
        ok=True,
        status="published",
        experience_id=experience_id,
        quota_after=get_quota(conn, owner),
        strict_summary={},
        blocking_hits=[],
    )


def unpublish_experience(
    conn: sqlite3.Connection,
    *,
    experience_id: str,
    actor_name: str,
) -> PublishResult:
    """Undo a publish: drop acl back to 'private', set publish_status to
    'private'. The owner's publish_count is NOT decremented (no take-backs
    on contribution credit) but unpublished_count is bumped for audit."""
    row = _load_experience_for_publish(conn, experience_id)
    if row is None:
        return PublishResult(
            ok=False, status="not_found", experience_id=experience_id,
            quota_after=None, strict_summary={}, blocking_hits=[],
            error=f"experience not found: {experience_id}",
        )
    if row["agent_name"] != actor_name:
        return PublishResult(
            ok=False, status="forbidden", experience_id=experience_id,
            quota_after=None, strict_summary={}, blocking_hits=[],
            error=f"actor {actor_name!r} does not own experience",
        )
    if row["publish_status"] != "published":
        owner = row["owner"] or row["agent_name"]
        return PublishResult(
            ok=True, status="already_private", experience_id=experience_id,
            quota_after=get_quota(conn, owner), strict_summary={}, blocking_hits=[],
        )

    owner = row["owner"] or row["agent_name"]
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE experiences
        SET acl = 'private',
            publish_status = 'private',
            published_at = NULL
        WHERE experience_id = ?
        """,
        (experience_id,),
    )
    bump_unpublish_count(conn, owner)
    conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            actor_name, "agent", "unpublish", experience_id,
            json.dumps({"owner": owner, "ts": now_iso}),
        ),
    )
    conn.commit()
    return PublishResult(
        ok=True,
        status="unpublished",
        experience_id=experience_id,
        quota_after=get_quota(conn, owner),
        strict_summary={},
        blocking_hits=[],
    )

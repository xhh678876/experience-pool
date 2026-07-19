"""Project pool membership and sharing helpers.

Projects are an ACL scope, not a copy of anyone's private data. Experiences
stay owned by their original owner; a project grant says "this owner lets this
project read my personal pool".
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any


INVITE_PREFIX = "exproj_"
INVITE_TTL_SECONDS = 60 * 60 * 24 * 7


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    owner_user_id TEXT,
    created_by_owner TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, user_id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS project_invites (
    invite_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    token_hash TEXT NOT NULL UNIQUE,
    created_by_user_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    revoked_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(created_by_user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS project_owner_grants (
    project_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    granted_by_user_id TEXT NOT NULL,
    include_high_sensitivity INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT,
    PRIMARY KEY (project_id, owner),
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(granted_by_user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_members_user
    ON project_members(user_id, status);
CREATE INDEX IF NOT EXISTS idx_project_invites_email
    ON project_invites(email, revoked_at, accepted_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_project_grants_project
    ON project_owner_grants(project_id, revoked_at);
"""


@dataclass(frozen=True)
class ProjectRef:
    project_id: str
    slug: str
    name: str


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60] or "project"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unique_slug(conn: sqlite3.Connection, desired: str) -> str:
    base = slugify(desired)
    slug = base
    suffix = 2
    while conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def user_owner(conn: sqlite3.Connection, user_id: str) -> str:
    row = conn.execute(
        """
        SELECT u.email, u.default_agent_name, a.owner
        FROM users u
        LEFT JOIN agents a ON a.name = u.default_agent_name
        WHERE u.user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown user")
    return row["owner"] or row["email"] or row["default_agent_name"]


def agent_owner(conn: sqlite3.Connection, agent_name: str) -> str:
    row = conn.execute(
        "SELECT owner, name FROM agents WHERE name = ?",
        (agent_name,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown agent: {agent_name}")
    return row["owner"] or row["name"]


def get_project(conn: sqlite3.Connection, ref: str) -> ProjectRef | None:
    row = conn.execute(
        """
        SELECT project_id, slug, name
        FROM projects
        WHERE archived_at IS NULL AND (project_id = ? OR slug = ?)
        """,
        (ref, ref),
    ).fetchone()
    if row is None:
        return None
    return ProjectRef(row["project_id"], row["slug"], row["name"])


def create_project(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    name: str,
    slug: str | None = None,
) -> dict[str, Any]:
    owner = user_owner(conn, user_id)
    project_id = str(uuid.uuid4())
    final_slug = _unique_slug(conn, slug or name)
    conn.execute(
        """
        INSERT INTO projects (project_id, slug, name, owner_user_id, created_by_owner)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, final_slug, name.strip(), user_id, owner),
    )
    conn.execute(
        """
        INSERT INTO project_members (project_id, user_id, role, status)
        VALUES (?, ?, 'owner', 'active')
        """,
        (project_id, user_id),
    )
    conn.execute(
        """
        INSERT INTO project_owner_grants
          (project_id, owner, granted_by_user_id, include_high_sensitivity)
        VALUES (?, ?, ?, 0)
        """,
        (project_id, owner, user_id),
    )
    conn.commit()
    return get_project_details(conn, user_id=user_id, project_ref=project_id)


def list_projects_for_user(conn: sqlite3.Connection, *, user_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.project_id, p.slug, p.name, p.created_by_owner, p.created_at,
               m.role,
               COUNT(DISTINCT g.owner) AS shared_owners,
               COUNT(DISTINCT m2.user_id) AS member_count
        FROM project_members m
        JOIN projects p ON p.project_id = m.project_id
        LEFT JOIN project_owner_grants g
               ON g.project_id = p.project_id AND g.revoked_at IS NULL
        LEFT JOIN project_members m2
               ON m2.project_id = p.project_id AND m2.status = 'active'
        WHERE m.user_id = ?
          AND m.status = 'active'
          AND p.archived_at IS NULL
        GROUP BY p.project_id, p.slug, p.name, p.created_by_owner, p.created_at, m.role
        ORDER BY p.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_projects_for_owner(conn: sqlite3.Connection, *, owner: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.project_id, p.slug, p.name, p.created_by_owner, p.created_at,
               MAX(COALESCE(g.include_high_sensitivity, 0)) AS include_high_sensitivity,
               CASE WHEN MAX(CASE WHEN m.user_id IS NOT NULL THEN 1 ELSE 0 END) = 1
                    THEN 'member' ELSE 'grant' END AS relation
        FROM projects p
        LEFT JOIN users u ON u.email = ?
        LEFT JOIN project_members m
               ON m.project_id = p.project_id
              AND m.user_id = u.user_id
              AND m.status = 'active'
        LEFT JOIN project_owner_grants g
               ON g.project_id = p.project_id
              AND g.owner = ?
              AND g.revoked_at IS NULL
        WHERE p.archived_at IS NULL
          AND (m.user_id IS NOT NULL OR g.owner IS NOT NULL)
        GROUP BY p.project_id, p.slug, p.name, p.created_by_owner, p.created_at
        ORDER BY p.created_at DESC
        """,
        (owner, owner),
    ).fetchall()
    return [dict(r) for r in rows]


def require_member(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    project_ref: str,
    roles: set[str] | None = None,
) -> ProjectRef:
    project = get_project(conn, project_ref)
    if project is None:
        raise ValueError("project not found")
    row = conn.execute(
        """
        SELECT role FROM project_members
        WHERE project_id = ? AND user_id = ? AND status = 'active'
        """,
        (project.project_id, user_id),
    ).fetchone()
    if row is None:
        raise PermissionError("not a project member")
    if roles is not None and row["role"] not in roles:
        raise PermissionError("insufficient project role")
    return project


def create_invite(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    project_ref: str,
    email: str,
    role: str = "member",
    ttl_seconds: int = INVITE_TTL_SECONDS,
) -> dict[str, Any]:
    project = require_member(
        conn, user_id=user_id, project_ref=project_ref, roles={"owner", "admin"}
    )
    email = (email or "").strip().lower()
    if "@" not in email:
        raise ValueError("invalid invite email")
    role = role if role in {"member", "admin"} else "member"
    raw = INVITE_PREFIX + secrets.token_urlsafe(24)
    invite_id = str(uuid.uuid4())
    expires = (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=ttl_seconds)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO project_invites
          (invite_id, project_id, email, role, token_hash, created_by_user_id, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (invite_id, project.project_id, email, role, _hash_token(raw), user_id, expires),
    )
    conn.commit()
    return {
        "invite_id": invite_id,
        "project_id": project.project_id,
        "project_slug": project.slug,
        "email": email,
        "role": role,
        "expires_at": expires,
        "token": raw,
    }


def accept_invite(conn: sqlite3.Connection, *, user_id: str, email: str, token: str) -> dict[str, Any]:
    token_hash = _hash_token((token or "").strip())
    row = conn.execute(
        """
        SELECT i.invite_id, i.project_id, i.email, i.role, i.expires_at,
               p.slug, p.name
        FROM project_invites i
        JOIN projects p ON p.project_id = i.project_id
        WHERE i.token_hash = ?
          AND i.accepted_at IS NULL
          AND i.revoked_at IS NULL
          AND datetime(i.expires_at) > datetime('now')
          AND p.archived_at IS NULL
        """,
        (token_hash,),
    ).fetchone()
    if row is None:
        raise ValueError("invite not found or expired")
    if row["email"].lower() != (email or "").strip().lower():
        raise PermissionError("invite email does not match current user")
    conn.execute(
        """
        INSERT INTO project_members (project_id, user_id, role, status)
        VALUES (?, ?, ?, 'active')
        ON CONFLICT(project_id, user_id) DO UPDATE SET
          role = excluded.role,
          status = 'active',
          joined_at = datetime('now')
        """,
        (row["project_id"], user_id, row["role"]),
    )
    conn.execute(
        "UPDATE project_invites SET accepted_at = ? WHERE invite_id = ?",
        (_now(), row["invite_id"]),
    )
    conn.commit()
    return {
        "ok": True,
        "project_id": row["project_id"],
        "project_slug": row["slug"],
        "project_name": row["name"],
        "role": row["role"],
    }


def grant_owner(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    project_ref: str,
    owner: str,
    include_high_sensitivity: bool = False,
) -> dict[str, Any]:
    project = require_member(conn, user_id=user_id, project_ref=project_ref)
    current_owner = user_owner(conn, user_id)
    if owner != current_owner:
        raise PermissionError("can only grant your own personal pool")
    conn.execute(
        """
        INSERT INTO project_owner_grants
          (project_id, owner, granted_by_user_id, include_high_sensitivity, revoked_at)
        VALUES (?, ?, ?, ?, NULL)
        ON CONFLICT(project_id, owner) DO UPDATE SET
          include_high_sensitivity = excluded.include_high_sensitivity,
          granted_by_user_id = excluded.granted_by_user_id,
          revoked_at = NULL
        """,
        (project.project_id, owner, user_id, 1 if include_high_sensitivity else 0),
    )
    conn.commit()
    return {"ok": True, "project_id": project.project_id, "owner": owner}


def revoke_owner_grant(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    project_ref: str,
    owner: str,
) -> dict[str, Any]:
    project = require_member(conn, user_id=user_id, project_ref=project_ref)
    current_owner = user_owner(conn, user_id)
    row = conn.execute(
        """
        SELECT role FROM project_members
        WHERE project_id = ? AND user_id = ? AND status = 'active'
        """,
        (project.project_id, user_id),
    ).fetchone()
    can_admin = row is not None and row["role"] in {"owner", "admin"}
    if owner != current_owner and not can_admin:
        raise PermissionError("can only revoke your own grant")
    conn.execute(
        """
        UPDATE project_owner_grants
        SET revoked_at = ?
        WHERE project_id = ? AND owner = ? AND revoked_at IS NULL
        """,
        (_now(), project.project_id, owner),
    )
    conn.commit()
    return {"ok": True, "project_id": project.project_id, "owner": owner}


def visible_project_owners_for_user(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    project_ref: str,
) -> dict[str, bool]:
    project = require_member(conn, user_id=user_id, project_ref=project_ref)
    return _granted_owner_map(conn, project.project_id)


def visible_project_owners_for_agent(
    conn: sqlite3.Connection,
    *,
    agent_name: str,
    project_ref: str,
) -> dict[str, bool]:
    owner = agent_owner(conn, agent_name)
    project = get_project(conn, project_ref)
    if project is None:
        raise ValueError("project not found")
    grants = _granted_owner_map(conn, project.project_id)
    member = conn.execute(
        """
        SELECT 1
        FROM project_members m
        JOIN users u ON u.user_id = m.user_id
        WHERE m.project_id = ?
          AND m.status = 'active'
          AND u.email = ?
        """,
        (project.project_id, owner),
    ).fetchone()
    if member is None and owner not in grants:
        # Agents can query a project when their owner is either a member or an
        # explicitly granted owner. This prevents a leaked slug from exposing
        # the project pool to unrelated agents.
        raise PermissionError("agent owner is not part of this project")
    return grants


def _granted_owner_map(conn: sqlite3.Connection, project_id: str) -> dict[str, bool]:
    rows = conn.execute(
        """
        SELECT owner, include_high_sensitivity
        FROM project_owner_grants
        WHERE project_id = ? AND revoked_at IS NULL
        """,
        (project_id,),
    ).fetchall()
    return {r["owner"]: bool(r["include_high_sensitivity"]) for r in rows}


def get_project_details(conn: sqlite3.Connection, *, user_id: str, project_ref: str) -> dict[str, Any]:
    project = require_member(conn, user_id=user_id, project_ref=project_ref)
    members = conn.execute(
        """
        SELECT m.user_id, u.email, u.display_name, m.role, m.status, m.joined_at
        FROM project_members m
        JOIN users u ON u.user_id = m.user_id
        WHERE m.project_id = ? AND m.status = 'active'
        ORDER BY m.joined_at ASC
        """,
        (project.project_id,),
    ).fetchall()
    grants = conn.execute(
        """
        SELECT owner, include_high_sensitivity, created_at, revoked_at
        FROM project_owner_grants
        WHERE project_id = ? AND revoked_at IS NULL
        ORDER BY created_at ASC
        """,
        (project.project_id,),
    ).fetchall()
    invites = conn.execute(
        """
        SELECT invite_id, email, role, created_at, expires_at, accepted_at, revoked_at
        FROM project_invites
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (project.project_id,),
    ).fetchall()
    return {
        "project_id": project.project_id,
        "slug": project.slug,
        "name": project.name,
        "members": [dict(r) for r in members],
        "grants": [dict(r) for r in grants],
        "invites": [dict(r) for r in invites],
    }

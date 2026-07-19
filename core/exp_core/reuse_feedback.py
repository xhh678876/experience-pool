"""Recall reuse events and feedback-driven Q updates.

The RAG endpoint records which chunks were offered to an agent. A later
feedback call can mark those chunks helpful or harmful; the owning experience
then receives a small EMA-style Q update using the same five dimensions as the
main credit assignment path.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections import defaultdict
from typing import Any


ALPHA = 0.2
DIMS = ("outcome", "intent", "execution", "orchestration", "expression")


SCHEMA = """
CREATE TABLE IF NOT EXISTS reuse_events (
    event_id TEXT PRIMARY KEY,
    viewer_agent_id TEXT NOT NULL,
    viewer_name TEXT NOT NULL,
    query_text TEXT NOT NULL,
    scope TEXT NOT NULL,
    task_type TEXT,
    project_ref TEXT,
    top_k INTEGER NOT NULL,
    final_status TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    feedback_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reuse_events_viewer ON reuse_events(viewer_agent_id, created_at);

CREATE TABLE IF NOT EXISTS reuse_items (
    event_id TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL DEFAULT '',
    rank INTEGER NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    similarity REAL NOT NULL DEFAULT 0,
    source TEXT,
    chunk_type TEXT,
    was_injected INTEGER NOT NULL DEFAULT 1,
    was_used_by_agent INTEGER,
    reward REAL,
    confidence REAL,
    feedback_source TEXT,
    feedback_reason TEXT,
    feedback_at TEXT,
    PRIMARY KEY(event_id, experience_id, chunk_id),
    FOREIGN KEY(event_id) REFERENCES reuse_events(event_id),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id)
);
CREATE INDEX IF NOT EXISTS idx_reuse_items_exp ON reuse_items(experience_id);
CREATE INDEX IF NOT EXISTS idx_reuse_items_event ON reuse_items(event_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def record_reuse_event(
    conn: sqlite3.Connection,
    *,
    viewer_agent_id: str,
    viewer_name: str,
    query: str,
    chunks: list[dict[str, Any]],
    scope: str,
    top_k: int,
    task_type: str | None = None,
    project_ref: str | None = None,
) -> str | None:
    """Persist a RAG recall event and the exact chunks injected."""

    if not chunks:
        return None
    ensure_schema(conn)
    event_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO reuse_events (
            event_id, viewer_agent_id, viewer_name, query_text, scope,
            task_type, project_ref, top_k
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, viewer_agent_id, viewer_name, query, scope, task_type, project_ref, top_k),
    )
    rows = []
    for rank, item in enumerate(chunks, start=1):
        rows.append(
            (
                event_id,
                item["experience_id"],
                item.get("chunk_id") or "",
                rank,
                float(item.get("score") or 0.0),
                float(item.get("similarity") or 0.0),
                item.get("source"),
                item.get("chunk_type"),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO reuse_items (
            event_id, experience_id, chunk_id, rank, score, similarity, source, chunk_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return event_id


def apply_feedback(
    conn: sqlite3.Connection,
    *,
    viewer_name: str,
    event_id: str,
    items: list[dict[str, Any]] | None = None,
    reward: float | None = None,
    confidence: float = 0.35,
    used: bool = True,
    reason: str = "",
    feedback_source: str = "agent",
    final_status: str = "unknown",
) -> dict[str, Any]:
    """Apply feedback for a recall event and update Q on affected experiences."""

    ensure_schema(conn)
    viewer = conn.execute(
        "SELECT agent_id, name FROM agents WHERE name = ?",
        (viewer_name,),
    ).fetchone()
    if viewer is None:
        raise ValueError(f"unknown agent: {viewer_name}")

    event = conn.execute(
        "SELECT event_id, viewer_agent_id FROM reuse_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if event is None:
        raise ValueError(f"reuse event not found: {event_id}")
    if event["viewer_agent_id"] != viewer["agent_id"]:
        raise PermissionError("reuse event belongs to a different viewer")

    normalized_items = _expand_feedback_items(
        conn,
        event_id=event_id,
        items=items or [],
        reward=reward,
        confidence=confidence,
        used=used,
        reason=reason,
    )
    if not normalized_items:
        raise ValueError("feedback items must be non-empty or provide a top-level reward")

    feedback_source = (feedback_source or "agent")[:64]
    final_status = (final_status or "unknown")[:64]
    pending_items = []
    skipped = 0
    seen_keys: set[tuple[str, str]] = set()
    for item in normalized_items:
        r = _clamp(float(item["reward"]), -1.0, 1.0)
        conf = _clamp(float(item.get("confidence", confidence)), 0.0, 1.0)
        exp_id = item["experience_id"]
        chunk_id = item.get("chunk_id") or ""
        key = (exp_id, chunk_id)
        if key in seen_keys:
            skipped += 1
            continue
        seen_keys.add(key)
        used_bool = bool(item.get("used", used))
        pending_items.append(
            (
                1 if used_bool else 0,
                r,
                conf,
                feedback_source,
                str(item.get("reason") or reason or "")[:500],
                event_id,
                exp_id,
                chunk_id,
                r,
                conf,
                used_bool,
            )
        )

    claimed = 0
    per_exp: dict[str, list[tuple[float, float]]] = defaultdict(list)
    per_exp_used: dict[str, bool] = defaultdict(bool)
    with conn:
        for row in pending_items:
            update_args = row[:8]
            exp_id = str(row[6])
            reward_value = float(row[8])
            confidence_value = float(row[9])
            used_bool = bool(row[10])
            cursor = conn.execute(
                """
                UPDATE reuse_items
                SET was_used_by_agent = ?,
                    reward = ?,
                    confidence = ?,
                    feedback_source = ?,
                    feedback_reason = ?,
                    feedback_at = datetime('now')
                WHERE event_id = ? AND experience_id = ? AND chunk_id = ?
                  AND feedback_at IS NULL
                """,
                update_args,
            )
            if cursor.rowcount != 1:
                skipped += 1
                continue
            claimed += 1
            per_exp[exp_id].append((reward_value, confidence_value))
            per_exp_used[exp_id] = per_exp_used[exp_id] or used_bool
        if claimed:
            conn.execute(
                """
                UPDATE reuse_events
                SET final_status = ?, feedback_at = datetime('now')
                WHERE event_id = ?
                """,
                (final_status, event_id),
            )
        updates = []
        for exp_id, values in per_exp.items():
            weighted_reward = _weighted_reward(values)
            avg_conf = sum(conf for _, conf in values) / len(values)
            update = _apply_q_update(
                conn,
                experience_id=exp_id,
                event_id=event_id,
                reward=weighted_reward,
                confidence=avg_conf,
                count_reuse=per_exp_used[exp_id],
            )
            if update is not None:
                updates.append(update)

    return {
        "event_id": event_id,
        "items_updated": claimed,
        "items_skipped": skipped,
        "experiences_updated": len(updates),
        "final_status": final_status,
        "updates": updates,
    }


def _expand_feedback_items(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    items: list[dict[str, Any]],
    reward: float | None,
    confidence: float,
    used: bool,
    reason: str,
) -> list[dict[str, Any]]:
    if items:
        expanded = []
        for item in items:
            rows = _matching_items(conn, event_id, item)
            if not rows:
                raise ValueError("feedback item does not match any chunk in the reuse event")
            item_reward = item.get("reward", reward)
            if item_reward is None:
                raise ValueError("feedback item reward is required")
            for row in rows:
                expanded.append(
                    {
                        "experience_id": row["experience_id"],
                        "chunk_id": row["chunk_id"],
                        "reward": item_reward,
                        "confidence": item.get("confidence", confidence),
                        "used": item.get("used", used),
                        "reason": item.get("reason", reason),
                    }
                )
        return expanded

    if reward is None:
        return []
    rows = conn.execute(
        """
        SELECT experience_id, chunk_id
        FROM reuse_items
        WHERE event_id = ?
        ORDER BY rank ASC
        """,
        (event_id,),
    ).fetchall()
    return [
        {
            "experience_id": row["experience_id"],
            "chunk_id": row["chunk_id"],
            "reward": reward,
            "confidence": confidence,
            "used": used,
            "reason": reason,
        }
        for row in rows
    ]


def _matching_items(
    conn: sqlite3.Connection,
    event_id: str,
    item: dict[str, Any],
) -> list[sqlite3.Row]:
    exp_id = item.get("experience_id")
    chunk_id = item.get("chunk_id")
    if chunk_id:
        params: list[Any] = [event_id, chunk_id]
        sql = "SELECT experience_id, chunk_id FROM reuse_items WHERE event_id = ? AND chunk_id = ?"
        if exp_id:
            sql += " AND experience_id = ?"
            params.append(exp_id)
        return conn.execute(sql, params).fetchall()
    if exp_id:
        return conn.execute(
            """
            SELECT experience_id, chunk_id
            FROM reuse_items
            WHERE event_id = ? AND experience_id = ?
            ORDER BY rank ASC
            """,
            (event_id, exp_id),
        ).fetchall()
    return []


def _apply_q_update(
    conn: sqlite3.Connection,
    *,
    experience_id: str,
    event_id: str,
    reward: float,
    confidence: float,
    count_reuse: bool = True,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT
            q_outcome,
            q_intent,
            q_execution,
            q_orchestration,
            q_expression,
            q_update_count,
            trajectory_score
        FROM experiences
        WHERE experience_id = ?
        """,
        (experience_id,),
    ).fetchone()
    if row is None:
        return None

    conf = _clamp(confidence, 0.0, 1.0)
    eff = ALPHA * conf
    reward_dims = _reward_dimensions(reward)
    learned_q = (
        0.30 * float(row["q_outcome"] or 0.0)
        + 0.20 * float(row["q_intent"] or 0.0)
        + 0.20 * float(row["q_execution"] or 0.0)
        + 0.15 * float(row["q_orchestration"] or 0.0)
        + 0.15 * float(row["q_expression"] or 0.0)
    )
    has_online_q = bool(row["q_update_count"] or abs(learned_q) > 1e-9)
    baseline = _clamp(float(row["trajectory_score"] or 0.0), -1.0, 1.0)
    new_q: dict[str, float] = {}
    deltas: dict[str, float] = {}
    for dim in DIMS:
        old = float(row[f"q_{dim}"] or 0.0) if has_online_q else baseline
        new = (1 - eff) * old + eff * reward_dims[dim]
        new_q[dim] = new
        deltas[dim] = new - old

    conn.execute(
        """
        UPDATE experiences
        SET q_outcome = ?,
            q_intent = ?,
            q_execution = ?,
            q_orchestration = ?,
            q_expression = ?,
            q_update_count = q_update_count + 1,
            reuse_count = reuse_count + ?
        WHERE experience_id = ?
        """,
        (
            new_q["outcome"],
            new_q["intent"],
            new_q["execution"],
            new_q["orchestration"],
            new_q["expression"],
            1 if count_reuse else 0,
            experience_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO q_updates (
            update_id, experience_id, triggered_by_child, alpha, confidence,
            delta_outcome, delta_intent, delta_execution,
            delta_orchestration, delta_expression
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            experience_id,
            f"reuse:{event_id}",
            ALPHA,
            conf,
            deltas["outcome"],
            deltas["intent"],
            deltas["execution"],
            deltas["orchestration"],
            deltas["expression"],
        ),
    )
    return {
        "experience_id": experience_id,
        "reward": reward,
        "confidence": conf,
        "delta": deltas,
        "q": new_q,
    }


def _reward_dimensions(reward: float) -> dict[str, float]:
    r = _clamp(reward, -1.0, 1.0)
    return {
        "outcome": r,
        "intent": r,
        "execution": r,
        "orchestration": _clamp(r * 0.6, -1.0, 1.0),
        "expression": _clamp(r * 0.4, -1.0, 1.0),
    }


def _weighted_reward(values: list[tuple[float, float]]) -> float:
    total_conf = sum(conf for _, conf in values)
    if total_conf <= 0:
        return sum(r for r, _ in values) / len(values)
    return sum(r * conf for r, conf in values) / total_conf


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))

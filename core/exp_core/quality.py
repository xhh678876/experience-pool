"""Quality / fingerprint / metrics layer (borrowed concepts from
modelscope/ultron Trajectory Hub).

Three responsibilities:

1. Content fingerprint    — SHA-256(role + content) prefix per trajectory →
                            idempotent push, returns existing eid on dup.
2. Structural metrics     — pure-Python deterministic scoring of a trajectory
                            (tool_error_rate, self_correction_count,
                            avg_response_length, tool_diversity, step_count).
                            Doesn't need an LLM; gives long-task a fair shake.
3. Quality eligibility    — recompute experiences.{trajectory_score,
                            is_memory_eligible, is_sft_eligible} after every
                            turn_rewards write.

Schema migrations (additive ALTERs) are also kept here so server.py can call
ensure_quality_columns(conn) at boot.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# Schema migrations — additive ALTER TABLE for columns that didn't exist in
# the original schema.py. Idempotent; safe to call on every boot.
# ---------------------------------------------------------------------------

_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column_name, full DDL fragment)
    ("experiences", "trajectory_score",   "REAL"),
    ("experiences", "is_memory_eligible", "INTEGER NOT NULL DEFAULT 0"),
    ("experiences", "is_sft_eligible",    "INTEGER NOT NULL DEFAULT 0"),
    ("experiences", "structural_metrics", "TEXT"),
    ("experiences", "content_fingerprint","TEXT"),
    # Revoked rows are excluded from search/clusters but kept in DB so
    # audit_log can reference them. trajectory file is hard-deleted.
    ("experiences", "revoked",            "INTEGER NOT NULL DEFAULT 0"),
    ("experiences", "revoked_at",         "TEXT"),
    ("experiences", "revoke_reason",      "TEXT"),
    # Personal-pool / community-pool two-tier ACL.
    # publish_status:
    #   'private'  — default; only the owner's agents can read
    #   'pending'  — strict-sanitize in flight (rare; transient)
    #   'published'— acl is also bumped to 'public'; visible in community pool
    #   'rejected' — strict-sanitize blocked publication; reason in strict_redactions
    ("experiences", "publish_status",     "TEXT NOT NULL DEFAULT 'private'"),
    ("experiences", "published_at",       "TEXT"),
    ("experiences", "strict_redactions",  "TEXT"),  # JSON of category→count or hit details
    # Owner = stable handle that groups multiple agents into one personal
    # pool. Existing rows back-fill owner = agents.name (1:1 isolation).
    ("agents",      "owner",              "TEXT"),
    # Session-singleton dedup:同一 (agent_id, session_id) 上传多次时,
    # 服务端原地 UPDATE 同一行,不再为每次快照新建 row。
    #   session_id     — adapter 从 session 文件名提的 stable id;空值表示
    #                    生成 trace 没有 session 概念(generic JSON / push-file)
    #   turn_count     — trajectory 长度,UI 列表显示「N turns」用
    #   superseded     — 被同 session 的更新版本顶替时标 1,等同软撤回:
    #                    搜索/列表都过滤掉,UI 不显示
    ("experiences", "session_id",         "TEXT"),
    ("experiences", "source_agent_type",  "TEXT"),
    ("experiences", "parent_session_id",  "TEXT"),
    ("experiences", "segment_id",         "TEXT"),
    ("experiences", "source_byte_start",  "INTEGER"),
    ("experiences", "source_byte_end",    "INTEGER"),
    ("experiences", "task_status",        "TEXT"),
    ("experiences", "turn_count",         "INTEGER NOT NULL DEFAULT 0"),
    ("experiences", "superseded",         "INTEGER NOT NULL DEFAULT 0"),
]


def ensure_quality_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for table, col, decl in _ADDITIVE_COLUMNS:
        existing = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
        if col not in existing:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # race / already exists
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_score ON experiences(trajectory_score)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_sft ON experiences(is_sft_eligible)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_fp ON experiences(content_fingerprint)"
    )
    # Session-singleton lookup:WHERE agent_id=? AND session_id=? AND superseded=0 AND revoked=0
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_session "
        "ON experiences(agent_id, session_id) "
        "WHERE session_id IS NOT NULL AND superseded=0 AND revoked=0"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_parent_session "
        "ON experiences(agent_id, parent_session_id, source_byte_start) "
        "WHERE parent_session_id IS NOT NULL AND superseded=0 AND revoked=0"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_publish ON experiences(publish_status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner)"
    )

    # Owner quotas table — one row per owner, tracks publish_count etc.
    # Created here (not in schema.py) so old DBs migrate forward lazily.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS owner_quotas (
            owner          TEXT PRIMARY KEY,
            publish_count  INTEGER NOT NULL DEFAULT 0,
            unpublished_count INTEGER NOT NULL DEFAULT 0,
            last_publish_at TEXT,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Back-fill: any agent without an owner inherits its name as a default
    # owner string, preserving existing isolation. New registrations can
    # set a real owner explicitly.
    cur.execute("UPDATE agents SET owner = name WHERE owner IS NULL OR owner = ''")
    conn.commit()


# ---------------------------------------------------------------------------
# Content fingerprint (Ultron schema)
# ---------------------------------------------------------------------------

def _coerce_text(content: Any) -> str:
    """Normalize trajectory content blocks into a deterministic string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                if b.get("type") == "text" and "text" in b:
                    parts.append(str(b["text"]))
                elif b.get("type") == "tool_use":
                    parts.append(json.dumps(
                        {"tool": b.get("name", ""), "input": b.get("input", {})},
                        ensure_ascii=False, sort_keys=True,
                    ))
                elif b.get("type") == "tool_result":
                    rc = b.get("content", "")
                    if not isinstance(rc, str):
                        rc = json.dumps(rc, ensure_ascii=False, sort_keys=True)
                    parts.append(rc)
                else:
                    parts.append(json.dumps(b, ensure_ascii=False, sort_keys=True))
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def compute_fingerprint(trajectory: list[dict[str, Any]] | None) -> str:
    """SHA-256 over (role, content) pairs. First 16 hex chars is enough to
    distinguish hundreds of millions of segments."""
    if not trajectory:
        return ""
    h = hashlib.sha256()
    for msg in trajectory:
        if not isinstance(msg, dict):
            continue
        h.update((msg.get("role") or "").encode("utf-8"))
        h.update(b"\x00")
        h.update(_coerce_text(msg.get("content", "")).encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()[:16]


def find_existing_by_fingerprint(
    conn: sqlite3.Connection, *, fingerprint: str, agent_id: str,
) -> str | None:
    if not fingerprint:
        return None
    row = conn.execute(
        """SELECT experience_id FROM content_fingerprints
           WHERE fingerprint=? AND agent_id=? LIMIT 1""",
        (fingerprint, agent_id),
    ).fetchone()
    return row[0] if row else None


def record_fingerprint(
    conn: sqlite3.Connection, *, fingerprint: str, experience_id: str, agent_id: str,
) -> None:
    if not fingerprint:
        return
    try:
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO content_fingerprints
                   (fingerprint, agent_id, experience_id) VALUES (?, ?, ?)""",
                (fingerprint, agent_id, experience_id),
            )
            conn.execute(
                "UPDATE experiences SET content_fingerprint=? WHERE experience_id=?",
                (fingerprint, experience_id),
            )
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------------
# Structural trajectory metrics (deterministic, LLM-free)
# ---------------------------------------------------------------------------

_SELF_CORRECTION_HINTS = (
    "let me try", "let's try a different", "try a different approach",
    "instead", "actually,", "actually i", "wait,", "sorry, that",
    "另一种", "换个方式", "再试", "重新", "我刚才搞错", "我搞错了",
    "let me retry", "retry with", "fix that", "correct that",
)


def compute_structural_metrics(trajectory: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Return a JSON-serializable dict of structural metrics.

    Cheap, LLM-free, deterministic. Useful especially for long sessions where
    LLM rewards tend to under-score because the per-turn signal is sparse.
    """
    if not trajectory:
        return {
            "turn_count": 0, "user_turn_count": 0, "assistant_turn_count": 0,
            "tool_call_count": 0, "tool_error_count": 0, "tool_error_rate": 0.0,
            "unique_tools": 0, "tool_diversity": 0.0,
            "self_correction_count": 0, "self_correction_rate": 0.0,
            "avg_assistant_chars": 0.0, "max_assistant_chars": 0,
            "completion_signal": False,
            "structural_score": 0.0,
        }

    user_n = 0
    asst_n = 0
    tool_call_n = 0
    tool_error_n = 0
    tools_seen: set[str] = set()
    self_correction_n = 0
    asst_chars: list[int] = []
    saw_completion = False

    for t in trajectory:
        if not isinstance(t, dict):
            continue
        role = t.get("role")
        content = t.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    text_parts.append(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    tool_call_n += 1
                    if b.get("name"):
                        tools_seen.add(b["name"])
                elif b.get("type") == "tool_result":
                    if b.get("is_error"):
                        tool_error_n += 1
            content_text = "\n".join(text_parts)
        elif isinstance(content, str):
            content_text = content
        else:
            content_text = ""

        # OpenAI-style tool_calls field
        for tc in t.get("tool_calls") or []:
            if isinstance(tc, dict):
                tool_call_n += 1
                fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name") or tc.get("name")
                if name:
                    tools_seen.add(name)
        # Uploader-shape tool_result_for marks an explicit tool result turn
        if t.get("tool_result_for") and "error" in (str(content_text)[:200].lower()):
            tool_error_n += 1

        if role == "user":
            user_n += 1
        elif role == "assistant":
            asst_n += 1
            asst_chars.append(len(content_text))
            lowered = content_text.lower()[:600]
            if any(h in lowered for h in _SELF_CORRECTION_HINTS):
                self_correction_n += 1
            if any(s in lowered for s in (
                "done", "完成", "搞定", "解决", "成功", "fixed",
                "all set", "tests pass", "everything works",
            )):
                saw_completion = True

    avg_chars = sum(asst_chars) / len(asst_chars) if asst_chars else 0.0
    max_chars = max(asst_chars) if asst_chars else 0
    tool_err_rate = (tool_error_n / tool_call_n) if tool_call_n else 0.0
    tool_div = (len(tools_seen) / tool_call_n) if tool_call_n else 0.0
    sc_rate = (self_correction_n / max(1, asst_n))

    # Aggregate "structural_score" in [-1, 1]:
    # +completion signal, -high error rate, +tool diversity, -no progress.
    score = 0.0
    if saw_completion:
        score += 0.4
    score -= min(0.5, tool_err_rate * 0.7)
    score += min(0.3, tool_div * 0.5)
    score -= min(0.3, sc_rate * 0.6)
    if asst_n == 0:
        score -= 0.5
    score = max(-1.0, min(1.0, round(score, 3)))

    return {
        "turn_count": user_n + asst_n,
        "user_turn_count": user_n,
        "assistant_turn_count": asst_n,
        "tool_call_count": tool_call_n,
        "tool_error_count": tool_error_n,
        "tool_error_rate": round(tool_err_rate, 3),
        "unique_tools": len(tools_seen),
        "tool_diversity": round(tool_div, 3),
        "self_correction_count": self_correction_n,
        "self_correction_rate": round(sc_rate, 3),
        "avg_assistant_chars": round(avg_chars, 1),
        "max_assistant_chars": max_chars,
        "completion_signal": saw_completion,
        "structural_score": score,
    }


def attach_structural_metrics(
    conn: sqlite3.Connection, *, experience_id: str,
    trajectory: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    metrics = compute_structural_metrics(trajectory)
    try:
        with conn:
            conn.execute(
                "UPDATE experiences SET structural_metrics=? WHERE experience_id=?",
                (json.dumps(metrics, ensure_ascii=False), experience_id),
            )
    except sqlite3.Error:
        pass
    return metrics


# ---------------------------------------------------------------------------
# Trajectory score + eligibility recompute
# ---------------------------------------------------------------------------

# Re-tuned weights (May 2026): debug/exploration mid-sessions were getting
# hammered on `execution` because LLM sees tool_use errors mid-trace and
# judges -1 even when the eventual outcome is correct. Move 0.05 from
# execution → outcome, since "did it ultimately work" matters more than
# "was the path clean".
_DIM_WEIGHTS = {
    "outcome": 0.40, "intent": 0.20, "execution": 0.15,
    "orchestration": 0.10, "expression": 0.15,
}

# Default thresholds — overridable via env in callers.
DEFAULT_MEMORY_THRESHOLD = 0.0   # be permissive; covers low-signal greetings
DEFAULT_SFT_THRESHOLD = 0.4      # strict — only "this trace is positive overall"


def recompute_quality(
    conn: sqlite3.Connection, *, experience_id: str,
    memory_threshold: float = DEFAULT_MEMORY_THRESHOLD,
    sft_threshold: float = DEFAULT_SFT_THRESHOLD,
) -> dict[str, Any]:
    """Aggregate turn_rewards (best judge_model wins by recency) + structural
    metrics → trajectory_score in [-1, 1] → memory/SFT eligibility booleans.
    """
    rows = conn.execute(
        """SELECT r_outcome, r_intent, r_execution, r_orchestration,
                  r_expression, confidence, judge_model, annotated_at
           FROM turn_rewards
           WHERE experience_id = ?
           ORDER BY annotated_at DESC""",
        (experience_id,),
    ).fetchall()
    # Use the most recent judge_model only (avoid double-counting models).
    if rows:
        latest_model = rows[0][6]
        rows = [r for r in rows if r[6] == latest_model]

    score: float = 0.0
    n = 0
    if rows:
        dim_means: dict[str, float] = {}
        for i, dim in enumerate(("outcome", "intent", "execution",
                                 "orchestration", "expression")):
            dim_means[dim] = sum(r[i] for r in rows) / len(rows)
        score = sum(dim_means[d] * w for d, w in _DIM_WEIGHTS.items())
        n = len(rows)

    # Blend in structural score (50/50) so long sessions get fair credit
    # even when LLM-judged rewards are sparse.
    struct_row = conn.execute(
        "SELECT structural_metrics FROM experiences WHERE experience_id=?",
        (experience_id,),
    ).fetchone()
    if struct_row and struct_row[0]:
        try:
            struct = json.loads(struct_row[0])
            ss = float(struct.get("structural_score", 0.0))
            if n > 0:
                score = 0.6 * score + 0.4 * ss
            else:
                score = ss
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    score = max(-1.0, min(1.0, round(score, 3)))
    is_memory = 1 if score >= memory_threshold else 0
    is_sft = 1 if score >= sft_threshold else 0

    try:
        with conn:
            conn.execute(
                """UPDATE experiences
                   SET trajectory_score=?, is_memory_eligible=?, is_sft_eligible=?
                   WHERE experience_id=?""",
                (score, is_memory, is_sft, experience_id),
            )
    except sqlite3.Error:
        pass

    return {
        "experience_id": experience_id,
        "trajectory_score": score,
        "is_memory_eligible": bool(is_memory),
        "is_sft_eligible": bool(is_sft),
        "n_turn_rewards": n,
    }

"""Server-side automatic labeling using OpenAI-compatible LLM endpoint.

Triggered as a FastAPI BackgroundTask after `/v1/lite/push`. For each new
experience this module will:

1. (intent) Re-summarize intent_text if it looks like a raw user-turn echo.
2. (rewards) Run synergy 5-dim per-turn annotation, write turn_rewards rows.
3. (usage) Record every LLM call into the llm_usage table for accounting.

Backend: any /v1/chat/completions endpoint. Configured via env:
    EXP_AUTO_LABEL_BASE_URL   (e.g. https://token-plan-cn.xiaomimimo.com/v1)
    EXP_AUTO_LABEL_API_KEY    (provider api key)
    EXP_AUTO_LABEL_MODEL      (e.g. mimo-v2.5-pro)
    EXP_AUTO_LABEL_ENABLED    "1" to enable; default "0"
    EXP_AUTO_LABEL_MAX_TURNS  per session (default 5)

Pricing for usage accounting (cost_usd column) is configured via:
    EXP_AUTO_LABEL_PRICE_INPUT_PER_MTOK     default 0
    EXP_AUTO_LABEL_PRICE_OUTPUT_PER_MTOK    default 0
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _enabled() -> bool:
    return os.environ.get("EXP_AUTO_LABEL_ENABLED", "0").lower() in {"1", "true", "yes"}


def _config() -> dict[str, str]:
    return {
        "base_url": os.environ.get("EXP_AUTO_LABEL_BASE_URL", "").rstrip("/"),
        "api_key": os.environ.get("EXP_AUTO_LABEL_API_KEY", ""),
        "model": os.environ.get("EXP_AUTO_LABEL_MODEL", ""),
    }


# ---------------------------------------------------------------------------
# Schema migration: llm_usage table for accounting
# ---------------------------------------------------------------------------

USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    usage_id          TEXT PRIMARY KEY,
    experience_id     TEXT,
    kind              TEXT NOT NULL,        -- 'intent' | 'reward' | 'other'
    backend           TEXT NOT NULL,        -- 'openai-compat' | ...
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0.0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    ok                INTEGER NOT NULL DEFAULT 1,
    error             TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usage_kind ON llm_usage(kind);
CREATE INDEX IF NOT EXISTS idx_usage_exp ON llm_usage(experience_id);
CREATE INDEX IF NOT EXISTS idx_usage_created ON llm_usage(created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(USAGE_SCHEMA)


# ---------------------------------------------------------------------------
# Prompts (synergy schema, byte-faithful to client annotator)
# ---------------------------------------------------------------------------

INTENT_SYSTEM = (
    "Output ONE short action-oriented task label (verb + object). "
    "Match the user's language. Max 80 chars. Output ONLY the label, "
    "no quotes, no period, no preamble. "
    "If transcript is just a greeting with no task, output: (no task)"
)

REWARD_SYSTEM = (
    "You are a conversation evaluator. Score 5 quality dimensions as discrete "
    "values, assess your confidence, and explain your reasoning. Output ONLY "
    "valid JSON.\n\n"
    "<input_format>\n"
    "<user> — user request being evaluated.\n"
    "<assistant> — the assistant response (text + [Tool: name] calls).\n"
    "<subsequent> — next several user/assistant turns; this is your delayed "
    "feedback signal.\n"
    "</input_format>\n\n"
    "<scoring>\n"
    "Each dim ∈ {-1, 0, 1}:\n"
    "  outcome: task done correctly?\n"
    "  intent: agent understood real intent?\n"
    "  execution: efficient path?\n"
    "  orchestration: tools well-coordinated?\n"
    "  expression: response well-delivered?\n"
    "confidence ∈ [0,1]; reason: one sentence.\n"
    "</scoring>\n\n"
    "<format>\n"
    "Output ONLY a single JSON object:\n"
    "{\"outcome\":0,\"intent\":0,\"execution\":0,\"orchestration\":0,"
    "\"expression\":0,\"confidence\":0.5,\"reason\":\"...\"}\n"
    "</format>"
)


# ---------------------------------------------------------------------------
# OpenAI-compat HTTP client + usage logging
# ---------------------------------------------------------------------------

def _price() -> tuple[float, float]:
    in_p = float(os.environ.get("EXP_AUTO_LABEL_PRICE_INPUT_PER_MTOK", "0") or 0)
    out_p = float(os.environ.get("EXP_AUTO_LABEL_PRICE_OUTPUT_PER_MTOK", "0") or 0)
    return in_p, out_p


def _log_usage(conn: sqlite3.Connection, *, experience_id: str | None, kind: str,
               backend: str, model: str, prompt: int, completion: int,
               latency_ms: int, ok: bool, error: str | None) -> None:
    in_p, out_p = _price()
    cost = (prompt / 1_000_000.0) * in_p + (completion / 1_000_000.0) * out_p
    try:
        with conn:
            conn.execute(
                """INSERT INTO llm_usage
                   (usage_id, experience_id, kind, backend, model,
                    prompt_tokens, completion_tokens, total_tokens, cost_usd,
                    latency_ms, ok, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), experience_id, kind, backend, model,
                 prompt, completion, prompt + completion, cost,
                 latency_ms, 1 if ok else 0, error),
            )
    except sqlite3.Error as e:
        LOG.warning("usage log insert failed: %s", e)


def _chat_completion(system: str, user: str, *, max_tokens: int = 400,
                     timeout: int = 60, json_mode: bool = False
                     ) -> tuple[str, dict[str, int]]:
    """Returns (content_text, usage_dict). Raises on transport / non-200."""
    cfg = _config()
    if not cfg["base_url"] or not cfg["api_key"] or not cfg["model"]:
        raise RuntimeError("auto-label backend not configured (need EXP_AUTO_LABEL_* env)")
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    choices = data.get("choices") or []
    content = (choices[0].get("message", {}).get("content", "") if choices else "")
    usage = data.get("usage") or {}
    return content, {
        "prompt": int(usage.get("prompt_tokens", 0)),
        "completion": int(usage.get("completion_tokens", 0)),
    }


# ---------------------------------------------------------------------------
# Trajectory utilities (pure data — no extra dep)
# ---------------------------------------------------------------------------

def _read_trajectory(traj_path: str | None) -> list[dict[str, Any]]:
    if not traj_path:
        return []
    try:
        from pathlib import Path
        p = Path(traj_path)
        if not p.exists():
            return []
        d = json.loads(p.read_text(encoding="utf-8"))
        traj = d.get("trajectory") or d.get("messages") or []
        return traj if isinstance(traj, list) else []
    except Exception:
        return []


def _format_for_intent(turns: list[dict[str, Any]], max_chars: int = 4000) -> str:
    user_turns = [t for t in turns if t.get("role") == "user"]
    asst_turns = [t for t in turns if t.get("role") == "assistant"]
    picks: list[tuple[str, str]] = []
    if user_turns:
        picks.append(("user", str(user_turns[0].get("content", ""))))
    if asst_turns:
        picks.append(("assistant", str(asst_turns[0].get("content", ""))))
    if len(asst_turns) > 1 and asst_turns[-1] is not asst_turns[0]:
        picks.append(("assistant", str(asst_turns[-1].get("content", ""))))
    chunks: list[str] = []
    budget_per = max(200, max_chars // max(1, len(picks)))
    for role, content in picks:
        chunks.append(f"[{role}] {content[:budget_per]}")
    return "TRANSCRIPT:\n" + "\n".join(chunks) + "\nLABEL:"


def _format_assistant(turn: dict[str, Any]) -> str:
    parts: list[str] = []
    c = turn.get("content", "")
    if isinstance(c, str) and c.strip():
        parts.append(c)
    for tc in turn.get("tool_calls") or []:
        if isinstance(tc, dict):
            name = tc.get("name", "?")
            inp = tc.get("input", {})
            if isinstance(inp, dict):
                preview = json.dumps(inp, ensure_ascii=False)[:600]
            else:
                preview = str(inp)[:600]
            parts.append(f"[Tool: {name}] {preview}")
    return "\n".join(parts).strip()


def _format_subsequent(turns: list[dict[str, Any]], k: int = 4) -> str:
    out = []
    count = 0
    for t in turns:
        if count >= k:
            break
        role = t.get("role", "")
        if role == "tool":
            continue
        c = t.get("content", "")
        if isinstance(c, list):
            c = "\n".join(b.get("text", "") for b in c
                          if isinstance(b, dict) and b.get("type") == "text")
        if not isinstance(c, str):
            c = json.dumps(c, ensure_ascii=False)
        c = c[:600]
        if role == "user":
            out.append(f"User: {c}")
        elif role == "assistant":
            out.append(f"Assistant: {_format_assistant(t)[:600]}")
        else:
            continue
        count += 1
    return "\n\n".join(out)


def _find_pairs(trajectory: list[dict[str, Any]]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    pending: int | None = None
    for i, t in enumerate(trajectory):
        role = t.get("role")
        content = t.get("content") or ""
        if role == "user" and isinstance(content, str) and content.strip():
            pending = i
        elif role == "assistant" and pending is not None:
            pairs.append((pending, i))
            pending = None
    return pairs


def _pick_evenly(pairs: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    if len(pairs) <= n:
        return pairs
    if n <= 1:
        return [pairs[0]]
    step = (len(pairs) - 1) / (n - 1)
    idxs = sorted({0, len(pairs) - 1, *(round(i * step) for i in range(n))})
    return [pairs[i] for i in idxs[:n]]


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None


def _validate_reward(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for dim in ("outcome", "intent", "execution", "orchestration", "expression"):
        try:
            v = int(d.get(dim, 0))
        except (TypeError, ValueError):
            v = 0
        out[dim] = max(-1, min(1, v))
    try:
        c = float(d.get("confidence", 0.5))
    except (TypeError, ValueError):
        c = 0.5
    out["confidence"] = max(0.0, min(1.0, c))
    out["reason"] = str(d.get("reason", ""))[:500]
    return out


# ---------------------------------------------------------------------------
# Public driver — called from server.py background task
# ---------------------------------------------------------------------------

_HEURISTIC_INTENT_RE = re.compile(
    r"^(你好|您好|hi|hello|hey|嗨|你在|在的|帮我|请|能否)",
    re.IGNORECASE,
)


def _intent_needs_relabel(intent_text: str | None, query: str | None) -> bool:
    if not intent_text:
        return True
    intent = intent_text.strip()
    if len(intent) < 4:
        return True
    if _HEURISTIC_INTENT_RE.match(intent):
        return True
    # If intent is exactly the truncated first user turn, replace it.
    if query and intent.rstrip("…").strip() == (query.strip()[:120].rstrip("…").strip()):
        return True
    return False


def auto_label_experience(
    db_path: str,
    experience_id: str,
    *,
    max_turns: int | None = None,
) -> dict[str, Any]:
    """Background task entrypoint. Re-labels intent + annotates rewards.

    Idempotent: if experience already has rewards from this judge_model, skips.
    """
    if not _enabled():
        return {"skipped": True, "reason": "disabled"}
    cfg = _config()
    if not all((cfg["base_url"], cfg["api_key"], cfg["model"])):
        return {"skipped": True, "reason": "no backend"}

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT experience_id, query, intent_text, trajectory_path "
            "FROM experiences WHERE experience_id = ?",
            (experience_id,),
        ).fetchone()
        if not row:
            return {"skipped": True, "reason": "experience not found"}

        trajectory = _read_trajectory(row["trajectory_path"])
        result: dict[str, Any] = {"experience_id": experience_id}

        # 1. Intent re-label
        if _intent_needs_relabel(row["intent_text"], row["query"]):
            try:
                user_block = _format_for_intent(trajectory)
                if user_block.strip():
                    t0 = time.time()
                    # mimo-v2.5-pro is a reasoning model — typically burns
                    # 500-800 reasoning_tokens on a free-form intent task
                    # before emitting any visible text. Cap at 1500 to leave
                    # room for both reasoning and the actual ~10-20-token label.
                    text, usage = _chat_completion(INTENT_SYSTEM, user_block, max_tokens=1500)
                    dt = int((time.time() - t0) * 1000)
                    label = _clean_label(text)
                    _log_usage(conn,
                               experience_id=experience_id,
                               kind="intent",
                               backend="openai-compat",
                               model=cfg["model"],
                               prompt=usage["prompt"],
                               completion=usage["completion"],
                               latency_ms=dt,
                               ok=bool(label),
                               error=None if label else "no usable label")
                    if label:
                        with conn:
                            conn.execute(
                                "UPDATE experiences SET intent_text=? WHERE experience_id=?",
                                (label, experience_id),
                            )
                        result["intent"] = label
            except Exception as e:
                _log_usage(conn, experience_id=experience_id, kind="intent",
                           backend="openai-compat", model=cfg["model"],
                           prompt=0, completion=0, latency_ms=0,
                           ok=False, error=str(e)[:200])
                result["intent_error"] = str(e)[:120]

        # 2. Skip rewards if already annotated by this model
        already = conn.execute(
            "SELECT 1 FROM turn_rewards WHERE experience_id=? AND judge_model=? LIMIT 1",
            (experience_id, cfg["model"]),
        ).fetchone()
        if already:
            result["rewards_skipped"] = "already-annotated"
            return result

        # 3. Annotate per-turn rewards
        pairs = _find_pairs(trajectory)
        cap = max_turns if max_turns is not None \
            else int(os.environ.get("EXP_AUTO_LABEL_MAX_TURNS", "5"))
        selected = _pick_evenly(pairs, cap)
        rows: list[tuple] = []
        annotated_at = _dt.datetime.utcnow().isoformat(timespec="seconds")
        for u_idx, a_idx in selected:
            user_block = _build_reward_input(trajectory, u_idx, a_idx)
            try:
                t0 = time.time()
                text, usage = _chat_completion(REWARD_SYSTEM, user_block,
                                               max_tokens=400, json_mode=True)
                dt = int((time.time() - t0) * 1000)
                obj = _extract_json(text)
                ok = obj is not None
                _log_usage(conn,
                           experience_id=experience_id,
                           kind="reward",
                           backend="openai-compat",
                           model=cfg["model"],
                           prompt=usage["prompt"],
                           completion=usage["completion"],
                           latency_ms=dt,
                           ok=ok,
                           error=None if ok else "non-json response")
                if not ok:
                    continue
                v = _validate_reward(obj)
                rows.append((
                    experience_id, a_idx, u_idx,
                    v["outcome"], v["intent"], v["execution"],
                    v["orchestration"], v["expression"],
                    v["confidence"], v["reason"],
                    cfg["model"], "openai-compat",
                    annotated_at, "auto-label",
                ))
            except Exception as e:
                _log_usage(conn, experience_id=experience_id, kind="reward",
                           backend="openai-compat", model=cfg["model"],
                           prompt=0, completion=0, latency_ms=0,
                           ok=False, error=str(e)[:200])
        if rows:
            with conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO turn_rewards
                       (experience_id, turn_index, user_turn_index,
                        r_outcome, r_intent, r_execution, r_orchestration,
                        r_expression, confidence, reason,
                        judge_model, judge_backend, annotated_at, annotated_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
        result["rewards_stored"] = len(rows)
        # Refresh trajectory_score + eligibility (blends LLM rewards with
        # structural metrics so long sessions get fair treatment).
        try:
            from . import quality as quality_mod
            q = quality_mod.recompute_quality(conn, experience_id=experience_id)
            result["trajectory_score"] = q.get("trajectory_score")
            result["is_memory_eligible"] = q.get("is_memory_eligible")
            result["is_sft_eligible"] = q.get("is_sft_eligible")
        except Exception:
            pass
        # Cluster + maybe crystallize. Eligibility-gated inside.
        try:
            from . import crystallize as crystal_mod
            assignment = crystal_mod.assign_to_cluster(
                conn, experience_id=experience_id,
            )
            if assignment and assignment.get("cluster_id"):
                cid = assignment["cluster_id"]
                result["cluster"] = {
                    "id": cid,
                    "action": assignment.get("action") or "skipped",
                    "similarity": assignment.get("similarity"),
                }
                synth = crystal_mod.maybe_crystallize(conn, cluster_id=cid)
                if synth:
                    result["crystallize"] = {
                        k: v for k, v in synth.items() if k != "usage"
                    }
        except Exception as e:
            result["cluster_error"] = str(e)[:120]
        return result
    finally:
        conn.close()


def _build_reward_input(trajectory: list[dict[str, Any]], u_idx: int, a_idx: int) -> str:
    user = trajectory[u_idx]
    asst = trajectory[a_idx]
    user_block = str(user.get("content", "")).strip()
    asst_block = _format_assistant(asst)
    subseq = _format_subsequent(trajectory[a_idx + 1:], k=4)
    return (
        f"<user>\n{user_block}\n</user>\n\n"
        f"<assistant>\n{asst_block}\n</assistant>\n\n"
        f"<subsequent>\n{subseq or '(no follow-up turns)'}\n</subsequent>"
    )


def _clean_label(text: str) -> str:
    """Extract the first usable label line from the model's response."""
    if not text:
        return ""
    # Find the first non-empty line that looks like a label.
    for line in text.splitlines():
        cand = line.strip().strip('"').strip("'").strip("「」")
        for prefix in ("LABEL:", "Label:", "label:", "TASK:", "Task:", "task:",
                       "标签:", "标签：", "任务:", "任务：", "**LABEL**:", "**Label**:"):
            if cand.startswith(prefix):
                cand = cand[len(prefix):].strip()
        if not cand:
            continue
        cand = cand.rstrip(".。 ")
        # Reject only when the LINE STARTS WITH conversational filler — substring
        # match was too aggressive (a label like "排查 i'm here 错误" would die).
        lowered = cand.lower()
        starts_bad = any(lowered.startswith(p) for p in (
            "i'm here", "i am here", "i'm an ai", "hello!", "hi there", "hey ",
            "我在的", "您好,", "您好，", "你好,", "你好，",
            "抱歉,", "抱歉，", "sorry,", "ok, i'll", "好的, 我", "好的，我",
            "sure,", "got it,", "明白了,", "明白了，",
        ))
        if starts_bad:
            continue
        if cand == "(no task)" or cand.lower() == "(no task)":
            return ""
        if len(cand) > 120:
            cand = cand[:117].rstrip() + "…"
        return cand
    return ""


# ---------------------------------------------------------------------------
# Usage stats — read by /v1/admin/usage
# ---------------------------------------------------------------------------

def usage_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute(
        """SELECT
             COUNT(*) AS n,
             SUM(prompt_tokens) AS prompt,
             SUM(completion_tokens) AS completion,
             SUM(total_tokens) AS total,
             SUM(cost_usd) AS cost,
             SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) AS ok_count,
             SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS err_count
           FROM llm_usage""",
    ).fetchone()
    by_kind: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        """SELECT kind, COUNT(*) AS n, SUM(total_tokens) AS tok, SUM(cost_usd) AS cost
           FROM llm_usage GROUP BY kind"""
    ):
        by_kind[r[0]] = {"calls": r[1] or 0, "tokens": r[2] or 0, "cost_usd": round(r[3] or 0, 4)}
    by_model: list[dict[str, Any]] = []
    for r in conn.execute(
        """SELECT model, COUNT(*) AS n, SUM(total_tokens) AS tok, SUM(cost_usd) AS cost
           FROM llm_usage GROUP BY model ORDER BY tok DESC"""
    ):
        by_model.append({
            "model": r[0],
            "calls": r[1] or 0,
            "tokens": r[2] or 0,
            "cost_usd": round(r[3] or 0, 4),
        })
    return {
        "total_calls": row[0] or 0,
        "prompt_tokens": row[1] or 0,
        "completion_tokens": row[2] or 0,
        "total_tokens": row[3] or 0,
        "total_cost_usd": round(row[4] or 0, 4),
        "ok": row[5] or 0,
        "errors": row[6] or 0,
        "by_kind": by_kind,
        "by_model": by_model,
    }

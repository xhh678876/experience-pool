"""v0 lite path — the minimum viable shape.

What it does:
    1. agent runs `prepare_local(trajectory)` to produce a {query, intent,
       steps, outcome} card. Sanitization (Layer 1 only) happens client-side
       so the raw trajectory never leaves the agent host.
    2. agent uploads the card to the server via push_lite().
    3. server stores card + embedding in SQLite (existing schema, with new
       `query` and `outcome` columns added by the lite migration).
    4. retrieval is pure cosine over the intent+query embedding, ACL-filtered.

What it deliberately does NOT do:
    - 5-dim judge / scoring
    - Credit assignment (parents earn no Q)
    - Skill bundle integration
    - Dedup
    - FTS5 keyword blending
    - Mixed Q + UCB ranking

The heavy machinery is still in pool.py and intentionally NOT called here.
The sql columns (q_*, edges, etc.) stay zero/empty so we can flip the heavy
path back on later without a schema change.

ACL kinds accepted by this module: 'private' | 'team:<X>' | 'public' | 'org'.
Direct uploads are stored as private/team only. Community visibility is granted
only by the publish endpoint after strict-public checks pass.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import llm, opf_filter
from .embeddings import cosine, embed, from_blob, to_blob
from .identity import can_read, parse_acl
from .sanitize import RuleSet, layer1_text, load_rules


@dataclass
class LiteCard:
    """The v0 four-field shape. Plus a tiny sidecar of provenance fields."""
    query: str           # the user's original ask, verbatim (sanitized)
    intent: str          # one-liner: what kind of task this is
    steps: list[str]     # ordered list of what was done
    outcome: str         # what the final state is
    task_type: str = "misc"
    source_model: str = "unknown"
    sensitivity: str = "medium"   # low | medium | high
    acl: str = "private"          # private | team:<X> | public | org
    tags: list[str] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query, "intent": self.intent,
            "steps": self.steps, "outcome": self.outcome,
            "task_type": self.task_type, "source_model": self.source_model,
            "sensitivity": self.sensitivity, "acl": self.acl,
            "tags": self.tags, "redactions": self.redactions,
        }


# ---------------------------------------------------------------------------
# Local sanitize (client-side Layer 1)
# ---------------------------------------------------------------------------

def sanitize_trajectory_local(
    trajectory: list[dict[str, Any]], rules: RuleSet | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Run Layer 1 (regex rules) on each turn's content. Returns (cleaned, counts).

    This runs on the agent host before anything is uploaded. The same regex
    rules also run server-side as defense-in-depth, but the agent shouldn't
    rely on that — secrets shouldn't leave the host."""
    rules = rules or load_rules()
    counts: dict[str, int] = {}
    cleaned = []
    for turn in trajectory:
        new_turn = dict(turn)
        for key in ("content", "tool_input", "tool_output"):
            val = new_turn.get(key)
            if isinstance(val, str):
                redacted, c, _ = layer1_text(val, rules)
                new_turn[key] = redacted
                for k, v in c.items():
                    counts[k] = counts.get(k, 0) + v
        cleaned.append(new_turn)
    return cleaned, counts


# ---------------------------------------------------------------------------
# Structuring (rule-based + LLM)
# ---------------------------------------------------------------------------

def structure_rule_based(trajectory: list[dict[str, Any]]) -> LiteCard:
    """Zero-LLM extraction. Heuristic but deterministic.

    - query: first user turn
    - intent: same as query, truncated
    - steps: each assistant turn becomes one step (truncated)
    - outcome: last assistant turn

    Useful for: offline / no-API-key / cost-sensitive deployments.
    """
    query = ""
    steps: list[str] = []
    outcome = ""
    for turn in trajectory:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role == "user" and not query:
            query = content
        elif role == "assistant":
            steps.append(content[:280])
            outcome = content  # last assistant wins
    intent = (query[:120] or "unspecified task").strip()
    return LiteCard(
        query=query or "(no user turn)",
        intent=intent,
        steps=steps,
        outcome=outcome[:500] or "(no assistant turn)",
    )


_STRUCTURE_SYSTEM = (
    "You are a trajectory summarizer. Read an agent trajectory and produce a "
    "compact JSON object with exactly these fields: query, intent, steps, "
    "outcome. JSON only, no prose."
)

_STRUCTURE_PROMPT = """Read this trajectory and produce a compact JSON object.

Trajectory:
{trajectory_json}

Return JSON with these exact keys:
{{
  "query": "<user's original ask, one sentence>",
  "intent": "<one-line description of the kind of task — model-agnostic>",
  "steps": ["<short imperative>", "<short imperative>", "..."],
  "outcome": "<what the final state is, one or two sentences>"
}}

Output ONLY the JSON object."""


def structure_llm(trajectory: list[dict[str, Any]]) -> LiteCard:
    """LLM-based structuring. Falls back to rule-based on any error."""
    prompt = _STRUCTURE_PROMPT.format(
        trajectory_json=json.dumps(trajectory, ensure_ascii=False)
    )
    try:
        data = llm.call_json(prompt, system=_STRUCTURE_SYSTEM)
    except Exception:  # noqa: BLE001
        return structure_rule_based(trajectory)
    return LiteCard(
        query=str(data.get("query", "")),
        intent=str(data.get("intent", "")),
        steps=[str(s) for s in (data.get("steps") or [])],
        outcome=str(data.get("outcome", "")),
    )


def prepare_local(
    trajectory: list[dict[str, Any]],
    *,
    use_llm: bool = False,
    task_type: str = "misc",
    source_model: str = "unknown",
    sensitivity: str = "medium",
    acl: str = "private",
    tags: list[str] | None = None,
    rules: RuleSet | None = None,
) -> LiteCard:
    """The full client-side prep: sanitize → structure → ready-to-upload card."""
    sanitized, redactions = sanitize_trajectory_local(trajectory, rules=rules)
    card = (structure_llm if use_llm else structure_rule_based)(sanitized)
    card.task_type = task_type
    card.source_model = source_model
    card.sensitivity = sensitivity
    card.acl = acl
    card.tags = list(tags or [])
    card.redactions = redactions
    return card


# ---------------------------------------------------------------------------
# Server-side push (skip judge/extract/credit; just sanitize, embed, store)
# ---------------------------------------------------------------------------

def _normalize_acl(acl: str) -> str:
    """Normalize direct-upload ACLs and fail closed.

    Older clients may still send ``public`` or the legacy ``org`` spelling.
    Treat both as ``private`` here so raw uploads cannot bypass the explicit
    publish gate, which runs strict public-safety checks before setting
    ``acl='public'``.
    """
    acl = (acl or "private").strip()
    if acl.startswith("team:"):
        return acl
    if acl == "private":
        return "private"
    return "private"


def push_lite(
    conn: sqlite3.Connection,
    *,
    rules: RuleSet,
    agent_name: str,
    card: LiteCard,
    trajectory: list[dict[str, Any]] | None = None,
    system: list[dict[str, Any]] | str | None = None,
    tools: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    trajectories_dir: "Path | None" = None,
) -> dict[str, Any]:
    """Insert a lite card. Sanitize again server-side as belt-and-suspenders.

    If `trajectory` is provided, it is sanitized (Layer 1) and stored as
    `<trajectories_dir>/<eid>.json`, with the path recorded in
    `experiences.trajectory_path` so the UI's Trajectory tab can render it.

    No judge, no extractor, no credit. The point is: searchable now."""
    import time as _time
    import os as _os2
    _PROF = _os2.environ.get("EXP_PROFILE_PUSH", "0") == "1"
    _t0 = _time.perf_counter()
    _ts: list[tuple[str, float]] = []
    def _mark(label: str) -> None:
        if _PROF:
            _ts.append((label, _time.perf_counter() - _t0))

    cur = conn.execute("SELECT agent_id, team FROM agents WHERE name = ?", (agent_name,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown agent: {agent_name}")
    agent_id = row["agent_id"]
    _mark("agent_lookup")

    # ── Dedup / 同 session 续传 ──────────────────────────────────────
    # 优先级:
    #   1. (agent_id, session_id):同一 session 多次 push,**原地 UPDATE
    #      已有 row**(turn_count 增长就续写,等同就 dedup)。这让 SessionEnd
    #      hook 在长会话里反复触发不会留一堆短快照,UI 上始终只看到一条
    #      并随后续对话自动续传。
    #   2. content_fingerprint:没有 session_id(generic JSON / push-file)
    #      时退回到完全相同 trajectory 的去重。
    #
    # `meta.session_id` 由 adapter 提供(claude-code 是 .jsonl 的 stem,
    #  codex 是 rollout 文件名等)。
    session_id = None
    if isinstance(meta, dict):
        sid_raw = meta.get("session_id")
        if isinstance(sid_raw, str) and sid_raw.strip():
            session_id = sid_raw.strip()[:200]
    new_turn_count = len(trajectory) if trajectory else 0

    from . import quality as quality_mod
    fingerprint = quality_mod.compute_fingerprint(trajectory)
    _mark("fingerprint")

    # 路径 1:session-singleton update-in-place
    if session_id:
        existing_row = conn.execute(
            """SELECT experience_id, turn_count, content_fingerprint
               FROM experiences
               WHERE agent_id = ? AND session_id = ?
                 AND superseded = 0 AND revoked = 0
               ORDER BY turn_count DESC LIMIT 1""",
            (agent_id, session_id),
        ).fetchone()
        if existing_row is not None:
            existing_eid = existing_row["experience_id"]
            existing_count = existing_row["turn_count"] or 0
            existing_fp = existing_row["content_fingerprint"]
            # 比当前已存的更短或一样 → 直接 dedup,不动现有 row
            if new_turn_count <= existing_count or fingerprint == existing_fp:
                return {
                    "experience_id": existing_eid,
                    "duplicate_of": existing_eid,
                    "fingerprint": fingerprint,
                    "ingest_path": "lite-dup",
                    "session_id": session_id,
                    "turn_count": existing_count,
                    "review_status": "auto_approved",
                    "sanitization_status": "skipped",
                    "redactions": {},
                    "deduplicated_reason": (
                        "shorter_or_equal" if new_turn_count <= existing_count
                        else "same_fingerprint"
                    ),
                }
            # 当前 push 比已存版本更长 → 标记走 update-in-place,继续走完
            # 下面的脱敏 / 写文件流程,但最终不 INSERT 而是 UPDATE。
            # 把 eid 暴露给后面的 INSERT/UPDATE 分叉用。
            update_target_eid = existing_eid
        else:
            update_target_eid = None
    else:
        update_target_eid = None

    # 路径 2:fingerprint 去重(没 session_id 或 session_id 这一支没命中)
    if update_target_eid is None and fingerprint:
        existing = quality_mod.find_existing_by_fingerprint(
            conn, fingerprint=fingerprint, agent_id=agent_id,
        )
        if existing:
            return {
                "experience_id": existing,
                "duplicate_of": existing,
                "fingerprint": fingerprint,
                "ingest_path": "lite-dup",
                "review_status": "auto_approved",
                "sanitization_status": "skipped",
                "redactions": {},
            }

    # Server-side sanitize on every text field of the card.
    # Layer 1 (regex) runs unconditionally; Layer 1.5 (OPF) runs whenever
    # opf_filter is loadable. OPF results are recorded in the redactions
    # dict prefixed with `opf_` so downstream audits can attribute them.
    redactions = dict(card.redactions)
    server_high = False
    opf_high = False

    # OPF is the slow part of the sanitize pipeline. Three modes:
    #   1. EXP_DEFER_OPF=1            — skip OPF; mark row 'layer1_only';
    #                                   a worker backfills later
    #   2. EXP_OPF_REMOTE_URL=http... — call remote OPF service over HTTP
    #                                   (recommended: deploys OPF on a
    #                                   separate GPU machine, keeps main
    #                                   API responsive)
    #   3. neither set                — load OPF in-process (slow, legacy)
    import os as _os
    defer_opf = _os.getenv("EXP_DEFER_OPF", "0").lower() not in {"0", "false", "no", ""}
    remote_opf_url = _os.getenv("EXP_OPF_REMOTE_URL", "").rstrip("/")
    remote_opf_token = _os.getenv("EXP_OPF_AUTH_TOKEN", "")
    remote_opf_timeout = float(_os.getenv("EXP_OPF_TIMEOUT_SECONDS", "8"))

    def _opf_remote(text: str) -> tuple[str, dict[str, int], bool]:
        """Call the remote OPF service. Returns (cleaned, hits, triggered_high).
        On any failure, falls through to (text, {}, False) so the push path
        does not break — the main contract is "best-effort sanitize".
        """
        import json as _json
        import urllib.request as _u
        body = _json.dumps({"text": text}).encode("utf-8")
        headers = {"content-type": "application/json"}
        if remote_opf_token:
            headers["x-opf-token"] = remote_opf_token
        req = _u.Request(f"{remote_opf_url}/redact-text", data=body,
                         headers=headers, method="POST")
        try:
            with _u.urlopen(req, timeout=remote_opf_timeout) as resp:
                d = _json.load(resp)
        except Exception:
            return text, {}, False
        return d.get("text", text), d.get("hits", {}) or {}, bool(d.get("triggered_high"))

    def _scrub_str(val: str) -> str:
        nonlocal server_high, opf_high
        if not val:
            return val
        cleaned, c, hi = layer1_text(val, rules)
        for k, v in c.items():
            redactions[k] = redactions.get(k, 0) + v
        server_high = server_high or hi
        if defer_opf:
            return cleaned  # layer1_only mode
        if remote_opf_url:
            cleaned2, hits, triggered = _opf_remote(cleaned)
            if hits:
                cleaned = cleaned2
                for label, n in hits.items():
                    key = f"opf_{label}"
                    redactions[key] = redactions.get(key, 0) + n
                opf_high = opf_high or triggered
            return cleaned
        if opf_filter.is_enabled():
            res = opf_filter.redact_text(cleaned)
            if res.hits:
                cleaned = res.text
                for label, n in res.hits.items():
                    key = f"opf_{label}"
                    redactions[key] = redactions.get(key, 0) + n
                opf_high = opf_high or res.triggered_high
        return cleaned

    for attr in ("query", "intent", "outcome"):
        setattr(card, attr, _scrub_str(getattr(card, attr)))
    card.steps = [_scrub_str(s) for s in card.steps]
    _mark("sanitize_card")

    # If a raw trajectory was sent, recursively sanitize every text-bearing
    # field. Trajectories may use any of:
    #   - flat shape:        {"role": "...", "content": "string"}
    #   - Anthropic blocks:  {"role": "assistant",
    #                         "content": [{"type":"text","text":"..."},
    #                                     {"type":"tool_use","name":"Read",
    #                                      "input":{"file_path":"..."}},
    #                                     {"type":"tool_result",
    #                                      "tool_use_id":"...",
    #                                      "content":"..."}]}
    #   - OpenAI tool_calls: {"role":"assistant","tool_calls":[...]} +
    #                        {"role":"tool","tool_call_id":"...","content":"..."}
    #
    # We walk dicts/lists and apply Layer 1 to any string we find, regardless
    # of nesting depth, so secrets in tool inputs / outputs cannot bypass.
    cleaned_trajectory: list[dict[str, Any]] | None = None
    if trajectory:
        # Skip keys that are pure identifiers or types — sanitizing those
        # would corrupt routing (tool_use_id, message id, role, etc.).
        SKIP_KEYS = {
            "id", "type", "role", "tool_use_id", "tool_call_id",
            "name", "subtype", "model", "stop_reason", "stop_sequence",
            "usage", "index",
        }

        # Honor the same defer_opf / remote_opf_url switches as _scrub_str.
        # Without this, trajectory walking calls OPF in-process per string,
        # which dwarfs everything else: a 4-turn session takes 55s on cpu
        # with only Layer 1 fast and OPF model-bound.
        opf_on = (not defer_opf) and (not remote_opf_url) and opf_filter.is_enabled()

        def _walk(node: Any) -> Any:
            nonlocal server_high, opf_high
            if isinstance(node, str):
                cleaned, counts, hi = layer1_text(node, rules)
                for k, v in counts.items():
                    redactions[k] = redactions.get(k, 0) + v
                server_high = server_high or hi
                if opf_on:
                    res = opf_filter.redact_text(cleaned)
                    if res.hits:
                        cleaned = res.text
                        for label, n in res.hits.items():
                            key = f"opf_{label}"
                            redactions[key] = redactions.get(key, 0) + n
                        opf_high = opf_high or res.triggered_high
                elif remote_opf_url:
                    cleaned2, hits, triggered = _opf_remote(cleaned)
                    if hits:
                        cleaned = cleaned2
                        for label, n in hits.items():
                            key = f"opf_{label}"
                            redactions[key] = redactions.get(key, 0) + n
                        opf_high = opf_high or triggered
                return cleaned
            if isinstance(node, list):
                return [_walk(item) for item in node]
            if isinstance(node, dict):
                out: dict[str, Any] = {}
                for k, v in node.items():
                    if k in SKIP_KEYS or not isinstance(v, (str, list, dict)):
                        out[k] = v
                    else:
                        out[k] = _walk(v)
                return out
            return node

        cleaned_trajectory = [
            _walk(t) if isinstance(t, dict) else {"content": _walk(str(t))}
            for t in trajectory
        ]
    _mark("sanitize_trajectory")

    # Sanitize system / tools / meta with the same recursive walker.
    cleaned_system = None
    cleaned_tools = None
    cleaned_meta = None
    if trajectory or system or tools or meta:
        # Reuse the closure if it was created above; otherwise build it now.
        if "_walk" not in dir():
            SKIP_KEYS = {
                "id", "type", "role", "tool_use_id", "tool_call_id",
                "name", "subtype", "model", "stop_reason", "stop_sequence",
                "usage", "index",
            }
            opf_on_aux = (not defer_opf) and (not remote_opf_url) and opf_filter.is_enabled()

            def _walk(node: Any) -> Any:  # noqa: F811
                nonlocal server_high, opf_high
                if isinstance(node, str):
                    cleaned, counts, hi = layer1_text(node, rules)
                    for k, v in counts.items():
                        redactions[k] = redactions.get(k, 0) + v
                    server_high = server_high or hi
                    if opf_on_aux:
                        res = opf_filter.redact_text(cleaned)
                        if res.hits:
                            cleaned = res.text
                            for label, n in res.hits.items():
                                key = f"opf_{label}"
                                redactions[key] = redactions.get(key, 0) + n
                            opf_high = opf_high or res.triggered_high
                    elif remote_opf_url:
                        cleaned2, hits, triggered = _opf_remote(cleaned)
                        if hits:
                            cleaned = cleaned2
                            for label, n in hits.items():
                                key = f"opf_{label}"
                                redactions[key] = redactions.get(key, 0) + n
                            opf_high = opf_high or triggered
                    return cleaned
                if isinstance(node, list):
                    return [_walk(x) for x in node]
                if isinstance(node, dict):
                    return {
                        k: (v if k in SKIP_KEYS or not isinstance(v, (str, list, dict)) else _walk(v))
                        for k, v in node.items()
                    }
                return node

        if system is not None:
            cleaned_system = _walk(system)
        if tools is not None:
            cleaned_tools = _walk(tools)
        if meta is not None:
            cleaned_meta = _walk(meta)

    # OPF `secret` hits are treated as high severity (regex missed them but
    # OPF says they're credentials → human review, not auto-approved).
    high_overall = server_high or opf_high
    if defer_opf:
        # Layer 1 done, OPF deferred to background backfill. Don't claim
        # 'done' since the heavier privacy pass hasn't run yet.
        sanitization_status = "layer1_only"
    else:
        sanitization_status = (
            "human_review" if high_overall else ("flagged" if redactions else "done")
        )
    review_status = "pending" if high_overall else "auto_approved"

    # 走 update-in-place 时复用旧 eid;新 row 才 mint 新 uuid
    is_update = update_target_eid is not None
    eid = update_target_eid if is_update else str(uuid.uuid4())
    acl = _normalize_acl(card.acl)

    trajectory_path: str | None = None
    has_payload = (
        cleaned_trajectory is not None
        or cleaned_system is not None
        or cleaned_tools is not None
        or cleaned_meta is not None
    )
    if has_payload and trajectories_dir is not None:
        trajectories_dir.mkdir(parents=True, exist_ok=True)
        traj_file = trajectories_dir / f"{eid}.json"
        sidecar: dict[str, Any] = {}
        if cleaned_trajectory is not None:
            sidecar["trajectory"] = cleaned_trajectory
        if cleaned_system is not None:
            sidecar["system"] = cleaned_system
        if cleaned_tools is not None:
            sidecar["tools"] = cleaned_tools
        if cleaned_meta is not None:
            sidecar["meta"] = cleaned_meta
        # update 路径会原地覆盖文件,write_text 自然替换;无需先 unlink
        traj_file.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        trajectory_path = str(traj_file)
    _mark("write_trajectory_file")

    if is_update:
        # 同 session 续传:UPDATE 现有 row。保留 created_at / experience_id /
        # publish_status,刷新 trajectory + 各字段。
        conn.execute(
            """
            UPDATE experiences SET
                task_type=?, source_model=?,
                query=?, intent_text=?, script_steps=?, outcome=?, summary=?,
                sensitivity=?, acl=?, tags=?,
                sanitization_status=?, review_status=?, extraction_status=?,
                trajectory_path=?,
                turn_count=?,
                content_fingerprint=?
            WHERE experience_id=?
            """,
            (
                card.task_type, card.source_model,
                card.query, card.intent, json.dumps(card.steps), card.outcome,
                card.outcome,
                card.sensitivity, acl, json.dumps(card.tags),
                sanitization_status, review_status, "done",
                trajectory_path,
                new_turn_count,
                fingerprint,
                eid,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO experiences (
                experience_id, agent_id, task_type, source_model,
                query, intent_text, script_steps, outcome, summary,
                sensitivity, acl, tags,
                sanitization_status, review_status, extraction_status,
                ingest_path, trajectory_path,
                session_id, turn_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid, agent_id, card.task_type, card.source_model,
                card.query, card.intent, json.dumps(card.steps), card.outcome,
                card.outcome,  # also fill summary so existing UI shows something
                card.sensitivity, acl, json.dumps(card.tags),
                sanitization_status, review_status, "done",
                "lite", trajectory_path,
                session_id, new_turn_count,
            ),
        )
    _mark("insert_or_update_experiences")

    # Single embedding over (intent + query). Steps and outcome are kept
    # in the row but not embedded separately in v0 — pure cosine over
    # this one vector is what search uses.
    text_to_embed = (card.intent + " " + card.query).strip() or card.outcome
    vec = embed(text_to_embed)
    _mark("embed")
    payload = json.dumps({"task_type": card.task_type, "acl": acl})
    conn.execute(
        "INSERT OR REPLACE INTO vectors (experience_id, kind, payload, vector) VALUES (?, ?, ?, ?)",
        (eid, "intent", payload, to_blob(vec)),
    )
    _mark("insert_vector")

    conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
        (agent_name, "agent", "push_lite", eid,
         json.dumps({"redactions": redactions,
                     "sanitization_status": sanitization_status,
                     "task_type": card.task_type,
                     "opf_status": opf_filter.status()})),
    )
    _mark("insert_audit")
    conn.commit()
    _mark("commit_main")

    # Record fingerprint + compute structural metrics + initial quality.
    # All defensive — wrap in try so a quality calc error never breaks push.
    try:
        quality_mod.record_fingerprint(
            conn, fingerprint=fingerprint, experience_id=eid, agent_id=agent_id,
        )
        _mark("record_fingerprint")
        quality_mod.attach_structural_metrics(
            conn, experience_id=eid, trajectory=cleaned_trajectory or trajectory,
        )
        _mark("attach_struct")
        quality_mod.recompute_quality(conn, experience_id=eid)
        _mark("recompute_quality")
    except Exception:
        pass
    if _PROF:
        # print to stderr; uvicorn surfaces it in server.log
        import sys as _sys
        prev = 0.0
        lines = ["[push_lite profile]"]
        for label, t in _ts:
            lines.append(f"  +{(t-prev)*1000:7.1f}ms  @{t*1000:8.1f}ms  {label}")
            prev = t
        print("\n".join(lines), file=_sys.stderr, flush=True)
    return {
        "experience_id": eid,
        "review_status": review_status,
        "sanitization_status": sanitization_status,
        "redactions": redactions,
        "ingest_path": "lite",
        "acl": acl,
        "session_id": session_id,
        "turn_count": new_turn_count,
        "updated_in_place": is_update,
    }


# ---------------------------------------------------------------------------
# Pure cosine search with ACL
# ---------------------------------------------------------------------------

def search_lite_with_meta(
    conn: sqlite3.Connection,
    *,
    viewer_name: str,
    query: str,
    top_k: int = 5,
    task_type: str | None = None,
    scope: str = "auto",
) -> dict[str, Any]:
    """Two-stage search returning structured metadata.

    Stage 1: viewer's own personal pool (acl='private' OR
             agent.owner == viewer.owner). Always returned.
    Stage 2: community pool (acl='public', publish_status='published').
             Only included when the viewer's owner has publish_count
             >= COMMUNITY_THRESHOLD.

    `scope` controls which stages run:
        'auto'      — stage 1 + stage 2 (gated by quota)
        'personal'  — stage 1 only (force private pool)
        'community' — stage 2 only (force community pool; still gated)

    Returns {results, personal, community, quota, scope, community_locked_hint}.
    See search_lite() for the legacy list-only shape used by older callers.
    """
    from . import community as community_mod

    cur = conn.execute(
        "SELECT agent_id, team, owner FROM agents WHERE name = ?", (viewer_name,)
    )
    me = cur.fetchone()
    if me is None:
        raise ValueError(f"unknown agent: {viewer_name}")
    viewer_id, viewer_team = me["agent_id"], me["team"]
    viewer_owner = me["owner"] or viewer_name

    # Find every agent_id sharing this owner (multi-agent personal pool).
    cur = conn.execute(
        "SELECT agent_id FROM agents WHERE owner = ? OR (owner IS NULL AND name = ?)",
        (viewer_owner, viewer_owner),
    )
    owner_agent_ids = {r["agent_id"] for r in cur.fetchall()}

    qvec = embed(query)

    quota = community_mod.get_quota(conn, viewer_owner)
    community_unlocked = quota.community_unlocked

    # Pull the candidate set once. Filter in Python so we can apply both
    # ACL + quota + owner-pool semantics in one place. Revoked rows are
    # excluded by review_status='revoked' which isn't in the allowlist.
    # Keep quota initialization above this query: get_quota() may open a
    # short-lived write connection, and an unconsumed SELECT cursor on this
    # connection can otherwise hold a read lock and make SQLite wait.
    cur = conn.execute(
        """
        SELECT v.experience_id, v.vector, e.task_type, e.review_status,
               e.query, e.intent_text, e.script_steps, e.outcome,
               e.acl, e.agent_id, e.created_at, e.ingest_path,
               COALESCE(e.publish_status, 'private') AS publish_status,
               e.published_at
        FROM vectors v JOIN experiences e USING(experience_id)
        WHERE v.kind = 'intent'
          AND e.review_status IN ('approved', 'auto_approved', 'edited')
          AND e.extraction_status = 'done'
          AND COALESCE(e.revoked, 0) = 0
        """
    )

    want_personal = scope in ("auto", "personal")
    want_community = scope in ("auto", "community")

    personal: list[tuple[float, sqlite3.Row]] = []
    community: list[tuple[float, sqlite3.Row]] = []

    for row in cur.fetchall():
        if task_type and row["task_type"] != task_type:
            continue

        is_owner_row = row["agent_id"] in owner_agent_ids
        is_published = row["publish_status"] == "published" and row["acl"] == "public"

        # Stage 1 — personal pool (any of the owner's agents)
        if is_owner_row and want_personal:
            sim = cosine(qvec, from_blob(row["vector"]))
            personal.append((sim, row))
            continue

        # Stage 2 — community pool (only if unlocked)
        if is_published and want_community and community_unlocked:
            # Don't show your own published rows again in the community
            # bucket — they were already in personal.
            if is_owner_row:
                continue
            sim = cosine(qvec, from_blob(row["vector"]))
            community.append((sim, row))
            continue

        # Otherwise: legacy ACL path (team:X, etc.). Do not let historical rows
        # with acl=public/org bypass the community publish_status gate.
        if row["acl"] in {"public", "org"}:
            continue
        if want_personal and can_read(viewer_id, viewer_team, row["agent_id"], row["acl"]):
            sim = cosine(qvec, from_blob(row["vector"]))
            personal.append((sim, row))

    personal.sort(key=lambda x: x[0], reverse=True)
    community.sort(key=lambda x: x[0], reverse=True)

    def _row_to_dict(sim: float, row: sqlite3.Row, source: str) -> dict[str, Any]:
        return {
            "experience_id": row["experience_id"],
            "query": row["query"],
            "intent": row["intent_text"],
            "steps": json.loads(row["script_steps"] or "[]"),
            "outcome": row["outcome"] or "",
            "task_type": row["task_type"],
            "acl": row["acl"],
            "ingest_path": row["ingest_path"],
            "publish_status": row["publish_status"],
            "similarity": sim,
            "source": source,
        }

    personal_results = [_row_to_dict(s, r, "personal") for s, r in personal[:top_k]]
    community_results = [_row_to_dict(s, r, "community") for s, r in community[:top_k]]

    # Combine for the legacy `results` field (UI compatibility): personal
    # first, then community filling up to top_k.
    combined: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for r in personal_results + community_results:
        if r["experience_id"] in seen_ids:
            continue
        combined.append(r)
        seen_ids.add(r["experience_id"])
        if len(combined) >= top_k:
            break

    # Bump visit_count for whatever we returned.
    ids = [r["experience_id"] for r in combined]
    if ids:
        conn.execute(
            "UPDATE experiences SET visit_count = visit_count + 1 "
            f"WHERE experience_id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()

    return {
        "results": combined,
        "personal": personal_results,
        "community": community_results,
        "quota": quota.to_dict(),
        "scope": scope,
        "community_locked_hint": (
            None if community_unlocked
            else f"community pool locked: {quota.hint}"
        ),
    }


def search_lite(
    conn: sqlite3.Connection,
    *,
    viewer_name: str,
    query: str,
    top_k: int = 5,
    task_type: str | None = None,
    scope: str = "auto",
) -> list[dict[str, Any]]:
    """Backwards-compatible wrapper that returns just the results list.

    For new code use search_lite_with_meta() to also get the personal /
    community / quota breakdown the UI shows.
    """
    return search_lite_with_meta(
        conn,
        viewer_name=viewer_name,
        query=query,
        top_k=top_k,
        task_type=task_type,
        scope=scope,
    )["results"]

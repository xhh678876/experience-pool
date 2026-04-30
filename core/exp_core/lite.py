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

ACL kinds accepted by this module: 'private' | 'team:<X>' | 'public' (alias
for 'org' in the legacy code) | 'org'. Storage normalizes 'public' → 'org'
to avoid duplicating ACL semantics.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import llm
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
    """Lite path accepts 'public' as an alias for 'org'."""
    if acl == "public":
        return "org"
    return acl


def push_lite(
    conn: sqlite3.Connection,
    *,
    rules: RuleSet,
    agent_name: str,
    card: LiteCard,
) -> dict[str, Any]:
    """Insert a lite card. Sanitize again server-side as belt-and-suspenders.

    No judge, no extractor, no credit. The point is: searchable now."""
    cur = conn.execute("SELECT agent_id, team FROM agents WHERE name = ?", (agent_name,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown agent: {agent_name}")
    agent_id = row["agent_id"]

    # Server-side sanitize on every text field of the card.
    redactions = dict(card.redactions)
    server_high = False
    for attr in ("query", "intent", "outcome"):
        val = getattr(card, attr)
        if val:
            cleaned, c, hi = layer1_text(val, rules)
            setattr(card, attr, cleaned)
            for k, v in c.items():
                redactions[k] = redactions.get(k, 0) + v
            server_high = server_high or hi
    cleaned_steps: list[str] = []
    for s in card.steps:
        cleaned, c, hi = layer1_text(s, rules)
        cleaned_steps.append(cleaned)
        for k, v in c.items():
            redactions[k] = redactions.get(k, 0) + v
        server_high = server_high or hi
    card.steps = cleaned_steps

    sanitization_status = (
        "human_review" if server_high else ("flagged" if redactions else "done")
    )
    review_status = "pending" if server_high else "auto_approved"

    eid = str(uuid.uuid4())
    acl = _normalize_acl(card.acl)
    conn.execute(
        """
        INSERT INTO experiences (
            experience_id, agent_id, task_type, source_model,
            query, intent_text, script_steps, outcome, summary,
            sensitivity, acl, tags,
            sanitization_status, review_status, extraction_status,
            ingest_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid, agent_id, card.task_type, card.source_model,
            card.query, card.intent, json.dumps(card.steps), card.outcome,
            card.outcome,  # also fill summary so existing UI shows something
            card.sensitivity, acl, json.dumps(card.tags),
            sanitization_status, review_status, "done",
            "lite",
        ),
    )

    # Single embedding over (intent + query). Steps and outcome are kept
    # in the row but not embedded separately in v0 — pure cosine over
    # this one vector is what search uses.
    text_to_embed = (card.intent + " " + card.query).strip() or card.outcome
    vec = embed(text_to_embed)
    payload = json.dumps({"task_type": card.task_type, "acl": acl})
    conn.execute(
        "INSERT OR REPLACE INTO vectors (experience_id, kind, payload, vector) VALUES (?, ?, ?, ?)",
        (eid, "intent", payload, to_blob(vec)),
    )

    conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
        (agent_name, "agent", "push_lite", eid,
         json.dumps({"redactions": redactions,
                     "sanitization_status": sanitization_status,
                     "task_type": card.task_type})),
    )
    conn.commit()
    return {
        "experience_id": eid,
        "review_status": review_status,
        "sanitization_status": sanitization_status,
        "redactions": redactions,
        "ingest_path": "lite",
    }


# ---------------------------------------------------------------------------
# Pure cosine search with ACL
# ---------------------------------------------------------------------------

def search_lite(
    conn: sqlite3.Connection,
    *,
    viewer_name: str,
    query: str,
    top_k: int = 5,
    task_type: str | None = None,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT agent_id, team FROM agents WHERE name = ?", (viewer_name,)
    )
    me = cur.fetchone()
    if me is None:
        raise ValueError(f"unknown agent: {viewer_name}")
    viewer_id, viewer_team = me["agent_id"], me["team"]

    qvec = embed(query)
    cur = conn.execute(
        """
        SELECT v.experience_id, v.vector, e.task_type, e.review_status,
               e.query, e.intent_text, e.script_steps, e.outcome,
               e.acl, e.agent_id, e.created_at, e.ingest_path
        FROM vectors v JOIN experiences e USING(experience_id)
        WHERE v.kind = 'intent'
          AND e.review_status IN ('approved', 'auto_approved', 'edited')
          AND e.extraction_status = 'done'
        """
    )
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in cur.fetchall():
        if task_type and row["task_type"] != task_type:
            continue
        if not can_read(viewer_id, viewer_team, row["agent_id"], row["acl"]):
            continue
        sim = cosine(qvec, from_blob(row["vector"]))
        scored.append((sim, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for sim, row in scored[:top_k]:
        out.append({
            "experience_id": row["experience_id"],
            "query": row["query"],
            "intent": row["intent_text"],
            "steps": json.loads(row["script_steps"] or "[]"),
            "outcome": row["outcome"] or "",
            "task_type": row["task_type"],
            "acl": row["acl"],
            "ingest_path": row["ingest_path"],
            "similarity": sim,
        })
    # Bump visit_count (kept for future Q work; harmless in v0).
    if scored:
        ids = [r["experience_id"] for _, r in scored[:top_k]]
        conn.execute(
            "UPDATE experiences SET visit_count = visit_count + 1 "
            f"WHERE experience_id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()
    return out

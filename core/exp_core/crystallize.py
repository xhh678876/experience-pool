"""Skill crystallization (Ultron-borrowed pattern).

Three components in one file:

1. ClusterAssigner   — every new experience embeds → cosine similarity vs
                       all centroid blobs → assign to nearest cluster
                       (sim ≥ 0.75) or create a new one. Pure-Python; uses
                       the same 256-dim hash embedding the search uses.

2. SkillSynthesizer  — when a cluster reaches MIN_MEMBERS (default 5) and has
                       no skill yet, ask the LLM to synthesize a multi-step
                       workflow skill from member experiences. Re-crystallize
                       on +RE_TRIGGER (default 3) new members, but only
                       publish if the new structure_score is strictly higher.

3. cluster eligibility filter — only experiences with
                       is_memory_eligible=1 are considered for clustering, so
                       low-quality / failed traces don't pollute the skill.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import struct
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

LOG = logging.getLogger(__name__)


CLUSTER_SIM_THRESHOLD = float(os.environ.get("EXP_CLUSTER_SIM_THRESHOLD", "0.75"))
MIN_MEMBERS_TO_CRYSTALLIZE = int(os.environ.get("EXP_CLUSTER_MIN_MEMBERS", "5"))
RECRYSTALLIZE_AFTER = int(os.environ.get("EXP_CLUSTER_RECRYSTALLIZE_AFTER", "3"))


# ---------------------------------------------------------------------------
# Vector helpers — match the format used in lite.py / mvp-queries.ts
# ---------------------------------------------------------------------------

VECTOR_DIM = 256


def _vec_from_blob(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    n = min(VECTOR_DIM, len(blob) // 4)
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (na * nb)


def _mean(vecs: list[list[float]]) -> list[float]:
    if not vecs:
        return [0.0] * VECTOR_DIM
    n = len(vecs[0])
    out = [0.0] * n
    for v in vecs:
        for i in range(n):
            out[i] += v[i]
    return [x / len(vecs) for x in out]


# ---------------------------------------------------------------------------
# Cluster assignment
# ---------------------------------------------------------------------------

def assign_to_cluster(conn: sqlite3.Connection, *, experience_id: str
                      ) -> dict[str, Any] | None:
    """Pull this experience's vector, find best cluster (or create new),
    update centroid, bump member counter. Returns dict with action info.
    """
    # Eligibility gate — only memory-eligible experiences get clustered.
    row = conn.execute(
        """SELECT is_memory_eligible, intent_text, task_type
           FROM experiences WHERE experience_id=?""",
        (experience_id,),
    ).fetchone()
    if not row or not row[0]:
        return {"skipped": True, "reason": "not memory_eligible"}

    vrow = conn.execute(
        "SELECT vector FROM vectors WHERE experience_id=? AND kind='intent' LIMIT 1",
        (experience_id,),
    ).fetchone()
    if not vrow or not vrow[0]:
        return {"skipped": True, "reason": "no vector"}
    vec = _vec_from_blob(vrow[0])
    if not vec:
        return {"skipped": True, "reason": "bad vector"}

    # Already in a cluster?
    pre = conn.execute(
        "SELECT cluster_id FROM cluster_membership WHERE experience_id=? LIMIT 1",
        (experience_id,),
    ).fetchone()
    if pre:
        return {"skipped": True, "reason": "already-clustered", "cluster_id": pre[0]}

    # Find best matching cluster.
    best_cid: str | None = None
    best_sim: float = -1.0
    for cid, blob in conn.execute(
        "SELECT cluster_id, centroid_blob FROM knowledge_clusters",
    ):
        cv = _vec_from_blob(blob)
        if not cv:
            continue
        sim = _cosine(vec, cv)
        if sim > best_sim:
            best_sim = sim
            best_cid = cid

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if best_cid is not None and best_sim >= CLUSTER_SIM_THRESHOLD:
        # Add to existing cluster + recompute centroid as mean of all members.
        member_vecs: list[list[float]] = [vec]
        for (b,) in conn.execute(
            """SELECT v.vector FROM vectors v
               JOIN cluster_membership m ON m.experience_id = v.experience_id
               WHERE m.cluster_id=? AND v.kind='intent'""",
            (best_cid,),
        ):
            cv = _vec_from_blob(b)
            if cv:
                member_vecs.append(cv)
        new_centroid = _mean(member_vecs)
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO cluster_membership
                   (cluster_id, experience_id, similarity) VALUES (?, ?, ?)""",
                (best_cid, experience_id, best_sim),
            )
            conn.execute(
                """UPDATE knowledge_clusters
                   SET centroid_blob=?, member_count=member_count+1,
                       new_since_crystallize=new_since_crystallize+1,
                       updated_at=?
                   WHERE cluster_id=?""",
                (_vec_to_blob(new_centroid), now, best_cid),
            )
        return {"cluster_id": best_cid, "similarity": best_sim, "action": "joined"}

    # No suitable cluster — start a new one with this experience.
    new_cid = str(uuid.uuid4())
    label = (row[1] or row[2] or "")[:80] if row[1] or row[2] else None
    with conn:
        conn.execute(
            """INSERT INTO knowledge_clusters
               (cluster_id, label, centroid_blob, centroid_dim, member_count,
                new_since_crystallize, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, ?)""",
            (new_cid, label, _vec_to_blob(vec), len(vec), now),
        )
        conn.execute(
            """INSERT INTO cluster_membership (cluster_id, experience_id, similarity)
               VALUES (?, ?, 1.0)""",
            (new_cid, experience_id),
        )
    return {"cluster_id": new_cid, "similarity": 1.0, "action": "created"}


# ---------------------------------------------------------------------------
# Skill synthesis (LLM)
# ---------------------------------------------------------------------------

CRYSTALLIZE_SYSTEM = (
    "You synthesize a reusable multi-step WORKFLOW SKILL from a list of "
    "related task experiences. Each experience is a (intent, outcome, "
    "structural metrics) tuple from a real session. The cluster centroid "
    "indicates these tasks share a semantic region.\n\n"
    "Output STRICT JSON, no preamble, no markdown:\n"
    "{\n"
    '  "name": "<kebab-case slug, max 40 chars>",\n'
    '  "trigger": "<when to use this skill>",\n'
    '  "steps": [\n'
    '    {"step": "<short imperative>", "why": "<reason>", "how": "<concrete>"},\n'
    "    ...\n"
    "  ],\n"
    '  "edge_cases": ["<failure mode + fallback>", ...],\n'
    '  "branches": ["<distinct path for distinct case>", ...],\n'
    '  "structure_score": <float in [0, 1]; quality of THIS skill structure>,\n'
    '  "quality": "good" | "insufficient"\n'
    "}\n\n"
    "Rules:\n"
    "- At least 3 distinct steps; each with concrete inputs/outputs\n"
    "- If member experiences are too scattered to form a coherent workflow, "
    "return quality:\"insufficient\" and an empty steps array\n"
    "- structure_score reflects: step coverage, edge handling, branch logic, "
    "specificity (not generic platitudes)"
)


def _llm_chat(system: str, user: str, max_tokens: int = 2000) -> tuple[str, dict]:
    """OpenAI-compat chat call using the same auto-label backend env."""
    base = os.environ.get("EXP_AUTO_LABEL_BASE_URL", "").rstrip("/")
    key = os.environ.get("EXP_AUTO_LABEL_API_KEY", "")
    model = os.environ.get("EXP_AUTO_LABEL_MODEL", "")
    if not (base and key and model):
        raise RuntimeError("crystallize requires EXP_AUTO_LABEL_* env")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        d = json.loads(resp.read())
    text = (d.get("choices", [{}])[0].get("message", {}).get("content", ""))
    usage = d.get("usage", {}) or {}
    return text, {
        "prompt": int(usage.get("prompt_tokens", 0)),
        "completion": int(usage.get("completion_tokens", 0)),
        "model": model,
    }


def _format_member_for_prompt(rec: Any) -> str:
    # Tolerate tuple-cursor: rec is (experience_id, intent_text, task_type, outcome, structural_metrics).
    if isinstance(rec, sqlite3.Row):
        intent = rec["intent_text"]
        task_type = rec["task_type"]
        outcome = rec["outcome"]
        sm_raw = rec["structural_metrics"]
    else:
        # tuple
        _, intent, task_type, outcome, sm_raw = rec
    parts = [f"intent: {intent or '(none)'}",
             f"task_type: {task_type}"]
    if outcome:
        parts.append(f"outcome: {outcome[:240]}")
    if sm_raw:
        try:
            sm = json.loads(sm_raw)
            parts.append(
                f"metrics: turns={sm.get('turn_count')} "
                f"tools={sm.get('tool_call_count')} "
                f"errors={sm.get('tool_error_count')} "
                f"score={sm.get('structural_score')}"
            )
        except Exception:
            pass
    return "- " + " | ".join(parts)


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None


def crystallize_cluster(conn: sqlite3.Connection, *, cluster_id: str
                        ) -> dict[str, Any]:
    """Generate (or upgrade) a workflow skill for the given cluster."""
    cluster = conn.execute(
        """SELECT cluster_id, label, member_count, crystallized_skill_id,
                  last_structure_score
           FROM knowledge_clusters WHERE cluster_id=?""",
        (cluster_id,),
    ).fetchone()
    if not cluster:
        return {"error": "cluster not found"}
    # Tolerate either tuple-cursor or Row-cursor.
    def _g(row, name, idx):
        try:
            return row[name]
        except (TypeError, IndexError, KeyError):
            return row[idx]
    member_count = _g(cluster, "member_count", 2)
    crystallized_skill_id = _g(cluster, "crystallized_skill_id", 3)
    last_structure_score = _g(cluster, "last_structure_score", 4)
    label = _g(cluster, "label", 1)

    # Use threshold from env at call time, not module load time.
    min_members = int(os.environ.get("EXP_CLUSTER_MIN_MEMBERS",
                                     str(MIN_MEMBERS_TO_CRYSTALLIZE)))
    if member_count < min_members:
        return {"skipped": True, "reason": "not enough members",
                "have": member_count, "need": min_members}

    members = conn.execute(
        """SELECT e.experience_id, e.intent_text, e.task_type, e.outcome,
                  e.structural_metrics
           FROM cluster_membership m
           JOIN experiences e ON e.experience_id = m.experience_id
           WHERE m.cluster_id=?
           ORDER BY m.added_at ASC""",
        (cluster_id,),
    ).fetchall()
    member_block = "\n".join(_format_member_for_prompt(m) for m in members)

    is_recrystallize = crystallized_skill_id is not None
    user_prompt = (
        f"Cluster: {label or '(unlabeled)'}\n"
        f"Members ({len(members)}):\n{member_block}\n"
    )
    if is_recrystallize:
        old_skill = conn.execute(
            "SELECT name, content FROM crystallized_skills WHERE skill_id=?",
            (crystallized_skill_id,),
        ).fetchone()
        if old_skill and old_skill[1]:
            user_prompt += (
                f"\n\nThis is a re-crystallization. The current skill is:\n"
                f"{old_skill[1][:1500]}\n\n"
                f"Make TARGETED edits if member experiences add real new "
                f"knowledge. If they don't, return quality:\"insufficient\"."
            )

    try:
        text, usage = _llm_chat(CRYSTALLIZE_SYSTEM, user_prompt, max_tokens=2500)
    except Exception as e:
        return {"error": f"llm call failed: {e}", "cluster_id": cluster_id}

    parsed = _extract_json(text)
    if not parsed:
        return {"error": "non-json response", "raw": text[:200],
                "cluster_id": cluster_id, "usage": usage}
    if parsed.get("quality") == "insufficient":
        return {"skipped": True, "reason": "llm said insufficient",
                "cluster_id": cluster_id, "usage": usage}

    new_score = float(parsed.get("structure_score", 0.0))
    old_score = float(last_structure_score or 0.0)
    if is_recrystallize and new_score <= old_score:
        return {
            "skipped": True,
            "reason": "structure_score did not improve",
            "old": old_score,
            "new": new_score,
            "cluster_id": cluster_id,
            "usage": usage,
        }

    skill_content = json.dumps(parsed, ensure_ascii=False, indent=2)
    skill_name = (parsed.get("name") or
                  (label or "skill")[:40].lower().replace(" ", "-"))

    # Persist into a generic table — store skill rows even if there isn't a
    # full M9 skills schema yet. Use a side table reserved for crystallized
    # skills to avoid colliding with M9 skill bundles.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS crystallized_skills (
              skill_id      TEXT PRIMARY KEY,
              cluster_id    TEXT NOT NULL,
              name          TEXT NOT NULL,
              version       INTEGER NOT NULL DEFAULT 1,
              content       TEXT NOT NULL,
              structure_score REAL NOT NULL,
              member_count  INTEGER NOT NULL,
              created_at    TEXT NOT NULL DEFAULT (datetime('now')),
              superseded_by TEXT
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_csk_cluster ON crystallized_skills(cluster_id)")

    skill_id = str(uuid.uuid4())
    version = 1
    if is_recrystallize:
        prior = conn.execute(
            "SELECT skill_id, version FROM crystallized_skills WHERE cluster_id=? ORDER BY version DESC LIMIT 1",
            (cluster_id,),
        ).fetchone()
        if prior:
            version = (prior[1] or 0) + 1
            conn.execute(
                "UPDATE crystallized_skills SET superseded_by=? WHERE skill_id=?",
                (skill_id, prior[0]),
            )
    with conn:
        conn.execute(
            """INSERT INTO crystallized_skills
               (skill_id, cluster_id, name, version, content,
                structure_score, member_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (skill_id, cluster_id, skill_name, version, skill_content,
             new_score, member_count),
        )
        conn.execute(
            """UPDATE knowledge_clusters
               SET crystallized_skill_id=?, last_structure_score=?,
                   last_crystallized_at=datetime('now'),
                   new_since_crystallize=0
               WHERE cluster_id=?""",
            (skill_id, new_score, cluster_id),
        )
    return {
        "cluster_id": cluster_id,
        "skill_id": skill_id,
        "name": skill_name,
        "version": version,
        "structure_score": new_score,
        "previous_score": old_score,
        "members": member_count,
        "usage": usage,
    }


def maybe_crystallize(conn: sqlite3.Connection, *, cluster_id: str
                      ) -> dict[str, Any] | None:
    """Decide whether to call crystallize_cluster based on counts."""
    row = conn.execute(
        """SELECT member_count, new_since_crystallize, crystallized_skill_id
           FROM knowledge_clusters WHERE cluster_id=?""",
        (cluster_id,),
    ).fetchone()
    if not row:
        return None
    member_count = row[0] or 0
    new_since = row[1] or 0
    has_skill = row[2] is not None

    if not has_skill and member_count >= MIN_MEMBERS_TO_CRYSTALLIZE:
        return crystallize_cluster(conn, cluster_id=cluster_id)
    if has_skill and new_since >= RECRYSTALLIZE_AFTER:
        return crystallize_cluster(conn, cluster_id=cluster_id)
    return None


def cluster_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT COUNT(*) AS n,
                  SUM(CASE WHEN crystallized_skill_id IS NOT NULL THEN 1 ELSE 0 END) AS crystallized,
                  AVG(member_count) AS avg_members,
                  MAX(member_count) AS max_members
           FROM knowledge_clusters"""
    ).fetchone()
    skill_row = conn.execute(
        "SELECT COUNT(*) FROM crystallized_skills"
    ).fetchone() if _has_table(conn, "crystallized_skills") else (0,)
    return {
        "clusters": row[0] or 0,
        "crystallized": row[1] or 0,
        "avg_members": round(row[2] or 0.0, 2),
        "max_members": row[3] or 0,
        "skills": skill_row[0] or 0,
    }


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None

"""ExperiencePool: standalone in-process implementation.

End-to-end loop:
  push(trajectory, parents=[...]) ->
    1. write trajectory to disk
    2. insert experience row
    3. record parent edges
    4. extract: double-run prompts, structural diff -> 'unstable' status
    5. judge: cost-aware routing (low+short -> 1 shot, else 3-shot median)
    6. embed intent + script, write vectors
    7. dedup: intent && script both above threshold -> merge into winner
    8. credit-assign one hop: update each parent's Q based on this child's reward

search(q, ...) -> mixed score (similarity z + Q z + UCB)

This is a single-file, single-process loop. The FastAPI gateway and Redis-driven
workers in /gateway and /workers are the same logic with infra split out.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import statistics
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import llm, prompts
from .embeddings import DIM, cosine, embed, from_blob, to_blob
from .ranking import Candidate, q_scalar, score_candidates
from .sanitize import RuleSet, load_rules, sanitize_trajectory
from .schema import LITE_MIGRATIONS, SCHEMA

EXTRACTOR_VERSION = "extractor-v1"
JUDGE_VERSION = "judge-v1"

EXTRACTION_DIVERGENCE_THRESHOLD = 0.5
INTENT_SIM_THRESHOLD = 0.85
SCRIPT_SIM_THRESHOLD = 0.78

ALPHA = 0.2  # credit assignment learning rate
DIMS = ("outcome", "intent", "execution", "orchestration", "expression")
LOG = logging.getLogger(__name__)


@dataclass
class PoolConfig:
    root: Path = field(default_factory=lambda: Path.home() / ".experience-pool")
    db_path_override: Path | None = None
    trajectories_dir_override: Path | None = None
    w_similarity: float = 0.55
    w_q: float = 0.35
    c_exploration: float = 0.10
    short_traj_turns: int = 4
    ensemble_reuse_threshold: int = 10

    @classmethod
    def from_env(cls) -> "PoolConfig":
        root = Path(
            os.getenv("EXP_ROOT", str(Path.home() / ".experience-pool"))
        ).expanduser()
        db_path = os.getenv("EXP_DB_PATH")
        trajectories_dir = os.getenv("EXP_TRAJECTORIES_DIR")
        return cls(
            root=root,
            db_path_override=Path(db_path).expanduser() if db_path else None,
            trajectories_dir_override=(
                Path(trajectories_dir).expanduser() if trajectories_dir else None
            ),
        )

    @property
    def db_path(self) -> Path:
        return self.db_path_override or self.root / "pool.db"

    @property
    def trajectories_dir(self) -> Path:
        return self.trajectories_dir_override or self.root / "trajectories"


def _new_id() -> str:
    return str(uuid.uuid4())


def _normalize_ingest_acl(acl: str) -> str:
    acl = (acl or "private").strip()
    if acl.startswith("team:"):
        return acl
    if acl == "private":
        return "private"
    # Raw push must not create community-visible rows. Publishing sets
    # acl='public' only after strict-public sanitize succeeds.
    return "private"


def _sanitizer_blocks_review(*, acl: str, sanitization_status: str) -> bool:
    """Private rows are sanitized and audited, but not held out of recall."""
    return sanitization_status == "human_review" and acl != "private"


class ExperiencePool:
    def __init__(
        self,
        config: PoolConfig | None = None,
        *,
        sanitize_rules: RuleSet | None = None,
    ):
        self.config = config or PoolConfig.from_env()
        self.config.root.mkdir(parents=True, exist_ok=True)
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.trajectories_dir.mkdir(parents=True, exist_ok=True)
        # `timeout=` raises sqlite's default 5s busy timeout to 30s so concurrent
        # writers across uvicorn workers wait their turn instead of erroring out
        # with "database is locked" under load.
        self.conn = sqlite3.connect(self.config.db_path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._apply_lite_migrations()
        # These schemas are part of the production contract. Failing startup is
        # safer than serving a partially initialized pool that accepts uploads
        # but cannot authenticate, index, or revoke them correctly.
        from . import api_keys as _api_keys
        from . import projects as _projects
        from . import quality as _q
        from . import rag as _rag
        from . import reuse_feedback as _reuse_feedback
        from . import users as _users

        _users.init_schema(self.conn)
        _api_keys.init_schema(self.conn)
        _q.ensure_quality_columns(self.conn)
        _projects.init_schema(self.conn)
        _rag.ensure_schema(self.conn)
        _reuse_feedback.ensure_schema(self.conn)
        self.conn.commit()
        # Load rules once at startup; tests can inject a custom RuleSet.
        self._sanitize_rules = sanitize_rules or load_rules()

    def _apply_lite_migrations(self) -> None:
        """Idempotently add columns introduced after the original schema."""
        for table, column_def in LITE_MIGRATIONS:
            col_name = column_def.split()[0]
            cur = self.conn.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
            if col_name in existing:
                continue
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")

    # ----- agents -----
    def register_agent(self, name: str, team: str) -> str:
        cur = self.conn.execute("SELECT agent_id FROM agents WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            return row["agent_id"]
        agent_id = _new_id()
        self.conn.execute(
            "INSERT INTO agents (agent_id, name, team) VALUES (?, ?, ?)",
            (agent_id, name, team),
        )
        self.conn.commit()
        return agent_id

    def agent_exists(self, name: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM agents WHERE name = ?", (name,))
        return cur.fetchone() is not None

    def _agent_id(self, name: str) -> str:
        cur = self.conn.execute("SELECT agent_id FROM agents WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown agent: {name}")
        return row["agent_id"]

    # ----- push -----
    def push(
        self,
        agent_name: str,
        task_type: str,
        source_model: str,
        trajectory: list[dict[str, Any]],
        *,
        parent_experience_ids: list[str] | None = None,
        uses_skills: list[str] | None = None,
        sensitivity: str = "medium",
        acl: str = "private",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        from . import skills as skills_mod  # lazy to avoid import cycles

        agent_id = self._agent_id(agent_name)
        acl = _normalize_ingest_acl(acl)
        eid = _new_id()
        parents = parent_experience_ids or []
        skill_names = uses_skills or []
        traj_path = self.config.trajectories_dir / f"{eid}.json"
        traj_path.write_text(json.dumps({"trajectory": trajectory}, ensure_ascii=False))

        self.conn.execute(
            """
            INSERT INTO experiences (
                experience_id, agent_id, task_type, source_model, sensitivity, acl, tags,
                trajectory_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (eid, agent_id, task_type, source_model, sensitivity, acl,
             json.dumps(tags or []), str(traj_path)),
        )
        for parent_id in parents:
            self.conn.execute(
                "INSERT OR IGNORE INTO experience_edges (parent_id, child_id) VALUES (?, ?)",
                (parent_id, eid),
            )
            self.conn.execute(
                "UPDATE experiences SET reuse_count = reuse_count + 1 WHERE experience_id = ?",
                (parent_id,),
            )
        # Resolve declared skill usage and bump invoke_count immediately.
        # If the agent referenced a non-existent or unapproved skill, it's silently
        # dropped — typos shouldn't fail the whole push.
        resolved_skill_ids: list[str] = []
        if skill_names:
            resolved_skill_ids = skills_mod.link_experience_uses(
                self.conn, eid, skill_names,
            )

        self._audit(agent_name, "agent", "push", eid,
                    {"task_type": task_type, "parents": parents,
                     "skills_declared": skill_names,
                     "skills_resolved": resolved_skill_ids,
                     "sensitivity": sensitivity})
        self.conn.commit()

        # ---- Sanitize: between trajectory write and extraction. ----
        # The extractor must never see the raw trajectory if Layer 1/2/3
        # surfaced anything, so we mutate `trajectory` to the sanitized copy.
        # If anything was redacted we keep the original on disk as
        # `<eid>.raw.json` for reviewer audit.
        sanitized_trajectory = self._run_sanitizer(eid, trajectory, sensitivity, traj_path, acl)
        trajectory = sanitized_trajectory  # downstream stages use the cleaned copy

        # Inline pipeline.
        self._extract(eid, trajectory)
        row = self._get(eid)
        if row["extraction_status"] != "done":
            return self._serialize(row)

        card = self._card_from_row(row)
        self._judge_and_persist(eid, trajectory, card, sensitivity)

        self._embed_and_index(eid, card, task_type=task_type, acl=acl)
        rag_indexed = False
        rag_index_error: str | None = None
        try:
            from . import rag as rag_mod
            rag_mod.ensure_schema(self.conn)
            rag_mod.rebuild_experience(self.conn, eid)
            rag_indexed = True
        except Exception as exc:
            rag_index_error = str(exc)[:300]
            LOG.exception("RAG indexing failed for experience %s", eid)

        # Credit is applied BEFORE dedup. The parents earned the credit because
        # the agent declared they used them, regardless of whether the resulting
        # work happens to duplicate something already in the pool. Same logic
        # applies to skills the agent declared they invoked.
        self._apply_credit(eid)
        skills_mod.apply_skill_credit(self.conn, eid, alpha=ALPHA)

        self._maybe_merge(eid, card, task_type=task_type)
        result = self._serialize(self._get(eid))
        result["rag_indexed"] = rag_indexed
        result["rag_index_error"] = rag_index_error
        return result

    # ----- sanitize -----
    def _run_sanitizer(
        self,
        eid: str,
        trajectory: list[dict[str, Any]],
        sensitivity: str,
        traj_path: Path,
        acl: str,
    ) -> list[dict[str, Any]]:
        """Run the three-layer sanitizer.

        Returns the sanitized trajectory. Mutates the experiences row's
        sanitization_status (and review_status when human review is required)
        and overwrites the on-disk trajectory with the sanitized copy. The
        original is preserved at `<eid>.raw.json` if anything changed.
        """
        result = sanitize_trajectory(
            trajectory, sensitivity=sensitivity, rules=self._sanitize_rules
        )
        if result.changed or result.layer2_findings or result.layer3:
            # Preserve the raw trajectory for reviewer audit before overwriting.
            raw_path = traj_path.with_suffix(".raw.json")
            raw_path.write_text(
                json.dumps({"trajectory": trajectory}, ensure_ascii=False)
            )
            traj_path.write_text(
                json.dumps({"trajectory": result.sanitized}, ensure_ascii=False)
            )

        self.conn.execute(
            "UPDATE experiences SET sanitization_status = ? WHERE experience_id = ?",
            (result.status, eid),
        )
        # Block auto-approval when human review is required. The judge stage
        # may later relax to `auto_approved`; we override that back to pending
        # at the end of the pipeline if status was human_review.
        if _sanitizer_blocks_review(acl=acl, sanitization_status=result.status):
            self.conn.execute(
                "UPDATE experiences SET review_status = 'pending' WHERE experience_id = ?",
                (eid,),
            )
        self._audit(
            "sanitizer",
            "system",
            "sanitize",
            eid,
            result.to_metadata(),
        )
        self.conn.commit()
        return result.sanitized

    # ----- extract -----
    def _extract(self, eid: str, trajectory: list[dict[str, Any]]) -> None:
        # Run prompts A and B independently so a transient failure on one
        # (the claude CLI occasionally returns non-JSON) doesn't kill the
        # whole extraction. Only fail if both raise.
        sys_a, p_a = prompts.render_extractor("A", trajectory)
        sys_b, p_b = prompts.render_extractor("B", trajectory)
        card_a: dict[str, Any] | None = None
        card_b: dict[str, Any] | None = None
        errors: list[str] = []
        try:
            card_a = llm.call_json(p_a, system=sys_a)
        except Exception as e:  # noqa: BLE001
            errors.append(f"A: {e}")
        try:
            card_b = llm.call_json(p_b, system=sys_b)
        except Exception as e:  # noqa: BLE001
            errors.append(f"B: {e}")

        if card_a is None and card_b is None:
            self.conn.execute(
                "UPDATE experiences SET extraction_status = 'failed' WHERE experience_id = ?",
                (eid,),
            )
            self._audit("extractor", "system", "extract_failed", eid, {"errors": errors})
            self.conn.commit()
            return

        # If only one survived, single-shot. Divergence is meaningless here.
        if card_a is None or card_b is None:
            chosen = card_a or card_b
            assert chosen is not None
            div = 0.0
            status = "done"
            self._audit("extractor", "system", "extract_partial",
                        eid, {"errors": errors})
        else:
            div = _divergence(card_a, card_b)
            chosen = card_a if card_a.get("confidence", 0) >= card_b.get("confidence", 0) else card_b
            status = "unstable" if div >= EXTRACTION_DIVERGENCE_THRESHOLD else "done"

        self.conn.execute(
            """
            UPDATE experiences SET
              intent_text = ?, preconditions = ?, script_steps = ?, tool_capabilities = ?,
              key_decisions = ?, pitfalls = ?, summary = ?, extraction_status = ?
            WHERE experience_id = ?
            """,
            (
                chosen.get("intent_text", ""),
                chosen.get("preconditions", ""),
                json.dumps(chosen.get("script_steps", [])),
                json.dumps(chosen.get("tool_capabilities", [])),
                json.dumps(chosen.get("key_decisions", [])),
                json.dumps(chosen.get("pitfalls", [])),
                chosen.get("summary", ""),
                status,
                eid,
            ),
        )
        self._audit("extractor", "system", "extract", eid,
                    {"divergence": div, "status": status})
        self.conn.commit()

    # ----- judge -----
    def _judge_and_persist(
        self, eid: str, trajectory: list[dict[str, Any]], card: dict[str, Any],
        sensitivity: str,
    ) -> None:
        row = self._get(eid)
        reuse = row["reuse_count"] or 0
        traj_turns = len(trajectory)

        if sensitivity == "high" or reuse >= self.config.ensemble_reuse_threshold:
            shots, ensemble = 3, True
            primary = "claude-sonnet-4-6"
        elif sensitivity == "low" and traj_turns <= self.config.short_traj_turns:
            shots, ensemble = 1, False
            primary = "claude-haiku-4-5-20251001"
        else:
            shots, ensemble = 3, False
            primary = "claude-haiku-4-5-20251001"

        sys_p, p = prompts.render_judge(trajectory, card)
        runs: list[dict[str, Any]] = []
        for _ in range(shots):
            try:
                runs.append(llm.call_json(p, system=sys_p, model=primary))
            except Exception as e:  # noqa: BLE001
                self._audit("judge", "system", "judge_shot_failed", eid, {"error": str(e)})
        if not runs:
            self.conn.execute(
                "UPDATE experiences SET extraction_status = 'failed' WHERE experience_id = ?",
                (eid,),
            )
            self.conn.commit()
            return

        agg = {
            d: statistics.median(_clip(r.get(d, 0.0)) for r in runs) for d in DIMS
        }
        confidence = statistics.median(_clip(r.get("confidence", 0.5), lo=0.0) for r in runs)
        variance = (
            statistics.mean(statistics.variance([r.get(d, 0.0) for r in runs]) for d in DIMS)
            if len(runs) >= 2
            else 0.0
        )

        unstable = variance > 0.05
        if ensemble:
            try:
                check = llm.call_json(p, system=sys_p, model="claude-sonnet-4-6")
                disagree = statistics.mean(abs(agg[d] - _clip(check.get(d, 0.0))) for d in DIMS)
                if disagree > 0.4:
                    confidence *= 0.6
                    unstable = True
            except Exception:  # noqa: BLE001
                pass

        rationale = "; ".join(r.get("rationale", "") for r in runs)
        self.conn.execute(
            """
            INSERT INTO rewards (
                reward_id, experience_id, judge_version, judge_model,
                r_outcome, r_intent, r_execution, r_orchestration, r_expression,
                confidence, self_consistency_variance, is_unstable, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_new_id(), eid, JUDGE_VERSION, primary,
             agg["outcome"], agg["intent"], agg["execution"], agg["orchestration"],
             agg["expression"], confidence, variance, 1 if unstable else 0, rationale),
        )
        # If the sanitizer routed this experience to human review, keep it
        # pending regardless of judge stability.
        sanitization_status = self._get(eid)["sanitization_status"]
        acl = self._get(eid)["acl"]
        if _sanitizer_blocks_review(acl=acl, sanitization_status=sanitization_status):
            review_status = "pending"
        elif unstable:
            review_status = "pending"
        else:
            review_status = "auto_approved"

        self.conn.execute(
            """
            UPDATE experiences SET
              q_outcome = ?, q_intent = ?, q_execution = ?, q_orchestration = ?,
              q_expression = ?, q_update_count = 1,
              review_status = ?
            WHERE experience_id = ?
            """,
            (agg["outcome"], agg["intent"], agg["execution"], agg["orchestration"],
             agg["expression"],
             review_status,
             eid),
        )
        self.conn.commit()

    # ----- embed + index -----
    def _embed_and_index(self, eid: str, card: dict[str, Any], *,
                         task_type: str, acl: str) -> None:
        intent_vec = embed(card.get("intent_text", ""))
        script_text = " ".join(
            f"{s.get('step','')} {s.get('why','')} {s.get('how','')}"
            for s in card.get("script_steps", [])
        ) or card.get("intent_text", "")
        script_vec = embed(script_text)
        payload = json.dumps({"task_type": task_type, "acl": acl})
        self.conn.execute(
            "INSERT OR REPLACE INTO vectors (experience_id, kind, payload, vector) VALUES (?, ?, ?, ?)",
            (eid, "intent", payload, to_blob(intent_vec)),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO vectors (experience_id, kind, payload, vector) VALUES (?, ?, ?, ?)",
            (eid, "script", payload, to_blob(script_vec)),
        )
        self.conn.commit()

    # ----- dedup -----
    def _maybe_merge(self, eid: str, card: dict[str, Any], *, task_type: str) -> str | None:
        intent_vec = embed(card.get("intent_text", ""))
        cur = self.conn.execute(
            """
            SELECT v.experience_id, v.vector, e.q_outcome, e.q_intent, e.q_execution,
                   e.q_orchestration, e.q_expression, e.review_status
            FROM vectors v JOIN experiences e USING(experience_id)
            WHERE v.kind = 'intent' AND v.experience_id != ?
              AND e.task_type = ?
              AND e.review_status IN ('approved', 'auto_approved', 'edited')
            """,
            (eid, task_type),
        )
        intent_candidates = []
        for row in cur.fetchall():
            sim = cosine(intent_vec, from_blob(row["vector"]))
            if sim >= INTENT_SIM_THRESHOLD:
                intent_candidates.append((row["experience_id"], sim, row))
        if not intent_candidates:
            return None

        script_text = " ".join(
            f"{s.get('step','')} {s.get('why','')} {s.get('how','')}"
            for s in card.get("script_steps", [])
        ) or card.get("intent_text", "")
        script_vec = embed(script_text)
        for cand_id, _intent_sim, cand_row in intent_candidates:
            cur2 = self.conn.execute(
                "SELECT vector FROM vectors WHERE experience_id = ? AND kind = 'script'",
                (cand_id,),
            )
            row2 = cur2.fetchone()
            if row2 is None:
                continue
            script_sim = cosine(script_vec, from_blob(row2["vector"]))
            if script_sim < SCRIPT_SIM_THRESHOLD:
                continue
            winner = self._pick_winner(eid, cand_id)
            loser = cand_id if winner == eid else eid
            self._merge(winner, loser)
            return winner
        return None

    def _pick_winner(self, a: str, b: str) -> str:
        ra = self._get(a)
        rb = self._get(b)
        sa = (ra["q_outcome"] + ra["q_intent"] + ra["q_execution"]
              + ra["q_orchestration"] + ra["q_expression"])
        sb = (rb["q_outcome"] + rb["q_intent"] + rb["q_execution"]
              + rb["q_orchestration"] + rb["q_expression"])
        return a if sa >= sb else b

    def _merge(self, winner: str, loser: str) -> None:
        loser_row = self._get(loser)
        self.conn.execute(
            """
            UPDATE experiences SET
              visit_count = visit_count + ?,
              reuse_count = reuse_count + ?
            WHERE experience_id = ?
            """,
            (loser_row["visit_count"] or 0, loser_row["reuse_count"] or 0, winner),
        )
        tags = json.loads(loser_row["tags"] or "[]")
        tags.append(f"merged_into:{winner}")
        self.conn.execute(
            "UPDATE experiences SET review_status = 'rejected', tags = ? WHERE experience_id = ?",
            (json.dumps(tags), loser),
        )
        self._audit("dedup", "system", "merge", loser, {"winner": winner})
        self.conn.commit()

    # ----- credit assignment (one-hop) -----
    def _apply_credit(self, child_id: str) -> int:
        cur = self.conn.execute(
            """
            SELECT r_outcome, r_intent, r_execution, r_orchestration, r_expression, confidence
            FROM rewards WHERE experience_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (child_id,),
        )
        reward = cur.fetchone()
        if reward is None:
            return 0
        cur = self.conn.execute(
            "SELECT parent_id FROM experience_edges WHERE child_id = ? AND credit_applied = 0",
            (child_id,),
        )
        edges = cur.fetchall()
        conf = reward["confidence"]
        eff = ALPHA * conf
        n = 0
        for edge in edges:
            parent_id = edge["parent_id"]
            parent = self._get(parent_id)
            if parent is None:
                continue
            new_q = {}
            deltas = {}
            for d in DIMS:
                old = parent[f"q_{d}"] or 0.0
                r = reward[f"r_{d}"]
                new = (1 - eff) * old + eff * r
                new_q[d] = new
                deltas[d] = new - old
            self.conn.execute(
                """
                UPDATE experiences SET
                  q_outcome = ?, q_intent = ?, q_execution = ?, q_orchestration = ?,
                  q_expression = ?, q_update_count = q_update_count + 1
                WHERE experience_id = ?
                """,
                (new_q["outcome"], new_q["intent"], new_q["execution"],
                 new_q["orchestration"], new_q["expression"], parent_id),
            )
            self.conn.execute(
                """
                INSERT INTO q_updates (
                    update_id, experience_id, triggered_by_child, alpha, confidence,
                    delta_outcome, delta_intent, delta_execution,
                    delta_orchestration, delta_expression
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_new_id(), parent_id, child_id, ALPHA, conf,
                 deltas["outcome"], deltas["intent"], deltas["execution"],
                 deltas["orchestration"], deltas["expression"]),
            )
            self.conn.execute(
                "UPDATE experience_edges SET credit_applied = 1 WHERE parent_id = ? AND child_id = ?",
                (parent_id, child_id),
            )
            n += 1
        self._audit("credit_assigner", "system", "credit_applied", child_id, {"parents_updated": n})
        self.conn.commit()
        return n

    # ----- search -----
    def search(
        self,
        agent_name: str,
        query: str,
        *,
        top_k: int = 5,
        task_type: str | None = None,
        sort: str = "score",
        exploration: float | None = None,
    ) -> list[dict[str, Any]]:
        from .fts import escape_query, rank_to_signal

        agent_id = self._agent_id(agent_name)
        qvec = embed(query)

        # FTS5 keyword pass: rank-based signal blended with vector cosine to
        # compensate for the deterministic hash embedding being semantically weak.
        fts_signal: dict[str, float] = {}
        fts_expr = escape_query(query)
        if fts_expr:
            try:
                fts_cur = self.conn.execute(
                    "SELECT experience_id FROM experiences_fts WHERE experiences_fts MATCH ? ORDER BY rank LIMIT 200",
                    (fts_expr,),
                )
                fts_ids = [r["experience_id"] for r in fts_cur.fetchall()]
                for rank, eid in enumerate(fts_ids):
                    fts_signal[eid] = rank_to_signal(rank, len(fts_ids))
            except sqlite3.OperationalError:
                fts_signal = {}

        cur = self.conn.execute(
            """
            SELECT v.experience_id, v.vector, v.payload, e.task_type, e.review_status,
                   e.intent_text, e.summary, e.script_steps, e.tool_capabilities,
                   e.pitfalls, e.q_outcome, e.q_intent, e.q_execution, e.q_orchestration,
                   e.q_expression, e.visit_count, e.reuse_count, e.q_update_count
            FROM vectors v JOIN experiences e USING(experience_id)
            WHERE v.kind = 'intent'
              AND e.extraction_status = 'done'
              AND e.review_status IN ('approved', 'auto_approved', 'edited')
            """
        )
        all_rows = cur.fetchall()
        candidates: list[tuple[Candidate, sqlite3.Row, float]] = []
        VEC_W, FTS_W = 0.7, 0.3
        for row in all_rows:
            if task_type and row["task_type"] != task_type:
                continue
            cos = cosine(qvec, from_blob(row["vector"]))
            fts = fts_signal.get(row["experience_id"], 0.0)
            sim = VEC_W * cos + FTS_W * fts
            cand = Candidate(
                experience_id=row["experience_id"],
                similarity=sim,
                q_outcome=row["q_outcome"] or 0.0,
                q_intent=row["q_intent"] or 0.0,
                q_execution=row["q_execution"] or 0.0,
                q_orchestration=row["q_orchestration"] or 0.0,
                q_expression=row["q_expression"] or 0.0,
                visit_count=row["visit_count"] or 0,
            )
            candidates.append((cand, row, sim))
        if not candidates:
            return []

        c_explore = exploration if exploration is not None else self.config.c_exploration
        ranked = score_candidates(
            [c for c, _, _ in candidates],
            w_similarity=self.config.w_similarity,
            w_q=self.config.w_q,
            c_exploration=c_explore,
        )
        if sort == "similarity":
            ranked.sort(key=lambda x: x[0].similarity, reverse=True)
        elif sort == "q_value":
            ranked.sort(key=lambda x: q_scalar(x[0]), reverse=True)
        ranked = ranked[:top_k]

        by_id = {c.experience_id: r for c, r, _ in candidates}
        hit_ids = [c.experience_id for c, *_ in ranked]
        for hid in hit_ids:
            self.conn.execute(
                "UPDATE experiences SET visit_count = visit_count + 1 WHERE experience_id = ?",
                (hid,),
            )
        for rank, (cand, total, _sim, _qc, _ucb) in enumerate(ranked):
            self.conn.execute(
                """
                INSERT INTO search_log (experience_id, queried_by, query_text, rank, similarity, score)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cand.experience_id, agent_id, query, rank, cand.similarity, total),
            )
        self.conn.commit()

        out = []
        for cand, total, sim_c, q_c, ucb_c in ranked:
            row = by_id[cand.experience_id]
            out.append({
                "experience_id": cand.experience_id,
                "intent_text": row["intent_text"],
                "summary": row["summary"],
                "script": {
                    "steps": json.loads(row["script_steps"] or "[]"),
                    "tool_capabilities": json.loads(row["tool_capabilities"] or "[]"),
                    "pitfalls": json.loads(row["pitfalls"] or "[]"),
                },
                "q_scalar": q_scalar(cand),
                "q_breakdown": {d: getattr(cand, f"q_{d}") for d in DIMS},
                "visit_count": cand.visit_count,
                "reuse_count": row["reuse_count"] or 0,
                "q_update_count": row["q_update_count"] or 0,
                "score": total,
                "score_components": {
                    "similarity": sim_c, "q_value": q_c, "exploration": ucb_c,
                },
            })
        return out

    # ----- helpers -----
    def _get(self, eid: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM experiences WHERE experience_id = ?", (eid,))
        return cur.fetchone()

    def _serialize(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        return {k: row[k] for k in row.keys()}

    def _card_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "intent_text": row["intent_text"] or "",
            "preconditions": row["preconditions"] or "",
            "script_steps": json.loads(row["script_steps"] or "[]"),
            "tool_capabilities": json.loads(row["tool_capabilities"] or "[]"),
            "key_decisions": json.loads(row["key_decisions"] or "[]"),
            "pitfalls": json.loads(row["pitfalls"] or "[]"),
            "summary": row["summary"] or "",
        }

    def _audit(self, actor: str, kind: str, action: str, target_id: str | None,
               payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
            (actor, kind, action, target_id, json.dumps(payload, ensure_ascii=False)),
        )

    # ----- skills -----
    def push_skill(
        self,
        agent_name: str,
        bundle_dir: Path,
        *,
        sensitivity: str = "medium",
        acl: str = "private",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        from . import skills as skills_mod  # lazy

        bundle = skills_mod.build_bundle(
            Path(bundle_dir), self._sanitize_rules, sensitivity=sensitivity,
        )
        return skills_mod.store_skill(
            self.conn,
            bundle_root=self.config.root,
            agent_name=agent_name,
            bundle=bundle,
            acl=acl,
            tags=tags,
            sensitivity=sensitivity,
        )

    def search_skills(
        self,
        query: str,
        *,
        top_k: int = 5,
        viewer_name: str | None = None,
    ) -> list[dict[str, Any]]:
        from . import skills as skills_mod
        return skills_mod.search_skills(
            self.conn, query, top_k=top_k,
            w_similarity=self.config.w_similarity,
            w_q=self.config.w_q,
            c_exploration=self.config.c_exploration,
            viewer_name=viewer_name,
        )

    def install_skill(
        self, name: str, target_dir: Path,
        *, version: str | None = None, agent_name: str = "anonymous",
    ) -> dict[str, Any]:
        from . import skills as skills_mod
        return skills_mod.install_skill(
            self.conn, name, Path(target_dir),
            version=version, agent_name=agent_name,
        )

    def list_skills(self, *, limit: int = 100) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            """
            SELECT skill_id, name, version, description, q_outcome, q_intent,
                   q_execution, q_orchestration, q_expression, q_update_count,
                   invoke_count, install_count, review_status, sanitization_status,
                   acl, sensitivity, created_at
            FROM skills ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def _clip(v: Any, lo: float = -1.0, hi: float = 1.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, f))


def _divergence(a: dict[str, Any], b: dict[str, Any]) -> float:
    fa = (
        len(a.get("script_steps") or []),
        len(a.get("tool_capabilities") or []),
        len(a.get("pitfalls") or []),
    )
    fb = (
        len(b.get("script_steps") or []),
        len(b.get("tool_capabilities") or []),
        len(b.get("pitfalls") or []),
    )
    diff = sum(abs(x - y) for x, y in zip(fa, fb, strict=True))
    return diff / max(sum(fa) + sum(fb), 1)

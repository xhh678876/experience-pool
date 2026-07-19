"""Offline retrieval evaluation for Experience Pool RAG.

This module evaluates the retrieval layer only: given query -> relevant unit
labels, it runs the current chunk ranker and reports Recall@k, MRR, nDCG@k,
and Precision@k. It deliberately does not call the HTTP API, plugin hook, or
LLM generation path.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import rag
from .embeddings import cosine, embed, from_blob


DEFAULT_KS = (1, 3, 5, 10)
UNIT_TYPES = {"do_unit", "dont_unit", "experience_unit"}


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    relevant_chunks: dict[str, float]
    relevant_experiences: dict[str, float]
    source: str = "gold"

    @property
    def relevant_count(self) -> int:
        if self.source.startswith("silver-"):
            # Silver labels may contain duplicate/equivalent chunks from
            # repeated runs. Any one of them satisfies the synthetic query.
            return 1
        return len(self.relevant_chunks) + len(self.relevant_experiences)


def load_gold(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            case = _case_from_obj(obj, default_id=f"case-{line_no}")
            if not case.query:
                raise ValueError(f"{path}:{line_no}: missing query")
            if case.relevant_count <= 0:
                raise ValueError(f"{path}:{line_no}: missing relevant ids")
            cases.append(case)
    return cases


def _case_from_obj(obj: dict[str, Any], *, default_id: str) -> EvalCase:
    return EvalCase(
        case_id=str(obj.get("id") or obj.get("case_id") or default_id),
        query=str(obj.get("query") or obj.get("q") or ""),
        relevant_chunks=_graded_id_map(
            obj.get("relevant_chunk_ids")
            or obj.get("relevant_chunks")
            or obj.get("relevant_unit_ids")
            or obj.get("relevant_units")
            or []
        ),
        relevant_experiences=_graded_id_map(
            obj.get("relevant_experience_ids")
            or obj.get("relevant_experiences")
            or []
        ),
        source=str(obj.get("source") or "gold"),
    )


def _graded_id_map(raw: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if raw is None:
        return out
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, dict):
        raw = [{"id": k, "grade": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str):
            out[item] = 1.0
        elif isinstance(item, dict):
            ident = item.get("chunk_id") or item.get("experience_id") or item.get("id")
            if ident:
                try:
                    grade = float(item.get("grade", item.get("relevance", 1.0)))
                except (TypeError, ValueError):
                    grade = 1.0
                out[str(ident)] = max(0.0, grade)
    return {k: v for k, v in out.items() if k and v > 0}


def build_silver_cases(
    conn: sqlite3.Connection,
    *,
    limit: int = 200,
    unit_types: set[str] | None = None,
    owner: str | None = None,
    per_experience: int = 5,
    min_turn_count: int = 0,
    min_turn_fraction: float = 0.0,
    max_query_chars: int = 320,
) -> list[EvalCase]:
    """Build cheap self-retrieval cases from existing unit chunks.

    This is not a replacement for human/LLM gold labels. It is a fast smoke
    set that catches indexing/ranking regressions: each case asks for the
    situation/action/outcome of one existing unit and expects that unit back.
    """
    rag.ensure_schema(conn)
    unit_types = unit_types or UNIT_TYPES
    params: list[Any] = list(unit_types)
    owner_clause = ""
    if owner:
        owner_clause = "AND c.owner = ?"
        params.append(owner)
    min_turn_count = max(0, int(min_turn_count))
    min_turn_fraction = max(0.0, min(1.0, float(min_turn_fraction)))
    params.extend(
        [min_turn_count, min_turn_fraction, max(1, per_experience), limit]
    )
    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT c.chunk_id, c.experience_id, c.chunk_type, c.text, c.search_text,
                 c.turn_start, c.turn_end, c.meta_json, c.created_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY c.experience_id
                   ORDER BY COALESCE(c.turn_start, -1) DESC, c.chunk_id
                 ) AS experience_rank
          FROM rag_chunks c
          JOIN experiences e ON e.experience_id = c.experience_id
          WHERE c.chunk_type IN ({','.join('?' * len(unit_types))})
            {owner_clause}
            AND COALESCE(e.turn_count, 0) >= ?
            AND COALESCE(c.turn_start, 0) >= COALESCE(e.turn_count, 0) * ?
        )
        SELECT chunk_id, experience_id, chunk_type, text, search_text,
               turn_start, turn_end, meta_json
        FROM ranked
        WHERE experience_rank <= ?
        ORDER BY created_at DESC, chunk_id
        LIMIT ?
        """,
        params,
    ).fetchall()
    universe_params: list[Any] = list(unit_types)
    universe_owner_clause = ""
    if owner:
        universe_owner_clause = "AND owner = ?"
        universe_params.append(owner)
    universe = conn.execute(
        f"""
        SELECT chunk_id, chunk_type, text, search_text
        FROM rag_chunks
        WHERE chunk_type IN ({','.join('?' * len(unit_types))})
          {universe_owner_clause}
        """,
        universe_params,
    ).fetchall()
    by_action: dict[str, set[str]] = {}
    by_search_text: dict[tuple[str, str], set[str]] = {}
    for item in universe:
        signature = rag._action_signature(item["text"], item["chunk_type"])  # noqa: SLF001
        if signature:
            by_action.setdefault(signature, set()).add(str(item["chunk_id"]))
        search_key = (str(item["chunk_type"]), str(item["search_text"] or ""))
        if search_key[1]:
            by_search_text.setdefault(search_key, set()).add(str(item["chunk_id"]))

    cases: list[EvalCase] = []
    source = (
        "silver-long-tail"
        if min_turn_count > 0 or min_turn_fraction > 0
        else "silver-self"
    )
    for idx, row in enumerate(rows, start=1):
        query = _query_from_unit_text(
            row["text"], max_chars=max_query_chars
        )
        if not query:
            continue
        action_signature = rag._action_signature(row["text"], row["chunk_type"])  # noqa: SLF001
        equivalents = set(by_action.get(action_signature, set())) if action_signature else set()
        equivalents.update(
            by_search_text.get((str(row["chunk_type"]), str(row["search_text"] or "")), set())
        )
        relevant_chunks = {chunk_id: 1.0 for chunk_id in equivalents}
        if not relevant_chunks:
            relevant_chunks = {row["chunk_id"]: 1.0}
        cases.append(
            EvalCase(
                case_id=f"silver-{idx}-{row['chunk_id'][:8]}",
                query=query,
                relevant_chunks=relevant_chunks,
                relevant_experiences={},
                source=source,
            )
        )
    return cases


def _query_from_unit_text(text: str, *, max_chars: int = 320) -> str:
    fields: dict[str, str] = {}
    labels = ("Situation", "Action", "Outcome", "Keywords")
    for label in labels:
        match = re_search_label(text, label, labels)
        if match:
            fields[label.lower()] = match
    if fields:
        return _compose_eval_query(fields, max_chars=max_chars)

    current: str | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("Situation:"):
            current = "situation"
            fields[current] = line.split(":", 1)[1].strip()
        elif line.startswith("Action:"):
            current = "action"
            fields[current] = line.split(":", 1)[1].strip()
        elif line.startswith("Outcome:"):
            current = "outcome"
            fields[current] = line.split(":", 1)[1].strip()
        elif line.startswith("Keywords:"):
            fields["keywords"] = line.split(":", 1)[1].strip()
            current = None
        elif current and line:
            fields[current] = f"{fields[current]} {line}".strip()
    return _compose_eval_query(fields, max_chars=max_chars)


def _compose_eval_query(fields: dict[str, str], *, max_chars: int) -> str:
    max_chars = max(16, int(max_chars))
    weighted = [
        ("action", 0.38),
        ("keywords", 0.24),
        ("outcome", 0.22),
        ("situation", 0.16),
    ]
    active = [(name, weight) for name, weight in weighted if fields.get(name)]
    if not active:
        return ""
    available = max(1, max_chars - (len(active) - 1))
    weight_total = sum(weight for _, weight in active)
    budgets = [
        max(1, int(available * weight / weight_total))
        for _, weight in active
    ]
    while sum(budgets) < available:
        budgets[sum(budgets) % len(budgets)] += 1
    while sum(budgets) > available:
        index = max(range(len(budgets)), key=budgets.__getitem__)
        budgets[index] -= 1
    parts = []
    for (name, _), budget in zip(active, budgets, strict=True):
        compact = (
            _compact_eval_outcome(fields[name], budget)
            if name == "outcome"
            else (
                _compact_eval_keywords(fields[name], budget)
                if name == "keywords"
                else _compact_eval_field(fields[name], budget)
            )
        )
        parts.append(compact)
    return " ".join(part for part in parts if part)[:max_chars].strip()


def _compact_eval_field(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    separator = " … "
    head = max(1, int(limit * 0.7))
    tail = max(1, limit - head - len(separator))
    return clean[:head].rstrip() + separator + clean[-tail:].lstrip()


def _compact_eval_outcome(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    marker = rag._query_outcome_preference(clean)  # noqa: SLF001
    marker_match = None
    if marker == "success":
        marker_match = re_search_first(
            clean,
            r"\b(?:passed|success(?:ful(?:ly)?)?|succeeded|fixed|resolved|completed|works)\b|成功|通过|已修复|已解决|完成",
        )
    elif marker == "failure":
        marker_match = re_search_first(
            clean,
            r"\b(?:failed|failure|error|exception|traceback|assertionerror|timeout|denied)\b|失败|报错|错误|异常|超时|拒绝",
        )
    compact = _compact_eval_field(clean, limit)
    if not marker_match or marker_match.lower() in compact.lower():
        return compact
    prefix_limit = max(1, limit - len(marker_match) - 1)
    return f"{_compact_eval_field(clean, prefix_limit)} {marker_match}"[:limit]


def _compact_eval_keywords(text: str, limit: int) -> str:
    import re

    words = []
    for token in re.findall(r"[A-Za-z0-9_+#.-]+|[\u4e00-\u9fff]+", text or ""):
        token = token.strip("._-")
        if token and token not in words:
            words.append(token)
    if not words:
        return _compact_eval_field(text, limit)
    chosen_indices = {0, len(words) - 1}
    for index in range(1, len(words) - 1):
        trial = " ".join(
            words[position] for position in sorted(chosen_indices | {index})
        )
        if len(trial) <= limit:
            chosen_indices.add(index)
    compact = " ".join(words[index] for index in sorted(chosen_indices))
    if len(compact) <= limit:
        return compact
    return _compact_eval_field(words[0], limit)


def re_search_first(text: str, pattern: str) -> str | None:
    import re

    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(0) if match else None


def re_search_label(text: str, label: str, labels: tuple[str, ...]) -> str:
    import re

    stops = "|".join(re.escape(f"{other}:") for other in labels if other != label)
    extra_stops = r"Use:|Tools:|Trajectory segment \d+|Experience unit \d+"
    pattern = rf"{re.escape(label)}:\s*(.*?)(?=\s+(?:{stops}|{extra_stops})|$)"
    match = re.search(pattern, text or "", flags=re.S)
    return " ".join(match.group(1).split()) if match else ""


def evaluate(
    conn: sqlite3.Connection,
    cases: list[EvalCase],
    *,
    viewer_name: str | None = None,
    top_k: int = 10,
    ks: tuple[int, ...] = DEFAULT_KS,
    scope: str = "personal",
    task_type: str | None = None,
    exclude_self: bool = False,
) -> dict[str, Any]:
    if not cases:
        return {"case_count": 0, "metrics": {}, "cases": []}
    max_k = max(max(ks), top_k)
    loaded = load_rank_candidates(
        conn,
        viewer_name=viewer_name,
        scope=scope,
        task_type=task_type,
    )
    details: list[dict[str, Any]] = []
    metric_rows = {k: {"recall": [], "precision": [], "ndcg": []} for k in ks}
    mrr_values: list[float] = []
    session_recall_rows = {k: [] for k in ks}
    session_mrr_values: list[float] = []
    parent_by_chunk = {
        str(candidate["chunk_id"]): _candidate_parent_key(candidate)
        for candidate in loaded
    }
    parent_by_experience = {
        str(candidate["experience_id"]): _candidate_parent_key(candidate)
        for candidate in loaded
    }

    for case in cases:
        hits = rank_loaded_chunks(
            conn,
            loaded,
            query=case.query,
            top_k=max_k,
            exclude_chunk_ids=set(case.relevant_chunks) if exclude_self else set(),
        )
        ranked = [_hit_summary(h, case) for h in hits]
        relevance = [_relevance(h, case) for h in hits]
        relevant_sessions = {
            parent_by_chunk[chunk_id]
            for chunk_id in case.relevant_chunks
            if chunk_id in parent_by_chunk
        }
        relevant_sessions.update(
            parent_by_experience[experience_id]
            for experience_id in case.relevant_experiences
            if experience_id in parent_by_experience
        )
        relevant_sessions.update(
            _candidate_parent_key(hit)
            for hit in hits
            if _relevance(hit, case) > 0
        )
        session_relevance = [
            1.0 if _candidate_parent_key(hit) in relevant_sessions else 0.0
            for hit in hits
        ]
        rel_total = max(1, case.relevant_count)
        for k in ks:
            rel_at_k = relevance[:k]
            hits_at_k = sum(1 for r in rel_at_k if r > 0)
            metric_rows[k]["recall"].append(min(1.0, hits_at_k / rel_total))
            metric_rows[k]["precision"].append(hits_at_k / max(1, k))
            metric_rows[k]["ndcg"].append(_ndcg_at_k(rel_at_k, _ideal_grades(case), k))
            session_recall_rows[k].append(
                1.0 if any(session_relevance[:k]) else 0.0
            )
        mrr_values.append(_reciprocal_rank(relevance))
        session_mrr_values.append(_reciprocal_rank(session_relevance))
        details.append(
            {
                "id": case.case_id,
                "source": case.source,
                "query": case.query,
                "relevant_chunk_ids": sorted(case.relevant_chunks),
                "relevant_experience_ids": sorted(case.relevant_experiences),
                "ranked": ranked,
                "first_relevant_rank": _first_relevant_rank(relevance),
                "first_relevant_session_rank": _first_relevant_rank(
                    session_relevance
                ),
            }
        )

    metrics: dict[str, float] = {
        "MRR": _mean(mrr_values),
        "SessionMRR": _mean(session_mrr_values),
    }
    for k in ks:
        metrics[f"Recall@{k}"] = _mean(metric_rows[k]["recall"])
        metrics[f"Precision@{k}"] = _mean(metric_rows[k]["precision"])
        metrics[f"nDCG@{k}"] = _mean(metric_rows[k]["ndcg"])
        metrics[f"SessionRecall@{k}"] = _mean(session_recall_rows[k])
    return {
        "case_count": len(cases),
        "top_k": max_k,
        "ks": list(ks),
        "viewer": viewer_name,
        "scope": scope,
        "metrics": metrics,
        "cases": details,
    }


def rank_chunks(
    conn: sqlite3.Connection,
    *,
    query: str,
    viewer_name: str | None = None,
    top_k: int = 10,
    scope: str = "personal",
    task_type: str | None = None,
    exclude_chunk_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank chunks without mutating visit counts or search logs."""
    loaded = load_rank_candidates(
        conn,
        viewer_name=viewer_name,
        scope=scope,
        task_type=task_type,
    )
    return rank_loaded_chunks(
        conn,
        loaded,
        query=query,
        top_k=top_k,
        exclude_chunk_ids=exclude_chunk_ids or set(),
    )


def load_rank_candidates(
    conn: sqlite3.Connection,
    *,
    viewer_name: str | None = None,
    scope: str = "personal",
    task_type: str | None = None,
) -> list[dict[str, Any]]:
    """Load and ACL-filter candidate chunks once for an evaluation run."""
    from . import community as community_mod

    rag.ensure_schema(conn)
    rag.backfill_missing_chunks(conn, limit=200)
    rag.refresh_stale_retrieval_text(conn)

    viewer_id = viewer_team = viewer_owner = None
    owner_agent_ids: set[str] = set()
    community_unlocked = True
    if viewer_name:
        viewer = conn.execute(
            "SELECT agent_id, team, owner, name FROM agents WHERE name = ?",
            (viewer_name,),
        ).fetchone()
        if viewer is None:
            raise ValueError(f"unknown agent: {viewer_name}")
        viewer_id = viewer["agent_id"]
        viewer_team = viewer["team"]
        viewer_owner = viewer["owner"] or viewer["name"]
        owner_agent_ids = {
            r["agent_id"]
            for r in conn.execute(
                "SELECT agent_id FROM agents WHERE owner = ? OR (owner IS NULL AND name = ?)",
                (viewer_owner, viewer_owner),
            ).fetchall()
        }
        community_unlocked = community_mod.get_quota(conn, viewer_owner).community_unlocked

    requested_scope = scope if scope in {"auto", "personal", "community"} else "personal"
    want_personal = requested_scope in {"auto", "personal"}
    want_community = requested_scope in {"auto", "community"}

    rows = conn.execute(
        """
        SELECT c.chunk_id, c.experience_id, c.owner, c.agent_id, c.chunk_type,
               c.text, COALESCE(c.search_text, c.text) AS search_text,
               c.lexical_terms,
               c.turn_start, c.turn_end, c.meta_json,
               c.token_count, c.quality_score,
               rv.vector,
               e.task_type, e.acl, e.sensitivity, e.created_at,
               COALESCE(e.publish_status, 'private') AS publish_status,
               e.session_id, e.parent_session_id,
               a.name AS agent_name, a.team AS agent_team, a.owner AS agent_owner
        FROM rag_chunks c
        JOIN rag_vectors rv ON rv.chunk_id = c.chunk_id
        JOIN experiences e ON e.experience_id = c.experience_id
        JOIN agents a ON a.agent_id = e.agent_id
        WHERE rv.model = ?
          AND e.review_status IN ('approved', 'auto_approved', 'edited')
          AND e.extraction_status = 'done'
          AND COALESCE(e.revoked, 0) = 0
        """,
        (rag.EMBED_MODEL,),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if task_type and row["task_type"] != task_type:
            continue
        source = "all"
        if viewer_name:
            source = rag._row_source(  # noqa: SLF001
                row,
                viewer_id=str(viewer_id),
                viewer_team=str(viewer_team),
                owner_agent_ids=owner_agent_ids,
                want_personal=want_personal,
                want_community=want_community,
                want_project=False,
                community_unlocked=community_unlocked,
                project_owners={},
            )
            if source is None:
                continue
        candidates.append(
            {
                "chunk_id": row["chunk_id"],
                "experience_id": row["experience_id"],
                "chunk_type": row["chunk_type"],
                "text": row["text"],
                "search_text": row["search_text"],
                "lexical_term_map": rag._decode_lexical_term_map(  # noqa: SLF001
                    row["lexical_terms"]
                ),
                "vector": from_blob(row["vector"]),
                "turn_start": row["turn_start"],
                "turn_end": row["turn_end"],
                "meta": rag._json_obj(row["meta_json"]),  # noqa: SLF001
                "quality": float(row["quality_score"] or 0.0),
                "source": source,
                "session_id": row["session_id"],
                "parent_session_id": row["parent_session_id"] or row["session_id"],
            }
        )
    return candidates


def rank_loaded_chunks(
    conn: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    *,
    query: str,
    top_k: int = 10,
    exclude_chunk_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    exclude_chunk_ids = exclude_chunk_ids or set()
    if not candidates:
        return []
    query_terms = rag._query_terms(query)  # noqa: SLF001
    if not rag._has_retrieval_signal(query, query_terms):  # noqa: SLF001
        return []
    fts_signal = rag._fts_signal(conn, query_terms)  # noqa: SLF001
    qvec = embed(rag._query_text_for_embedding(query, query_terms))  # noqa: SLF001
    scored_vectors: list[tuple[float, int]]
    try:
        import numpy as np

        raw = b"".join(candidate["vector"].tobytes() for candidate in candidates)
        matrix = np.frombuffer(raw, dtype=np.float32).reshape(len(candidates), len(qvec))
        query_array = np.frombuffer(qvec, dtype=np.float32, count=len(qvec))
        scores = np.einsum("ij,j->i", matrix, query_array, optimize=False)
        take = min(rag.VECTOR_PREFETCH_LIMIT, len(candidates))
        if take < len(candidates):
            indices = np.argpartition(scores, len(candidates) - take)[-take:]
        else:
            indices = np.arange(len(candidates))
        scored_vectors = [(float(scores[int(idx)]), int(idx)) for idx in indices]
    except (ImportError, TypeError, ValueError):
        scored_vectors = heapq.nlargest(
            min(rag.VECTOR_PREFETCH_LIMIT, len(candidates)),
            ((cosine(qvec, candidate["vector"]), idx) for idx, candidate in enumerate(candidates)),
        )

    scored_vectors = heapq.nlargest(
        min(rag.VECTOR_CANDIDATE_LIMIT, len(scored_vectors)),
        scored_vectors,
    )

    candidate_indices = {idx for _, idx in scored_vectors}
    candidate_indices.update(
        idx
        for idx, candidate in enumerate(candidates)
        if candidate["chunk_id"] in fts_signal
    )
    cosine_by_index = {idx: score for score, idx in scored_vectors}
    ranked: list[dict[str, Any]] = []
    query_outcome_preference = rag._query_outcome_preference(query)  # noqa: SLF001
    for idx in candidate_indices:
        candidate = candidates[idx]
        if candidate["chunk_id"] in exclude_chunk_ids:
            continue
        cos = cosine_by_index.get(idx)
        if cos is None:
            cos = cosine(qvec, candidate["vector"])
        fts = fts_signal.get(candidate["chunk_id"], 0.0)
        search_text = candidate.get("search_text") or candidate["text"]
        term_map = candidate.get("lexical_term_map") or {}
        lexical, coverage = rag._lexical_metrics(  # noqa: SLF001
            query_terms,
            search_text,
            doc_terms=term_map.get("all"),
        )
        action_lexical = rag._lexical_metrics(  # noqa: SLF001
            query_terms,
            ""
            if "action" in term_map
            else rag._chunk_action_text(candidate["text"]),  # noqa: SLF001
            doc_terms=term_map.get("action"),
        )[0]
        situation_lexical = rag._lexical_metrics(  # noqa: SLF001
            query_terms,
            ""
            if "situation" in term_map
            else rag._chunk_labeled_text(candidate["text"], "situation"),  # noqa: SLF001
            doc_terms=term_map.get("situation"),
        )[0]
        outcome_lexical = rag._lexical_metrics(  # noqa: SLF001
            query_terms,
            ""
            if "outcome" in term_map
            else rag._chunk_labeled_text(candidate["text"], "outcome"),  # noqa: SLF001
            doc_terms=term_map.get("outcome"),
        )[0]
        outcome_alignment = rag._outcome_status_alignment_for_preference(  # noqa: SLF001
            query_outcome_preference, candidate["chunk_type"]
        )
        keyword = max(fts, lexical)
        quality = float(candidate.get("quality") or 0.0)
        score = (
            (rag.VECTOR_WEIGHT * cos)
            + (rag.FTS_WEIGHT * fts)
            + (rag.LEXICAL_WEIGHT * lexical)
            + (rag.QUALITY_WEIGHT * quality)
            + (rag.ACTION_WEIGHT * action_lexical)
            + (rag.SITUATION_WEIGHT * situation_lexical)
            + (rag.OUTCOME_WEIGHT * outcome_lexical)
            + outcome_alignment
            + rag._chunk_type_bonus(candidate["chunk_type"], keyword, cos)  # noqa: SLF001
            + rag._keyword_coverage_bonus(query_terms, coverage)  # noqa: SLF001
        )
        item = dict(candidate)
        item.pop("vector", None)
        item.update(
            {
                "score": score,
                "similarity": cos,
                "fts": fts,
                "lexical": lexical,
                "coverage": coverage,
                "keyword": keyword,
                "action_lexical": action_lexical,
                "situation_lexical": situation_lexical,
                "outcome_lexical": outcome_lexical,
                "outcome_alignment": outcome_alignment,
            }
        )
        if rag._candidate_is_relevant(item):  # noqa: SLF001
            ranked.append(item)
    ranked.sort(key=lambda item: item["score"], reverse=True)
    selected: list[dict[str, Any]] = []
    seen_experiences: set[str] = set()
    seen_parents: set[str] = set()
    seen_search_texts: set[str] = set()
    seen_actions: set[str] = set()
    for item in ranked:
        experience_id = str(item["experience_id"])
        parent = str(item.get("parent_session_id") or experience_id)
        search_text = str(item.get("search_text") or "")
        action_signature = rag._action_signature(  # noqa: SLF001
            str(item.get("text") or ""), str(item.get("chunk_type") or "")
        )
        if (
            experience_id in seen_experiences
            or parent in seen_parents
            or (search_text and search_text in seen_search_texts)
            or (action_signature and action_signature in seen_actions)
        ):
            continue
        selected.append(item)
        seen_experiences.add(experience_id)
        seen_parents.add(parent)
        if search_text:
            seen_search_texts.add(search_text)
        if action_signature:
            seen_actions.add(action_signature)
        if len(selected) >= top_k:
            break
    return selected


def _hit_summary(hit: dict[str, Any], case: EvalCase) -> dict[str, Any]:
    return {
        "chunk_id": hit["chunk_id"],
        "experience_id": hit["experience_id"],
        "chunk_type": hit["chunk_type"],
        "score": round(float(hit["score"]), 6),
        "similarity": round(float(hit["similarity"]), 6),
        "lexical": round(float(hit["lexical"]), 6),
        "fts": round(float(hit["fts"]), 6),
        "turn_start": hit.get("turn_start"),
        "turn_end": hit.get("turn_end"),
        "parent_session_id": hit.get("parent_session_id"),
        "relevance": _relevance(hit, case),
    }


def _candidate_parent_key(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("parent_session_id")
        or candidate.get("session_id")
        or candidate["experience_id"]
    )


def _relevance(hit: dict[str, Any], case: EvalCase) -> float:
    return max(
        case.relevant_chunks.get(hit["chunk_id"], 0.0),
        case.relevant_experiences.get(hit["experience_id"], 0.0),
    )


def _ideal_grades(case: EvalCase) -> list[float]:
    if case.source.startswith("silver-"):
        grades = [*case.relevant_chunks.values(), *case.relevant_experiences.values()]
        return [max(grades)] if grades else []
    return sorted(
        [*case.relevant_chunks.values(), *case.relevant_experiences.values()],
        reverse=True,
    )


def _dcg(grades: list[float]) -> float:
    return sum((2**grade - 1) / math.log2(idx + 2) for idx, grade in enumerate(grades))


def _ndcg_at_k(grades: list[float], ideal: list[float], k: int) -> float:
    if not ideal:
        return 0.0
    actual = _dcg(grades[:k])
    best = _dcg(ideal[:k])
    return actual / best if best > 0 else 0.0


def _reciprocal_rank(relevance: list[float]) -> float:
    rank = _first_relevant_rank(relevance)
    return 0.0 if rank is None else 1.0 / rank


def _first_relevant_rank(relevance: list[float]) -> int | None:
    for idx, rel in enumerate(relevance, start=1):
        if rel > 0:
            return idx
    return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _default_db() -> Path:
    if os.getenv("EXP_DB_PATH"):
        return Path(os.environ["EXP_DB_PATH"])
    if os.getenv("EXP_ROOT"):
        return Path(os.environ["EXP_ROOT"]) / "pool.db"
    for candidate in (Path(".experience-pool/pool.db"), Path("../.experience-pool/pool.db")):
        if candidate.exists():
            return candidate
    root = Path(".experience-pool")
    return root / "pool.db"


def _parse_ks(raw: str) -> tuple[int, ...]:
    vals = sorted({int(x.strip()) for x in raw.split(",") if x.strip()})
    return tuple(v for v in vals if v > 0) or DEFAULT_KS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Experience Pool RAG retrieval offline.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    eval_p = sub.add_parser("eval", help="evaluate a gold JSONL file")
    eval_p.add_argument("--db", default=str(_default_db()))
    eval_p.add_argument("--gold", required=True, help="JSONL with query and relevant ids")
    eval_p.add_argument("--viewer", default=None, help="agent name for ACL-filtered eval")
    eval_p.add_argument("--scope", default="personal", choices=["personal", "community", "auto", "all"])
    eval_p.add_argument("--task-type", default=None)
    eval_p.add_argument("--top-k", type=int, default=10)
    eval_p.add_argument("--ks", default="1,3,5,10")
    eval_p.add_argument("--exclude-self", action="store_true")
    eval_p.add_argument("--details", action="store_true", help="include per-case rankings")

    silver_p = sub.add_parser("make-silver", help="write cheap self-retrieval JSONL cases")
    silver_p.add_argument("--db", default=str(_default_db()))
    silver_p.add_argument("--out", required=True)
    silver_p.add_argument("--limit", type=int, default=50)
    silver_p.add_argument("--owner", default=None)
    silver_p.add_argument("--per-experience", type=int, default=5)
    silver_p.add_argument(
        "--chunk-types",
        default=",".join(sorted(UNIT_TYPES)),
        help="comma-separated chunk types to sample",
    )
    silver_p.add_argument("--min-turn-count", type=int, default=0)
    silver_p.add_argument(
        "--min-turn-fraction",
        type=float,
        default=0.0,
        help="only sample chunks after this fraction of each session timeline",
    )
    silver_p.add_argument("--max-query-chars", type=int, default=320)

    args = parser.parse_args(argv)
    conn = _connect(args.db)
    if args.cmd == "make-silver":
        cases = build_silver_cases(
            conn,
            limit=args.limit,
            unit_types={
                value.strip()
                for value in args.chunk_types.split(",")
                if value.strip()
            },
            owner=args.owner,
            per_experience=args.per_experience,
            min_turn_count=args.min_turn_count,
            min_turn_fraction=args.min_turn_fraction,
            max_query_chars=args.max_query_chars,
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for case in cases:
                fh.write(
                    json.dumps(
                        {
                            "id": case.case_id,
                            "source": case.source,
                            "query": case.query,
                            "relevant_chunk_ids": list(case.relevant_chunks),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(json.dumps({"ok": True, "out": str(out), "cases": len(cases)}, ensure_ascii=False))
        return 0

    cases = load_gold(args.gold)
    scope = args.scope
    viewer = args.viewer
    if scope == "all":
        scope = "personal"
        viewer = None
    report = evaluate(
        conn,
        cases,
        viewer_name=viewer,
        top_k=args.top_k,
        ks=_parse_ks(args.ks),
        scope=scope,
        task_type=args.task_type,
        exclude_self=args.exclude_self,
    )
    if not args.details:
        report = {k: v for k, v in report.items() if k != "cases"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

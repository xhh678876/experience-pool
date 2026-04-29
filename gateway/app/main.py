from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import structlog
from fastapi import Depends, FastAPI, HTTPException, Header, Query

from .config import settings
from .db import close_pool, execute, fetch_all, fetch_one
from .embeddings import embed
from .models import (
    PushRequest,
    PushResponse,
    RegisterRequest,
    RegisterResponse,
    ScoreComponents,
    SearchHit,
    SearchResponse,
    SortMode,
)
from .queue import RAW, close_redis, publish
from .ranking import Candidate, q_scalar, score_candidates
from .storage import put_trajectory
from .vectors import ensure_collections, search_intent, upsert_intent

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await ensure_collections()
    yield
    await close_pool()
    await close_redis()


app = FastAPI(title="Experience Pool Gateway", version="0.1.0", lifespan=lifespan)


async def resolve_agent(x_agent_name: str = Header(default="dev-local")) -> dict[str, Any]:
    row = await fetch_one("SELECT agent_id, name, team FROM agents WHERE name = $1", x_agent_name)
    if row is None:
        raise HTTPException(status_code=401, detail=f"unknown agent: {x_agent_name}")
    return dict(row)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/agents/register", response_model=RegisterResponse)
async def register_agent(req: RegisterRequest) -> RegisterResponse:
    row = await fetch_one(
        """
        INSERT INTO agents (name, team) VALUES ($1, $2)
        ON CONFLICT (name) DO UPDATE SET team = EXCLUDED.team
        RETURNING agent_id, name, team
        """,
        req.name,
        req.team,
    )
    assert row is not None
    return RegisterResponse(agent_id=row["agent_id"], name=row["name"], team=row["team"])


@app.post("/v1/experiences", response_model=PushResponse, status_code=202)
async def push_experience(
    req: PushRequest, agent: dict[str, Any] = Depends(resolve_agent)
) -> PushResponse:
    review_required = req.sensitivity == "high"

    # 1. Insert row in pending state. trajectory_uri filled after S3 put.
    row = await fetch_one(
        """
        INSERT INTO experiences (
            agent_id, task_type, source_model, sensitivity, acl, tags
        ) VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING experience_id
        """,
        agent["agent_id"],
        req.task_type,
        req.source_model,
        req.sensitivity,
        req.acl,
        req.tags,
    )
    assert row is not None
    eid: UUID = row["experience_id"]

    # 2. Persist raw trajectory.
    uri = put_trajectory(eid, [s.model_dump() for s in req.trajectory])
    await execute("UPDATE experiences SET trajectory_uri = $1 WHERE experience_id = $2", uri, eid)

    # 3. Record parent edges for delayed credit assignment.
    for parent_id in req.parent_experience_ids:
        await execute(
            """
            INSERT INTO experience_edges (parent_id, child_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            parent_id,
            eid,
        )
        await execute(
            "UPDATE experiences SET reuse_count = reuse_count + 1 WHERE experience_id = $1",
            parent_id,
        )

    # 4. Publish to pipeline.
    await publish(
        RAW,
        {
            "experience_id": str(eid),
            "trajectory_uri": uri,
            "task_type": req.task_type,
            "source_model": req.source_model,
            "sensitivity": req.sensitivity,
            "objective_signals": req.objective_signals,
            "parent_experience_ids": [str(p) for p in req.parent_experience_ids],
        },
    )

    # 5. Audit.
    await execute(
        """
        INSERT INTO audit_log (actor, actor_kind, action, target_id, payload)
        VALUES ($1, 'agent', 'push', $2, $3)
        """,
        agent["name"],
        eid,
        json.dumps(
            {
                "task_type": req.task_type,
                "sensitivity": req.sensitivity,
                "parents": [str(p) for p in req.parent_experience_ids],
            }
        ),
    )

    return PushResponse(experience_id=eid, status="queued", review_required=review_required)


@app.get("/v1/experiences/{experience_id}")
async def get_experience(experience_id: UUID) -> dict[str, Any]:
    row = await fetch_one("SELECT * FROM experiences WHERE experience_id = $1", experience_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _serialize(dict(row))


@app.get("/v1/experiences/search", response_model=SearchResponse)
async def search(
    q: str,
    task_type: str | None = None,
    top_k: int = Query(default=5, ge=1, le=50),
    sort: SortMode = "score",
    exploration: float | None = None,
    acl: str = "private,team,org",
    agent: dict[str, Any] = Depends(resolve_agent),
) -> SearchResponse:
    acl_filter = [a.strip() for a in acl.split(",") if a.strip()]

    # 1. Vector search over intent.
    qvec = embed(q)
    hits = await search_intent(qvec, limit=top_k * 4, acl_filter=acl_filter, task_type=task_type)
    if not hits:
        return SearchResponse(results=[])

    ids = [UUID(h.id) for h in hits if isinstance(h.id, str)]
    sim_by_id = {UUID(h.id): float(h.score) for h in hits if isinstance(h.id, str)}

    # 2. Pull Q + visit_count from Postgres.
    rows = await fetch_all(
        """
        SELECT experience_id, intent_text, script_steps, tool_capabilities, key_decisions,
               pitfalls, summary, q_outcome, q_intent, q_execution, q_orchestration,
               q_expression, visit_count, reuse_count
        FROM experiences
        WHERE experience_id = ANY($1::uuid[])
          AND extraction_status = 'done'
          AND review_status IN ('approved', 'auto_approved', 'edited')
        """,
        ids,
    )
    by_id = {r["experience_id"]: r for r in rows}

    candidates: list[Candidate] = []
    for eid in ids:
        r = by_id.get(eid)
        if r is None:
            continue
        candidates.append(
            Candidate(
                experience_id=str(eid),
                similarity=sim_by_id[eid],
                q_outcome=r["q_outcome"] or 0.0,
                q_intent=r["q_intent"] or 0.0,
                q_execution=r["q_execution"] or 0.0,
                q_orchestration=r["q_orchestration"] or 0.0,
                q_expression=r["q_expression"] or 0.0,
                visit_count=r["visit_count"] or 0,
            )
        )
    if not candidates:
        return SearchResponse(results=[])

    # 3. Mixed score (default). Other sorts are simpler views over the same data.
    c_explore = exploration if exploration is not None else settings.c_exploration
    ranked = score_candidates(
        candidates,
        w_similarity=settings.w_similarity,
        w_q=settings.w_q,
        c_exploration=c_explore,
    )

    if sort == "similarity":
        ranked.sort(key=lambda x: x[0].similarity, reverse=True)
    elif sort == "q_value":
        ranked.sort(key=lambda x: q_scalar(x[0]), reverse=True)
    elif sort == "recency":
        ranked.sort(key=lambda x: by_id[UUID(x[0].experience_id)]["experience_id"], reverse=True)

    ranked = ranked[:top_k]

    # 4. Bump visit_count + log search.
    hit_ids = [UUID(c.experience_id) for c, *_ in ranked]
    await execute(
        "UPDATE experiences SET visit_count = visit_count + 1 WHERE experience_id = ANY($1::uuid[])",
        hit_ids,
    )
    for rank, (cand, total, _sim, _qc, _ucb) in enumerate(ranked):
        await execute(
            """
            INSERT INTO search_log (experience_id, queried_by, query_text, rank, similarity, score)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            UUID(cand.experience_id),
            agent["agent_id"],
            q,
            rank,
            cand.similarity,
            total,
        )

    # 5. Build response.
    results: list[SearchHit] = []
    for cand, total, sim_c, q_c, ucb_c in ranked:
        r = by_id[UUID(cand.experience_id)]
        results.append(
            SearchHit(
                experience_id=UUID(cand.experience_id),
                intent_text=r["intent_text"],
                script={
                    "steps": r["script_steps"] or [],
                    "tool_capabilities": r["tool_capabilities"] or [],
                    "key_decisions": r["key_decisions"] or [],
                    "pitfalls": r["pitfalls"] or [],
                },
                summary=r["summary"],
                q_scalar=q_scalar(cand),
                q_breakdown={
                    "outcome": cand.q_outcome,
                    "intent": cand.q_intent,
                    "execution": cand.q_execution,
                    "orchestration": cand.q_orchestration,
                    "expression": cand.q_expression,
                },
                visit_count=cand.visit_count,
                reuse_count=r["reuse_count"] or 0,
                score=total,
                score_components=ScoreComponents(
                    similarity=sim_c, q_value=q_c, exploration=ucb_c
                ),
            )
        )
    return SearchResponse(results=results)


@app.post("/v1/experiences/{experience_id}/_dev/seed")
async def dev_seed(experience_id: UUID) -> dict[str, str]:
    """Dev-only: take a row that finished S3 put and pretend the pipeline finished.
    Lets you exercise search without spinning up workers. Drop in production."""
    row = await fetch_one(
        "SELECT trajectory_uri, task_type FROM experiences WHERE experience_id = $1",
        experience_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")

    intent_text = f"placeholder intent for {experience_id}"
    await execute(
        """
        UPDATE experiences SET
          intent_text = $2,
          summary = $3,
          script_steps = $4::jsonb,
          tool_capabilities = $5::jsonb,
          pitfalls = $6::jsonb,
          q_outcome = 0.5, q_intent = 0.5, q_execution = 0.4,
          q_orchestration = 0.4, q_expression = 0.5,
          extraction_status = 'done',
          review_status = 'auto_approved',
          sanitization_status = 'done'
        WHERE experience_id = $1
        """,
        experience_id,
        intent_text,
        "seeded",
        json.dumps([{"step": "stub", "why": "dev", "how": "noop"}]),
        json.dumps(["stub_capability"]),
        json.dumps([]),
    )

    vec = embed(intent_text)
    await upsert_intent(
        experience_id,
        vec,
        {"task_type": row["task_type"], "acl": "private"},
    )
    return {"status": "seeded"}


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k, v in list(out.items()):
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, UUID):
            out[k] = str(v)
    return out

"""HTTP gateway that wraps the standalone ExperiencePool.

This is the canonical "shared server" agents talk to. One process, SQLite-backed,
HMAC-verified per request. Run with:

    uvicorn exp_core.server:app --host 0.0.0.0 --port 8080

Behind a real reverse proxy in production (nginx / Cloudflare). The pool root is
controlled by EXP_ROOT; credentials are stored at $EXP_ROOT/credentials/.
"""

from __future__ import annotations

import base64
import json
import os
import tarfile
import io
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import lite as lite_mod
from . import skills as skills_mod
from .acl_search import get_with_acl, search_with_acl
from .identity import issue_credential, load_credential, verify_signature
from .monitoring import dashboard_stats, reuse_leaderboard
from .pool import ExperiencePool, PoolConfig

POOL: ExperiencePool | None = None


def _pool() -> ExperiencePool:
    global POOL
    if POOL is None:
        root = Path(os.getenv("EXP_ROOT", str(Path.home() / ".experience-pool")))
        POOL = ExperiencePool(PoolConfig(root=root))
    return POOL


app = FastAPI(title="Experience Pool", version="0.1.0")


# ----- middleware -----------------------------------------------------------

PUBLIC_PATHS = {"/healthz", "/v1/agents/register", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def hmac_auth(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs"):
        return await call_next(request)
    name = request.headers.get("x-agent-name")
    sig = request.headers.get("x-signature")
    if not name:
        return JSONResponse({"error": "missing X-Agent-Name"}, status_code=401)
    cred = load_credential(name)
    if cred is None:
        return JSONResponse({"error": f"unknown agent: {name}"}, status_code=401)
    body = await request.body()
    if sig is None or not verify_signature(
        cred.secret, request.method, request.url.path + (
            "?" + request.url.query if request.url.query else ""
        ),
        body, sig,
    ):
        # Compute again with just path (no query) — older clients sign that way.
        if sig is None or not verify_signature(
            cred.secret, request.method, request.url.path, body, sig,
        ):
            return JSONResponse({"error": "bad signature"}, status_code=401)
    request.state.agent_name = name
    request.state.agent_team = cred.team
    return await call_next(request)


# ----- request models -------------------------------------------------------


class RegisterReq(BaseModel):
    name: str
    team: str


class PushReq(BaseModel):
    task_type: str
    source_model: str
    trajectory: list[dict[str, Any]]
    parent_experience_ids: list[str] = Field(default_factory=list)
    uses_skills: list[str] = Field(default_factory=list)
    sensitivity: str = "medium"
    acl: str = "private"
    tags: list[str] = Field(default_factory=list)


class PushSkillReq(BaseModel):
    bundle_b64: str
    sensitivity: str = "medium"
    acl: str = "private"
    tags: list[str] = Field(default_factory=list)


# ----- routes ---------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/agents/register")
async def register(req: RegisterReq) -> dict[str, Any]:
    pool = _pool()
    aid = pool.register_agent(req.name, req.team)
    cred = issue_credential(aid, req.name, req.team)
    return cred.to_dict()


@app.post("/v1/experiences", status_code=202)
async def push(req: PushReq, request: Request) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    return pool.push(
        agent_name=actor,
        task_type=req.task_type,
        source_model=req.source_model,
        trajectory=req.trajectory,
        parent_experience_ids=req.parent_experience_ids,
        uses_skills=req.uses_skills,
        sensitivity=req.sensitivity,
        acl=req.acl,
        tags=req.tags,
    )


@app.get("/v1/experiences/search")
async def search(
    request: Request,
    q: str,
    task_type: str | None = None,
    top_k: int = 5,
    sort: str = "score",
    exploration: float | None = None,
) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    return {"results": search_with_acl(
        pool, actor, q, top_k=top_k, task_type=task_type,
        sort=sort, exploration=exploration,
    )}


@app.get("/v1/experiences/{experience_id}")
async def get_experience(experience_id: str, request: Request) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    row = get_with_acl(pool, actor, experience_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found or denied")
    return row


@app.post("/v1/skills", status_code=202)
async def push_skill(req: PushSkillReq, request: Request) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    raw = base64.b64decode(req.bundle_b64)
    # Extract to a temp dir so we can reuse the existing build_bundle path that
    # walks a directory. This also gives us tarbomb resistance.
    with tempfile.TemporaryDirectory(prefix="expskill_") as tmp:
        # Resolve symlinks (macOS /tmp -> /private/tmp) so prefix-match works.
        tmp_path = Path(tmp).resolve()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                # Strip leading "./" that `tar -czf - -C dir .` produces.
                rel = member.name.lstrip("./") or member.name
                target = (tmp_path / rel).resolve()
                if not str(target).startswith(str(tmp_path) + os.sep) \
                   and target != tmp_path:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unsafe path in bundle: {member.name!r}",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                fobj = tar.extractfile(member)
                if fobj:
                    target.write_bytes(fobj.read())
        return pool.push_skill(
            agent_name=actor,
            bundle_dir=tmp_path,
            sensitivity=req.sensitivity,
            acl=req.acl,
            tags=req.tags,
        )


@app.get("/v1/skills/search")
async def skill_search(
    request: Request, q: str, top_k: int = 5,
) -> dict[str, Any]:
    pool = _pool()
    return {"results": pool.search_skills(q, top_k=top_k)}


@app.get("/v1/skills/install")
async def skill_install(name: str, request: Request, version: str | None = None):
    pool = _pool()
    row = skills_mod.resolve_skill(pool.conn, name, version)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown skill: {name}")
    bundle_path = Path(row["bundle_path"])
    if not bundle_path.exists():
        raise HTTPException(status_code=500, detail=f"bundle missing: {bundle_path}")
    bundle = bundle_path.read_bytes()
    pool.conn.execute(
        "UPDATE skills SET install_count = install_count + 1 WHERE skill_id = ?",
        (row["skill_id"],),
    )
    pool.conn.commit()
    return {
        "skill_id": row["skill_id"],
        "name": row["name"],
        "version": row["version"],
        "bundle_b64": base64.b64encode(bundle).decode("ascii"),
        "bundle_sha256": row["bundle_sha256"],
    }


class LitePushReq(BaseModel):
    query: str
    intent: str
    steps: list[str]
    outcome: str
    task_type: str = "misc"
    source_model: str = "unknown"
    sensitivity: str = "medium"
    acl: str = "private"
    tags: list[str] = Field(default_factory=list)
    redactions: dict[str, int] = Field(default_factory=dict)


class LiteSearchReq(BaseModel):
    q: str
    top_k: int = 5
    task_type: str | None = None


@app.post("/v1/lite/push", status_code=202)
async def lite_push(req: LitePushReq, request: Request) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    card = lite_mod.LiteCard(
        query=req.query, intent=req.intent, steps=req.steps, outcome=req.outcome,
        task_type=req.task_type, source_model=req.source_model,
        sensitivity=req.sensitivity, acl=req.acl, tags=req.tags,
        redactions=req.redactions,
    )
    return lite_mod.push_lite(
        pool.conn, rules=pool._sanitize_rules,
        agent_name=actor, card=card,
    )


@app.post("/v1/lite/search")
async def lite_search(req: LiteSearchReq, request: Request) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    return {"results": lite_mod.search_lite(
        pool.conn, viewer_name=actor, query=req.q,
        top_k=req.top_k, task_type=req.task_type,
    )}


@app.get("/v1/admin/dashboard")
async def admin_dashboard(request: Request) -> dict[str, Any]:
    return dashboard_stats(_pool())


@app.get("/v1/admin/leaderboard")
async def admin_leaderboard(request: Request, top_k: int = 20) -> list[dict[str, Any]]:
    return reuse_leaderboard(_pool(), top_k=top_k)

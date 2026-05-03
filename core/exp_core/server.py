"""HTTP gateway that wraps the standalone ExperiencePool.

This is the canonical "shared server" agents talk to. One process, SQLite-backed,
HMAC-verified per request. Run with:

    uvicorn exp_core.server:app --host 0.0.0.0 --port 8080

Behind a real reverse proxy in production (nginx / Cloudflare). The pool root is
controlled by EXP_ROOT; credentials are stored at $EXP_ROOT/credentials/.
"""

from __future__ import annotations

import base64
from collections import defaultdict
from collections.abc import Callable
import datetime as _dt
import json
import os
import shutil
import sqlite3
import tarfile
import io
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import lite as lite_mod
from . import skills as skills_mod
from . import auto_label as auto_label_mod
from .acl_search import get_with_acl, search_with_acl
from .identity import issue_credential, load_credential, verify_signature
from .monitoring import dashboard_stats, reuse_leaderboard
from .pool import ExperiencePool, PoolConfig

POOL: ExperiencePool | None = None
LOG = structlog.get_logger(__name__)
RATE_COUNTERS: dict[tuple[str, str, int], int] = defaultdict(int)
LAST_RATE_PRUNE = 0.0


def _pool() -> ExperiencePool:
    global POOL
    if POOL is None:
        root = Path(os.getenv("EXP_ROOT", str(Path.home() / ".experience-pool")))
        POOL = ExperiencePool(PoolConfig(root=root))
    return POOL


app = FastAPI(title="Experience Pool", version="0.1.0")


# ----- middleware -----------------------------------------------------------

PUBLIC_PATHS = {"/healthz", "/v1/agents/register", "/docs", "/openapi.json", "/redoc"}


def _root_path() -> Path:
    return Path(os.getenv("EXP_ROOT", str(Path.home() / ".experience-pool")))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _rate_group(request: Request) -> tuple[str, int] | None:
    """Return (group, per-window limit) for routes that need throttling."""
    method = request.method.upper()
    path = request.url.path
    if path == "/v1/agents/register" and method == "POST":
        return ("register", _env_int("EXP_RATE_REGISTER_PER_MIN", 30))
    if path in {"/v1/experiences", "/v1/lite/push"} and method == "POST":
        return ("push", _env_int("EXP_RATE_PUSH_PER_MIN", 60))
    if path == "/v1/skills" and method == "POST":
        return ("push_skill", _env_int("EXP_RATE_PUSH_SKILL_PER_MIN", 10))
    if path in {"/v1/experiences/search", "/v1/skills/search"} and method == "GET":
        return ("search", _env_int("EXP_RATE_SEARCH_PER_MIN", 1000))
    if path == "/v1/lite/search" and method == "POST":
        return ("search", _env_int("EXP_RATE_SEARCH_PER_MIN", 1000))
    if path == "/v1/lite/rewards" and method == "POST":
        return ("rewards", _env_int("EXP_RATE_REWARDS_PER_MIN", 60))
    if path == "/v1/lite/revoke" and method == "POST":
        return ("revoke", _env_int("EXP_RATE_REVOKE_PER_MIN", 30))
    if path == "/v1/lite/publish" and method == "POST":
        return ("publish", _env_int("EXP_RATE_PUBLISH_PER_MIN", 30))
    if path == "/v1/lite/unpublish" and method == "POST":
        return ("publish", _env_int("EXP_RATE_PUBLISH_PER_MIN", 30))
    if path == "/v1/me/quota" and method == "GET":
        return ("quota", _env_int("EXP_RATE_QUOTA_PER_MIN", 60))
    return None


def _client_key(request: Request) -> str:
    agent = request.headers.get("x-agent-name")
    if agent:
        return f"agent:{agent}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _rate_limit_response(request: Request) -> JSONResponse | None:
    if os.getenv("EXP_RATE_LIMIT_ENABLED", "1").lower() in {"0", "false", "no"}:
        return None
    group = _rate_group(request)
    if group is None:
        return None
    bucket_name, limit = group
    if limit <= 0:
        return None
    window = _env_int("EXP_RATE_WINDOW_SECONDS", 60)
    now = time.time()
    bucket = int(now // window)
    key = (_client_key(request), bucket_name, bucket)
    global LAST_RATE_PRUNE
    if now - LAST_RATE_PRUNE > window:
        stale_before = bucket - 2
        for old_key in list(RATE_COUNTERS):
            if old_key[2] <= stale_before:
                RATE_COUNTERS.pop(old_key, None)
        LAST_RATE_PRUNE = now
    RATE_COUNTERS[key] += 1
    if RATE_COUNTERS[key] <= limit:
        return None
    return JSONResponse(
        {
            "error": "rate_limited",
            "group": bucket_name,
            "limit": limit,
            "window_seconds": window,
        },
        status_code=429,
        headers={"Retry-After": str(window)},
    )


def _log_request(request: Request, status_code: int, duration_ms: float) -> None:
    LOG.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        duration_ms=round(duration_ms, 2),
        agent_name=getattr(request.state, "agent_name", None)
        or request.headers.get("x-agent-name"),
        client=request.client.host if request.client else None,
    )


async def _call_and_log(request: Request, call_next: Callable[[Request], Any]):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _log_request(request, 500, (time.perf_counter() - started) * 1000)
        raise
    _log_request(request, response.status_code, (time.perf_counter() - started) * 1000)
    return response


def _json_and_log(request: Request, payload: dict[str, Any], status_code: int) -> JSONResponse:
    _log_request(request, status_code, 0)
    return JSONResponse(payload, status_code=status_code)


@app.middleware("http")
async def hmac_auth(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs"):
        limited = _rate_limit_response(request)
        if limited is not None:
            _log_request(request, limited.status_code, 0)
            return limited
        return await _call_and_log(request, call_next)
    name = request.headers.get("x-agent-name")
    sig = request.headers.get("x-signature")
    if not name:
        return _json_and_log(request, {"error": "missing X-Agent-Name"}, 401)
    cred = load_credential(name)
    if cred is None:
        return _json_and_log(request, {"error": f"unknown agent: {name}"}, 401)
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
            return _json_and_log(request, {"error": "bad signature"}, 401)
    request.state.agent_name = name
    request.state.agent_team = cred.team
    limited = _rate_limit_response(request)
    if limited is not None:
        _log_request(request, limited.status_code, 0)
        return limited
    return await _call_and_log(request, call_next)


# ----- request models -------------------------------------------------------


class RegisterReq(BaseModel):
    name: str
    team: str
    # Stable owner handle that groups multiple agents into one personal
    # pool. If omitted, the server back-fills owner = name (1:1 isolation,
    # same as before). Recommended: a GitHub handle or email-shaped string.
    owner: str | None = None


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


def _health_payload(*, deep: bool) -> tuple[dict[str, Any], int]:
    root = _root_path()
    payload: dict[str, Any] = {
        "status": "ok",
        "checks": {},
    }
    if deep:
        payload["root"] = str(root)
    status_code = 200

    try:
        pool = _pool()
        pool.conn.execute("SELECT 1").fetchone()
        db_path = pool.config.db_path
        payload["checks"]["sqlite"] = {
            "status": "ok",
            "path": str(db_path) if deep else db_path.name,
            "bytes": db_path.stat().st_size if db_path.exists() else 0,
        }
        if deep:
            payload["counts"] = {
                "agents": pool.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
                "experiences": pool.conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0],
                "lite_experiences": pool.conn.execute(
                    "SELECT COUNT(*) FROM experiences WHERE COALESCE(ingest_path, 'full') = 'lite'"
                ).fetchone()[0],
                "skills": pool.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0],
                "audit_log": pool.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            }
    except sqlite3.DatabaseError as exc:
        payload["status"] = "fail"
        payload["checks"]["sqlite"] = {"status": "fail", "error": str(exc)}
        status_code = 503
    except Exception as exc:  # noqa: BLE001
        payload["status"] = "fail"
        payload["checks"]["sqlite"] = {"status": "fail", "error": str(exc)}
        status_code = 503

    try:
        disk_root = root if root.exists() else root.parent
        usage = shutil.disk_usage(disk_root)
        free_ratio = usage.free / usage.total if usage.total else 0
        disk_status = "ok"
        if free_ratio < 0.05:
            disk_status = "fail"
            payload["status"] = "fail"
            status_code = 503
        elif free_ratio < 0.10 and payload["status"] == "ok":
            disk_status = "degraded"
            payload["status"] = "degraded"
        payload["checks"]["disk"] = {
            "status": disk_status,
            "free_percent": round(free_ratio * 100, 2),
            "free_bytes": usage.free if deep else None,
        }
    except Exception as exc:  # noqa: BLE001
        payload["status"] = "fail"
        payload["checks"]["disk"] = {"status": "fail", "error": str(exc)}
        status_code = 503

    if not deep:
        for check in payload["checks"].values():
            check.pop("free_bytes", None)
    return payload, status_code


@app.get("/healthz")
async def healthz():
    payload, status_code = _health_payload(deep=False)
    return JSONResponse(payload, status_code=status_code)


@app.post("/v1/agents/register")
async def register(req: RegisterReq) -> dict[str, Any]:
    pool = _pool()
    aid = pool.register_agent(req.name, req.team)
    # Set owner — explicit if provided, else default to name. The DB
    # column was added by quality.ensure_quality_columns; updating it
    # here is idempotent for re-registrations.
    owner = (req.owner or req.name).strip()
    pool.conn.execute(
        "UPDATE agents SET owner = ? WHERE name = ?", (owner, req.name)
    )
    pool.conn.commit()
    cred = issue_credential(aid, req.name, req.team)
    out = cred.to_dict()
    out["owner"] = owner
    return out


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
    # Rich session IR — populated when the upload is sourced from a real
    # extractor (e.g. claude_sft_delivery / cursor_sft_delivery). All
    # optional; omitting them keeps the legacy card-only behavior.
    trajectory: list[dict[str, Any]] | None = None
    system: list[dict[str, Any]] | str | None = None
    tools: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None


class LiteSearchReq(BaseModel):
    q: str
    top_k: int = 5
    task_type: str | None = None
    # 'auto' = personal pool always + community pool if quota unlocked.
    # 'personal' = force personal-only. 'community' = force public-only
    # (still gated by quota).
    scope: str = "auto"


@app.post("/v1/lite/push", status_code=202)
async def lite_push(
    req: LitePushReq,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    card = lite_mod.LiteCard(
        query=req.query, intent=req.intent, steps=req.steps, outcome=req.outcome,
        task_type=req.task_type, source_model=req.source_model,
        sensitivity=req.sensitivity, acl=req.acl, tags=req.tags,
        redactions=req.redactions,
    )
    result = lite_mod.push_lite(
        pool.conn, rules=pool._sanitize_rules,
        agent_name=actor, card=card,
        trajectory=req.trajectory,
        system=req.system,
        tools=req.tools,
        meta=req.meta,
        trajectories_dir=pool.config.trajectories_dir,
    )
    # Fire-and-forget auto-labeling. Uses a separate sqlite connection inside
    # the task so it never blocks the response.
    eid = result.get("experience_id")
    if eid and auto_label_mod._enabled():
        db_path = str(pool.config.db_path)
        background_tasks.add_task(_safe_auto_label, db_path, eid)
        result["auto_label_queued"] = True
    return result


def _safe_auto_label(db_path: str, eid: str) -> None:
    try:
        auto_label_mod.auto_label_experience(db_path, eid)
    except Exception as e:
        LOG.warning("auto_label failed", experience_id=eid, error=str(e)[:200])


@app.post("/v1/lite/search")
async def lite_search(req: LiteSearchReq, request: Request) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    scope = getattr(req, "scope", "auto") or "auto"
    return lite_mod.search_lite_with_meta(
        pool.conn, viewer_name=actor, query=req.q,
        top_k=req.top_k, task_type=req.task_type, scope=scope,
    )


# ----- revoke (right-to-be-forgotten) ----------------------------------------


class LiteRevokeReq(BaseModel):
    experience_id: str
    reason: str = "user_request"


@app.post("/v1/lite/revoke")
async def lite_revoke(req: LiteRevokeReq, request: Request) -> dict[str, Any]:
    """Revoke a previously uploaded experience.

    What happens:
      1. Caller must own the row (agent_name on header == experiences.agent_id).
      2. trajectory_path file is HARD-DELETED from disk.
      3. experiences.revoked = 1, revoked_at = now, revoke_reason = req.reason.
         The row stays in the DB so audit_log can reference it.
      4. vectors row is dropped — search/clusters won't return it.
      5. cluster_membership rows are dropped so the cluster recomputes
         without the revoked content.
      6. audit_log entry written.
    """
    pool = _pool()
    actor = request.state.agent_name
    eid = req.experience_id

    cur = pool.conn.execute(
        """
        SELECT e.experience_id, e.agent_id, e.trajectory_path, e.revoked, a.name AS agent_name
        FROM experiences e JOIN agents a USING(agent_id)
        WHERE e.experience_id = ?
        """,
        (eid,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"experience not found: {eid}")
    if row["agent_name"] != actor:
        raise HTTPException(
            status_code=403,
            detail=f"agent {actor!r} does not own experience {eid}",
        )
    if row["revoked"]:
        return {
            "ok": True,
            "status": "already_revoked",
            "experience_id": eid,
        }

    # 1. Delete trajectory sidecar from disk.
    deleted_files: list[str] = []
    traj_path = row["trajectory_path"]
    if traj_path:
        from pathlib import Path as _P
        p = _P(traj_path)
        if p.exists():
            try:
                p.unlink()
                deleted_files.append(str(p))
            except OSError as exc:
                LOG.warning("revoke: trajectory unlink failed",
                            path=str(p), error=str(exc))

    # 2. Mark revoked + drop vector + drop cluster membership.
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    pool.conn.execute(
        """UPDATE experiences
           SET revoked = 1, revoked_at = ?, revoke_reason = ?,
               review_status = 'revoked',
               trajectory_path = NULL
           WHERE experience_id = ?""",
        (now_iso, req.reason[:200], eid),
    )
    pool.conn.execute("DELETE FROM vectors WHERE experience_id = ?", (eid,))
    try:
        pool.conn.execute(
            "DELETE FROM cluster_membership WHERE experience_id = ?", (eid,))
    except sqlite3.OperationalError:
        pass  # table may not exist on older deployments
    try:
        pool.conn.execute(
            "DELETE FROM turn_rewards WHERE experience_id = ?", (eid,))
    except sqlite3.OperationalError:
        pass

    pool.conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        (actor, "agent", "revoke", eid,
         json.dumps({"reason": req.reason[:200],
                     "deleted_files": deleted_files,
                     "ts": now_iso})),
    )
    pool.conn.commit()
    return {
        "ok": True,
        "status": "revoked",
        "experience_id": eid,
        "deleted_files": deleted_files,
        "revoked_at": now_iso,
    }


# ----- publish / unpublish (community pool opt-in) ---------------------------


class LitePublishReq(BaseModel):
    experience_id: str


@app.post("/v1/lite/publish")
async def lite_publish(req: LitePublishReq, request: Request) -> JSONResponse:
    """Publish a private experience to the community pool.

    Runs strict_public_check (file://, local resources, localhost URLs,
    UUIDs, etc.). On pass: sets acl='public', bumps owner.publish_count.
    On fail: HTTP 422 with the offending hits + locations so the user can
    clean and retry.
    """
    from . import community
    pool = _pool()
    actor = request.state.agent_name
    result = community.publish_experience(
        pool.conn, experience_id=req.experience_id, actor_name=actor,
    )
    if result.status == "blocked":
        # Surface the strict-mode hits to the user.
        return JSONResponse(result.to_dict(), status_code=422)
    if result.status == "not_found":
        return JSONResponse(result.to_dict(), status_code=404)
    if result.status == "forbidden":
        return JSONResponse(result.to_dict(), status_code=403)
    return JSONResponse(result.to_dict(), status_code=200)


@app.post("/v1/lite/unpublish")
async def lite_unpublish(req: LitePublishReq, request: Request) -> JSONResponse:
    """Drop a published experience back to private. publish_count is
    NOT decremented (contribution credit stays once earned)."""
    from . import community
    pool = _pool()
    actor = request.state.agent_name
    result = community.unpublish_experience(
        pool.conn, experience_id=req.experience_id, actor_name=actor,
    )
    if result.status == "not_found":
        return JSONResponse(result.to_dict(), status_code=404)
    if result.status == "forbidden":
        return JSONResponse(result.to_dict(), status_code=403)
    return JSONResponse(result.to_dict(), status_code=200)


@app.get("/v1/me/quota")
async def me_quota(request: Request) -> dict[str, Any]:
    """Returns the current viewer's publish_count, threshold, and unlock
    state. Used by the UI to show progress + decide whether to surface
    the community pool."""
    from . import community
    pool = _pool()
    actor = request.state.agent_name
    owner = community.get_owner(pool.conn, actor)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {actor}")
    quota = community.get_quota(pool.conn, owner)
    return quota.to_dict()


# ----- per-turn rewards (synergy schema) -----------------------------------

class TurnReward(BaseModel):
    turn_index: int
    user_turn_index: int | None = None
    outcome: int
    intent: int
    execution: int
    orchestration: int
    expression: int
    confidence: float
    reason: str = ""


class RewardsPushReq(BaseModel):
    experience_id: str
    rewards: list[TurnReward]
    summary: dict[str, Any] = Field(default_factory=dict)
    judge_model: str = "unknown"
    judge_backend: str = "unknown"
    annotated_at: str = ""
    replace: bool = True  # delete prior rewards from this judge_model before insert


def _validate_turn_reward(r: TurnReward) -> None:
    for name, v in (("outcome", r.outcome), ("intent", r.intent),
                    ("execution", r.execution), ("orchestration", r.orchestration),
                    ("expression", r.expression)):
        if v not in (-1, 0, 1):
            raise HTTPException(
                status_code=400,
                detail=f"reward.{name} must be -1/0/1; got {v} at turn_index={r.turn_index}",
            )
    if not (0.0 <= r.confidence <= 1.0):
        raise HTTPException(
            status_code=400,
            detail=f"confidence must be in [0,1]; got {r.confidence} at turn_index={r.turn_index}",
        )


@app.post("/v1/lite/rewards", status_code=202)
async def lite_rewards_push(req: RewardsPushReq, request: Request) -> dict[str, Any]:
    pool = _pool()
    actor = request.state.agent_name
    # Verify experience exists. Return 404 if not, so clients don't silently
    # post into the void.
    row = pool.conn.execute(
        "SELECT experience_id, agent_id, acl FROM experiences WHERE experience_id=?",
        (req.experience_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"experience not found: {req.experience_id}")
    if not req.rewards:
        raise HTTPException(status_code=400, detail="rewards array must be non-empty")
    for r in req.rewards:
        _validate_turn_reward(r)
    import datetime as _dt
    annotated_at = req.annotated_at or _dt.datetime.utcnow().isoformat(timespec="seconds")
    judge_model = req.judge_model or "unknown"
    judge_backend = req.judge_backend or "unknown"

    rows = [
        (
            req.experience_id, r.turn_index, r.user_turn_index,
            r.outcome, r.intent, r.execution, r.orchestration, r.expression,
            r.confidence, r.reason[:500],
            judge_model, judge_backend, annotated_at, actor or "",
        )
        for r in req.rewards
    ]
    with pool.conn:
        if req.replace:
            pool.conn.execute(
                "DELETE FROM turn_rewards WHERE experience_id=? AND judge_model=?",
                (req.experience_id, judge_model),
            )
        pool.conn.executemany(
            """INSERT OR REPLACE INTO turn_rewards
               (experience_id, turn_index, user_turn_index,
                r_outcome, r_intent, r_execution, r_orchestration, r_expression,
                confidence, reason, judge_model, judge_backend, annotated_at, annotated_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    # Refresh trajectory_score + eligibility now that rewards changed.
    try:
        from . import quality as quality_mod
        q = quality_mod.recompute_quality(pool.conn, experience_id=req.experience_id)
    except Exception:
        q = {}
    return {
        "experience_id": req.experience_id,
        "rewards_stored": len(rows),
        "judge_model": judge_model,
        "judge_backend": judge_backend,
        "annotated_at": annotated_at,
        "annotated_by": actor,
        "trajectory_score": q.get("trajectory_score"),
        "is_memory_eligible": q.get("is_memory_eligible"),
        "is_sft_eligible": q.get("is_sft_eligible"),
    }


@app.get("/v1/lite/rewards/{experience_id}")
async def lite_rewards_get(
    experience_id: str,
    request: Request,
    judge_model: str | None = None,
) -> dict[str, Any]:
    pool = _pool()
    sql = "SELECT * FROM turn_rewards WHERE experience_id=?"
    params: list[Any] = [experience_id]
    if judge_model:
        sql += " AND judge_model=?"
        params.append(judge_model)
    sql += " ORDER BY turn_index ASC"
    pool.conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in pool.conn.execute(sql, params).fetchall()]
    summary: dict[str, Any] = {}
    if rows:
        dims = ("outcome", "intent", "execution", "orchestration", "expression")
        means = {d: round(sum(r[f"r_{d}"] for r in rows) / len(rows), 3) for d in dims}
        weights = {"outcome": 0.35, "intent": 0.20, "execution": 0.20,
                   "orchestration": 0.10, "expression": 0.15}
        weighted = sum(means[d] * w for d, w in weights.items())
        summary = {
            "n": len(rows),
            "mean": means,
            "trajectory_score": round(weighted, 3),
            "confidence_mean": round(sum(r["confidence"] for r in rows) / len(rows), 3),
            "judges": sorted({r["judge_model"] for r in rows}),
        }
    return {"experience_id": experience_id, "rewards": rows, "summary": summary}


@app.get("/v1/admin/dashboard")
async def admin_dashboard(request: Request) -> dict[str, Any]:
    return dashboard_stats(_pool())


@app.get("/v1/admin/healthz")
async def admin_healthz(request: Request):
    payload, status_code = _health_payload(deep=True)
    return JSONResponse(payload, status_code=status_code)


@app.get("/v1/admin/leaderboard")
async def admin_leaderboard(request: Request, top_k: int = 20) -> list[dict[str, Any]]:
    return reuse_leaderboard(_pool(), top_k=top_k)


@app.get("/v1/admin/usage")
async def admin_usage(request: Request) -> dict[str, Any]:
    """Aggregate LLM token usage from auto-labeling."""
    pool = _pool()
    auto_label_mod.ensure_schema(pool.conn)
    return auto_label_mod.usage_stats(pool.conn)


@app.get("/v1/admin/opf-status")
async def admin_opf_status(request: Request) -> dict[str, Any]:
    """Report whether the OpenAI Privacy Filter (Layer 1.5) is loaded.

    Exposed so operators can confirm OPF actually started after a rebuild
    — when this returns loaded=false in production, sanitize falls back
    to regex-only and the audit_log records the reason in opf_status.
    """
    from . import opf_filter
    return opf_filter.status()


@app.get("/v1/admin/clusters")
async def admin_clusters(request: Request) -> dict[str, Any]:
    """Knowledge-cluster + crystallized-skill stats."""
    from . import crystallize as crystal_mod
    pool = _pool()
    return crystal_mod.cluster_stats(pool.conn)


@app.post("/v1/admin/crystallize")
async def admin_crystallize(req: dict[str, Any], request: Request) -> dict[str, Any]:
    """Force-crystallize a cluster (or all eligible clusters)."""
    from . import crystallize as crystal_mod
    pool = _pool()
    cid = req.get("cluster_id")
    if cid:
        return crystal_mod.crystallize_cluster(pool.conn, cluster_id=cid)
    # Crystallize all eligible
    rows = pool.conn.execute(
        """SELECT cluster_id FROM knowledge_clusters
           WHERE (crystallized_skill_id IS NULL AND member_count >= ?)
              OR (crystallized_skill_id IS NOT NULL AND new_since_crystallize >= ?)""",
        (crystal_mod.MIN_MEMBERS_TO_CRYSTALLIZE,
         crystal_mod.RECRYSTALLIZE_AFTER),
    ).fetchall()
    out = []
    for (cid,) in rows[:10]:
        try:
            r = crystal_mod.crystallize_cluster(pool.conn, cluster_id=cid)
            out.append(r)
        except Exception as e:
            out.append({"cluster_id": cid, "error": str(e)[:120]})
    return {"crystallized": len(out), "results": out}


@app.post("/v1/admin/auto-label")
async def admin_auto_label(req: dict[str, Any], request: Request,
                           background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Manually trigger auto-labeling.

    Body options:
      experience_ids: explicit list (highest priority)
      filter: 'missing' | 'missing_for_current_model' | 'all' (default missing_for_current_model)
      limit: int (default 100)
    """
    pool = _pool()
    eids = req.get("experience_ids")
    if not eids:
        filt = req.get("filter", "missing_for_current_model")
        limit = int(req.get("limit", 100))
        if filt == "all":
            rows = pool.conn.execute(
                "SELECT experience_id FROM experiences ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        elif filt == "missing":
            rows = pool.conn.execute(
                """SELECT e.experience_id FROM experiences e
                   LEFT JOIN turn_rewards r ON r.experience_id = e.experience_id
                   WHERE r.experience_id IS NULL
                   ORDER BY e.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        else:  # missing_for_current_model — default
            current = os.environ.get("EXP_AUTO_LABEL_MODEL", "")
            rows = pool.conn.execute(
                """SELECT e.experience_id FROM experiences e
                   WHERE NOT EXISTS (
                     SELECT 1 FROM turn_rewards r
                     WHERE r.experience_id = e.experience_id AND r.judge_model = ?
                   )
                   ORDER BY e.created_at DESC LIMIT ?""",
                (current, limit),
            ).fetchall()
        eids = [r[0] for r in rows]
    db_path = str(pool.config.db_path)
    for eid in eids:
        background_tasks.add_task(_safe_auto_label, db_path, eid)
    return {"queued": len(eids), "experience_ids": eids[:20]}

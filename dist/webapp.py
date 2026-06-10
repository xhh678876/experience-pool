"""Experience Pool — UI webapp.

Wraps the core ExperiencePool directly (no HMAC) and serves a single-page
app at `/`. Run with:

    EXP_ROOT=/tmp/expool-prod EXP_LLM=mock \
        uvicorn dist.webapp:app --host 0.0.0.0 --port 8765

This is the "operator-side" UI: meant to run on a trusted box, no auth.
For agent-facing access keep using `exp_core.server` which signs requests.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from exp_core.lite import LiteCard, push_lite, search_lite
from exp_core.monitoring import dashboard_stats, reuse_leaderboard
from exp_core.pool import ExperiencePool, PoolConfig
from exp_core.acl_search import get_with_acl, search_with_acl

ROOT = Path(__file__).parent
SPA_HTML = ROOT / "app.html"

POOL: ExperiencePool | None = None


def _pool() -> ExperiencePool:
    global POOL
    if POOL is None:
        root = Path(os.getenv("EXP_ROOT", str(Path.home() / ".experience-pool")))
        POOL = ExperiencePool(PoolConfig(root=root))
    return POOL


app = FastAPI(title="Experience Pool — Operator UI", version="0.6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- helpers --------------------------------------------------------------


def _ensure_agent(name: str, team: str = "platform") -> str:
    p = _pool()
    aid = p.register_agent(name, team)
    return aid


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def _serialize_experience(row: Any) -> dict[str, Any]:
    d = _row_dict(row)
    for k in ("script_steps", "tool_capabilities", "pitfalls", "tags",
              "key_decisions", "preconditions"):
        v = d.get(k)
        if isinstance(v, str) and v:
            try:
                d[k] = json.loads(v)
            except json.JSONDecodeError:
                pass
    return d


# ----- request models -------------------------------------------------------


class RegisterReq(BaseModel):
    name: str
    team: str = "platform"


class PushReq(BaseModel):
    agent_name: str = "ui-admin"
    task_type: str
    source_model: str = "claude-haiku-4-5-20251001"
    trajectory: list[dict[str, Any]]
    parent_experience_ids: list[str] = Field(default_factory=list)
    uses_skills: list[str] = Field(default_factory=list)
    sensitivity: str = "medium"
    acl: str = "org"
    tags: list[str] = Field(default_factory=list)


class LitePushReq(BaseModel):
    agent_name: str = "ui-admin"
    query: str
    intent: str
    steps: list[str]
    outcome: str
    task_type: str = "misc"
    source_model: str = "manual"
    sensitivity: str = "low"
    acl: str = "org"
    tags: list[str] = Field(default_factory=list)


class PushSkillFromFilesReq(BaseModel):
    agent_name: str = "ui-admin"
    files: list[dict[str, str]]  # [{path: "...", content: "..."}]
    sensitivity: str = "low"
    acl: str = "org"
    tags: list[str] = Field(default_factory=list)


class ReviewReq(BaseModel):
    reviewer: str = "ui-admin"
    reason: str | None = None


# ----- routes: meta + dashboard ---------------------------------------------


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    p = _pool()
    return {
        "version": "0.6.0",
        "root": str(p.config.root),
        "db_path": str(p.config.db_path),
        "llm_backend": os.getenv("EXP_LLM", "claude"),
        "agent_default": "ui-admin",
        "team_default": "platform",
    }


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    p = _pool()
    try:
        stats = dashboard_stats(p)
    except Exception as exc:
        return {"error": str(exc), "trace": traceback.format_exc()}
    # leaderboard as well
    try:
        leaderboard = reuse_leaderboard(p, top_k=10)
    except Exception:
        leaderboard = []
    # disk
    usage = shutil.disk_usage(p.config.root)
    counts = {
        "experiences": p.conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0],
        "skills": p.conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0],
        "agents": p.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
        "audit_log": p.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
    }
    return {
        "stats": stats,
        "leaderboard": leaderboard,
        "counts": counts,
        "disk": {
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_percent": round(usage.free * 100 / usage.total, 2) if usage.total else 0,
        },
    }


@app.get("/api/agents")
def list_agents() -> list[dict[str, Any]]:
    p = _pool()
    cur = p.conn.execute(
        "SELECT agent_id, name, team, created_at FROM agents ORDER BY created_at DESC"
    )
    return [dict(r) for r in cur.fetchall()]


@app.post("/api/agents/register")
def register_agent(req: RegisterReq) -> dict[str, Any]:
    aid = _ensure_agent(req.name, req.team)
    return {"agent_id": aid, "name": req.name, "team": req.team}


# ----- routes: experiences --------------------------------------------------


@app.get("/api/experiences")
def list_experiences(limit: int = 50, status: str | None = None,
                     task_type: str | None = None) -> list[dict[str, Any]]:
    p = _pool()
    q = (
        "SELECT experience_id, agent_id, task_type, source_model, sensitivity, acl, "
        "intent_text, summary, review_status, sanitization_status, extraction_status, "
        "q_outcome, q_intent, q_execution, q_orchestration, q_expression, "
        "q_update_count, reuse_count, visit_count, ingest_path, created_at "
        "FROM experiences WHERE 1=1 "
    )
    params: list[Any] = []
    if status:
        q += " AND review_status = ?"
        params.append(status)
    if task_type:
        q += " AND task_type = ?"
        params.append(task_type)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = p.conn.execute(q, params)
    rows = [dict(r) for r in cur.fetchall()]
    # join agent name
    if rows:
        agent_ids = {r["agent_id"] for r in rows if r["agent_id"]}
        if agent_ids:
            q_in = ",".join("?" * len(agent_ids))
            cur2 = p.conn.execute(
                f"SELECT agent_id, name, team FROM agents WHERE agent_id IN ({q_in})",
                tuple(agent_ids),
            )
            am = {a["agent_id"]: dict(a) for a in cur2.fetchall()}
            for r in rows:
                a = am.get(r["agent_id"]) or {}
                r["agent_name"] = a.get("name")
                r["agent_team"] = a.get("team")
    return rows


@app.get("/api/experiences/search")
def api_search(q: str, top_k: int = 10, task_type: str | None = None,
               viewer: str = "ui-admin") -> dict[str, Any]:
    p = _pool()
    _ensure_agent(viewer)
    try:
        results = search_with_acl(p, viewer, q, top_k=top_k, task_type=task_type)
    except Exception as exc:
        raise HTTPException(500, f"search failed: {exc!s}") from exc
    try:
        lite_results = search_lite(p.conn, viewer_name=viewer, query=q,
                                   top_k=top_k, task_type=task_type)
    except Exception:
        lite_results = []
    return {"results": results, "lite_results": lite_results}


@app.get("/api/experiences/{eid}")
def get_experience(eid: str) -> dict[str, Any]:
    p = _pool()
    row = p.conn.execute(
        "SELECT * FROM experiences WHERE experience_id = ?", (eid,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"unknown experience: {eid}")
    out = _serialize_experience(row)

    # rewards
    rew = p.conn.execute(
        "SELECT * FROM rewards WHERE experience_id = ? ORDER BY created_at DESC",
        (eid,),
    ).fetchall()
    out["rewards"] = [dict(r) for r in rew]

    # parents / children
    parents = p.conn.execute(
        "SELECT parent_id FROM experience_edges WHERE child_id = ?", (eid,),
    ).fetchall()
    children = p.conn.execute(
        "SELECT child_id FROM experience_edges WHERE parent_id = ?", (eid,),
    ).fetchall()
    out["parent_ids"] = [r["parent_id"] for r in parents]
    out["child_ids"] = [r["child_id"] for r in children]

    # q updates
    qu = p.conn.execute(
        "SELECT * FROM q_updates WHERE experience_id = ? ORDER BY created_at DESC LIMIT 50",
        (eid,),
    ).fetchall()
    out["q_updates"] = [dict(r) for r in qu]

    # audit
    au = p.conn.execute(
        "SELECT * FROM audit_log WHERE target_id = ? ORDER BY created_at ASC",
        (eid,),
    ).fetchall()
    out["audit"] = [dict(r) for r in au]

    # raw + sanitized trajectory
    traj_path = Path(out.get("trajectory_path") or "")
    raw_path = traj_path.with_suffix(".raw.json") if traj_path.suffix else None
    if traj_path.exists():
        try:
            out["trajectory"] = json.loads(traj_path.read_text())
        except Exception:
            out["trajectory"] = {"_raw": traj_path.read_text()}
    if raw_path and raw_path.exists():
        try:
            out["trajectory_raw"] = json.loads(raw_path.read_text())
        except Exception:
            out["trajectory_raw"] = {"_raw": raw_path.read_text()}

    # skill uses
    su = p.conn.execute(
        """SELECT esu.skill_id, s.name, s.version, esu.created_at
           FROM experience_skill_uses esu
           LEFT JOIN skills s USING(skill_id)
           WHERE esu.experience_id = ?""",
        (eid,),
    ).fetchall()
    out["skills_used"] = [dict(r) for r in su]
    return out


@app.post("/api/experiences/push")
def push_experience(req: PushReq) -> dict[str, Any]:
    p = _pool()
    _ensure_agent(req.agent_name)
    try:
        return p.push(
            agent_name=req.agent_name,
            task_type=req.task_type,
            source_model=req.source_model,
            trajectory=req.trajectory,
            parent_experience_ids=req.parent_experience_ids,
            uses_skills=req.uses_skills,
            sensitivity=req.sensitivity,
            acl=req.acl,
            tags=req.tags,
        )
    except Exception as exc:
        raise HTTPException(500, f"push failed: {exc!s}") from exc


@app.post("/api/experiences/push-lite")
def push_experience_lite(req: LitePushReq) -> dict[str, Any]:
    p = _pool()
    _ensure_agent(req.agent_name)
    card = LiteCard(
        query=req.query,
        intent=req.intent,
        steps=req.steps,
        outcome=req.outcome,
        task_type=req.task_type,
        source_model=req.source_model,
        sensitivity=req.sensitivity,
        acl=req.acl,
        tags=req.tags,
    )
    return push_lite(p.conn, rules=p._sanitize_rules,
                     agent_name=req.agent_name, card=card)


@app.post("/api/experiences/{eid}/approve")
def approve(eid: str, req: ReviewReq) -> dict[str, Any]:
    p = _pool()
    cur = p.conn.execute(
        "UPDATE experiences SET review_status='approved' "
        "WHERE experience_id = ? AND review_status IN ('pending','auto_approved')",
        (eid,),
    )
    p.conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"reviewer:{req.reviewer}", "review", "approve", eid,
         json.dumps({"reason": req.reason or ""}, ensure_ascii=False)),
    )
    p.conn.commit()
    return {"updated": cur.rowcount}


@app.post("/api/experiences/{eid}/reject")
def reject(eid: str, req: ReviewReq) -> dict[str, Any]:
    p = _pool()
    cur = p.conn.execute(
        "UPDATE experiences SET review_status='rejected' WHERE experience_id = ?",
        (eid,),
    )
    p.conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"reviewer:{req.reviewer}", "review", "reject", eid,
         json.dumps({"reason": req.reason or ""}, ensure_ascii=False)),
    )
    p.conn.commit()
    return {"updated": cur.rowcount}


# ----- routes: skills -------------------------------------------------------


@app.get("/api/skills")
def api_list_skills(limit: int = 100) -> list[dict[str, Any]]:
    return _pool().list_skills(limit=limit)


@app.get("/api/skills/search")
def api_search_skills_route(q: str, top_k: int = 10) -> dict[str, Any]:
    p = _pool()
    return {"results": p.search_skills(q, top_k=top_k)}


@app.get("/api/skills/{name}/install")
def api_install_skill_route(name: str, version: str | None = None) -> dict[str, Any]:
    from exp_core import skills as skills_mod
    p = _pool()
    row = skills_mod.resolve_skill(p.conn, name, version)
    if row is None:
        raise HTTPException(404, f"unknown skill: {name}")
    bundle_path = Path(row["bundle_path"])
    if not bundle_path.exists():
        raise HTTPException(500, f"bundle missing: {bundle_path}")
    raw = bundle_path.read_bytes()
    files: list[dict[str, Any]] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            fobj = tar.extractfile(m)
            content = fobj.read().decode("utf-8", errors="replace") if fobj else ""
            files.append({"path": m.name, "size": m.size, "content": content})
    p.conn.execute(
        "UPDATE skills SET install_count = install_count + 1 WHERE skill_id = ?",
        (row["skill_id"],),
    )
    p.conn.commit()
    return {
        "skill_id": row["skill_id"],
        "name": row["name"],
        "version": row["version"],
        "bundle_sha256": row["bundle_sha256"],
        "bundle_b64": base64.b64encode(raw).decode("ascii"),
        "files": files,
    }


@app.get("/api/skills/{name}")
def api_get_skill(name: str, version: str | None = None) -> dict[str, Any]:
    from exp_core import skills as skills_mod
    p = _pool()
    row = skills_mod.resolve_skill(p.conn, name, version)
    if row is None:
        raise HTTPException(404, f"unknown skill: {name}")
    out = dict(row)
    # skill q updates
    qu = p.conn.execute(
        "SELECT * FROM skill_q_updates WHERE skill_id = ? ORDER BY created_at DESC LIMIT 50",
        (out["skill_id"],),
    ).fetchall()
    out["q_updates"] = [dict(r) for r in qu]
    # uses
    uses = p.conn.execute(
        """SELECT esu.experience_id, e.intent_text, e.task_type
           FROM experience_skill_uses esu LEFT JOIN experiences e USING(experience_id)
           WHERE esu.skill_id = ? ORDER BY esu.created_at DESC LIMIT 30""",
        (out["skill_id"],),
    ).fetchall()
    out["used_by"] = [dict(r) for r in uses]
    return out


@app.post("/api/skills/push-from-files")
def push_skill_from_files(req: PushSkillFromFilesReq) -> dict[str, Any]:
    """Build a bundle from a list of {path, content} pairs (no tar.gz needed)."""
    p = _pool()
    _ensure_agent(req.agent_name)
    if not req.files:
        raise HTTPException(400, "no files")
    has_skill_md = any(
        Path(f["path"]).name == "SKILL.md" or f["path"] == "SKILL.md"
        for f in req.files
    )
    if not has_skill_md:
        raise HTTPException(400, "bundle must contain SKILL.md at root")
    with tempfile.TemporaryDirectory(prefix="ui_skill_") as tmp:
        tmp_path = Path(tmp).resolve()
        for f in req.files:
            rel = (f.get("path") or "").lstrip("/")
            if not rel or ".." in Path(rel).parts:
                raise HTTPException(400, f"bad path: {rel!r}")
            target = (tmp_path / rel).resolve()
            if not str(target).startswith(str(tmp_path) + os.sep):
                raise HTTPException(400, f"unsafe path: {rel!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.get("content") or "", encoding="utf-8")
        try:
            return p.push_skill(
                agent_name=req.agent_name,
                bundle_dir=tmp_path,
                sensitivity=req.sensitivity,
                acl=req.acl,
                tags=req.tags,
            )
        except Exception as exc:
            raise HTTPException(500, f"push_skill failed: {exc!s}") from exc


# ----- routes: audit + admin -----------------------------------------------


@app.get("/api/audit")
def api_audit(limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
    p = _pool()
    q = "SELECT * FROM audit_log WHERE 1=1 "
    params: list[Any] = []
    if kind:
        q += " AND actor_kind = ?"
        params.append(kind)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = p.conn.execute(q, params)
    out = []
    for r in cur.fetchall():
        d = dict(r)
        if isinstance(d.get("payload"), str) and d["payload"]:
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
        out.append(d)
    return out


@app.post("/api/seed/demo")
def seed_demo() -> dict[str, Any]:
    """Push a few demo experiences + a skill for tire-kicking."""
    p = _pool()
    _ensure_agent("alice", "platform")
    _ensure_agent("bob", "data")
    out: dict[str, Any] = {"experiences": [], "skills": []}

    # Skill bundle (push from files)
    skill_files = [
        {"path": "SKILL.md", "content":
         "---\nname: csv-helper\ndescription: Aggregate revenue by region "
         "from a wide CSV. Returns top-K dimensions with totals.\n---\n\n"
         "# csv-helper\n\nUse pandas groupby(region).sum().nlargest(K)."},
        {"path": "snippet.py", "content":
         "import pandas as pd\n\n"
         "def top_regions(path, k=5):\n"
         "    df = pd.read_csv(path)\n"
         "    return df.groupby('region')['revenue'].sum().nlargest(k)\n"},
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="seed_skill_") as tmp:
            tmp_path = Path(tmp).resolve()
            for f in skill_files:
                target = tmp_path / f["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f["content"])
            res = p.push_skill(
                agent_name="alice",
                bundle_dir=tmp_path,
                sensitivity="low", acl="org",
                tags=["demo", "csv"],
            )
            out["skills"].append(res)
    except Exception as exc:
        out["skill_error"] = str(exc)

    # Experiences
    demos = [
        {
            "agent": "alice",
            "task": "csv_analysis",
            "trajectory": [
                {"role": "user", "content": "Compute revenue by region from sales.csv, top 5"},
                {"role": "assistant", "content": "I'll use pandas groupby and nlargest"},
                {"role": "tool", "content": "df.groupby('region')['revenue'].sum().nlargest(5)"},
                {"role": "assistant", "content": "Top regions: APAC 1.2M, EMEA 0.9M, NA 0.8M"},
            ],
            "skills": ["csv-helper"],
            "tags": ["demo", "csv"],
        },
        {
            "agent": "bob",
            "task": "kafka_debug",
            "trajectory": [
                {"role": "user", "content": "consumer group lag stuck at 50k, how to reset"},
                {"role": "assistant", "content": "Use kafka-consumer-groups.sh --reset-offsets --to-earliest"},
                {"role": "tool", "content": "kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group g1 --reset-offsets --to-earliest --execute --all-topics"},
                {"role": "assistant", "content": "Lag cleared after offset reset"},
            ],
            "skills": [],
            "tags": ["demo", "kafka"],
        },
        {
            "agent": "alice",
            "task": "csv_analysis",
            "trajectory": [
                {"role": "user", "content": "rank top dimensions in tabular data"},
                {"role": "assistant", "content": "Same playbook: groupby + nlargest"},
                {"role": "tool", "content": "df.groupby(col).agg(metric).nlargest(k)"},
                {"role": "assistant", "content": "Reused csv-helper, parent=<previous>"},
            ],
            "skills": ["csv-helper"],
            "tags": ["demo"],
        },
    ]
    pushed_ids: list[str] = []
    for i, d in enumerate(demos):
        try:
            parents = [pushed_ids[0]] if i == 2 and pushed_ids else []
            res = p.push(
                agent_name=d["agent"],
                task_type=d["task"],
                source_model="claude-haiku-4-5-20251001",
                trajectory=d["trajectory"],
                parent_experience_ids=parents,
                uses_skills=d["skills"],
                sensitivity="low",
                acl="org",
                tags=d["tags"],
            )
            pushed_ids.append(res.get("experience_id", ""))
            out["experiences"].append(res)
        except Exception as exc:
            out.setdefault("errors", []).append(str(exc))
    return out


@app.post("/api/admin/wipe")
def admin_wipe() -> dict[str, Any]:
    """Dangerous: clear the whole pool. Requires X-Confirm header."""
    p = _pool()
    tables = ["audit_log", "search_log", "q_updates", "skill_q_updates",
              "experience_skill_uses", "experience_edges", "vectors",
              "rewards", "experiences", "skills", "agents"]
    for t in tables:
        try:
            p.conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    p.conn.commit()
    # files
    for sub in ("trajectories", "skills"):
        d = p.config.root / sub
        if d.exists():
            shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)
    return {"wiped": True}


# ----- static SPA ----------------------------------------------------------


@app.get("/")
def index():
    if SPA_HTML.exists():
        return FileResponse(SPA_HTML, media_type="text/html")
    return Response("SPA not built", status_code=500)


@app.get("/preview")
def preview():
    pv = ROOT / "preview.html"
    if pv.exists():
        return FileResponse(pv, media_type="text/html")
    raise HTTPException(404, "preview.html not found")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    p = _pool()
    p.conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "ts": int(time.time())}

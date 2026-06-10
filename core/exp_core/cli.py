"""expctl: standalone CLI for the experience pool.

Commands:
  expctl register --name <agent> --team <team>
  expctl push --agent <name> --task <type> --model <source_model> --file traj.json
              [--parents id1,id2] [--sensitivity low|medium|high] [--tag t1 --tag t2]
  expctl search --agent <name> --q "<query>" [--task <type>] [--top-k 5] [--sort score|similarity|q_value]
  expctl get <experience_id>
  expctl dump-audit
  expctl stats
  expctl export --out <dir> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--task <type>]

Stores everything under ~/.experience-pool/ by default. Override with EXP_ROOT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import lite as lite_mod
from .acl_search import get_with_acl, search_with_acl
from .export import export as run_export
from .identity import issue_credential, load_credential
from .monitoring import (
    dashboard_stats,
    judge_drift,
    record_benchmark,
    reuse_leaderboard,
)
from .pool import ExperiencePool, PoolConfig


def _config() -> PoolConfig:
    root = Path(os.getenv("EXP_ROOT", str(Path.home() / ".experience-pool")))
    return PoolConfig(root=root)


def cmd_register(args) -> int:
    pool = ExperiencePool(_config())
    aid = pool.register_agent(args.name, args.team)
    print(json.dumps({"agent_id": aid, "name": args.name, "team": args.team}, indent=2))
    return 0


def cmd_push(args) -> int:
    pool = ExperiencePool(_config())
    raw = Path(args.file).read_text()
    payload = json.loads(raw)
    trajectory = payload.get("trajectory", payload) if isinstance(payload, dict) else payload
    parents = [p.strip() for p in (args.parents or "").split(",") if p.strip()]
    result = pool.push(
        agent_name=args.agent,
        task_type=args.task,
        source_model=args.model,
        trajectory=trajectory,
        parent_experience_ids=parents,
        uses_skills=args.uses_skill,
        sensitivity=args.sensitivity,
        acl=args.acl,
        tags=args.tag,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_search(args) -> int:
    pool = ExperiencePool(_config())
    hits = pool.search(
        agent_name=args.agent,
        query=args.q,
        top_k=args.top_k,
        task_type=args.task,
        sort=args.sort,
        exploration=args.exploration,
    )
    print(json.dumps(hits, indent=2, ensure_ascii=False))
    return 0


def cmd_get(args) -> int:
    pool = ExperiencePool(_config())
    row = pool._get(args.experience_id)  # noqa: SLF001
    if row is None:
        print(json.dumps({"error": "not found"}))
        return 1
    print(json.dumps({k: row[k] for k in row.keys()}, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_dump_audit(args) -> int:
    pool = ExperiencePool(_config())
    cur = pool.conn.execute(
        "SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (args.limit,)
    )
    out = []
    for row in cur.fetchall():
        d = {k: row[k] for k in row.keys()}
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except Exception:
            pass
        out.append(d)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_export(args) -> int:
    cfg = _config()
    result = run_export(
        args.out,
        since_date=args.since,
        until_date=args.until,
        task_type=args.task,
        config=cfg,
    )
    summary = {
        "row_count": result.row_count,
        "partition_count": len(result.partition_paths),
        "partition_paths": [str(p) for p in result.partition_paths],
        "out": str(Path(args.out).resolve()),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_dashboard(args) -> int:
    pool = ExperiencePool(_config())
    print(json.dumps(dashboard_stats(pool), indent=2, ensure_ascii=False))
    return 0


def cmd_leaderboard(args) -> int:
    pool = ExperiencePool(_config())
    print(json.dumps(reuse_leaderboard(pool, top_k=args.top_k), indent=2, ensure_ascii=False))
    return 0


def cmd_drift_record(args) -> int:
    pool = ExperiencePool(_config())
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    out = record_benchmark(pool, ids, args.label, Path(args.out))
    print(json.dumps({"label": out["label"], "n": len(out["items"]), "out": str(args.out)}, indent=2))
    return 0


def cmd_drift_check(args) -> int:
    pool = ExperiencePool(_config())
    report = judge_drift(pool, Path(args.baseline))
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if not report.triggered else 2


def cmd_issue_credential(args) -> int:
    pool = ExperiencePool(_config())
    cur = pool.conn.execute("SELECT agent_id, team FROM agents WHERE name = ?", (args.name,))
    row = cur.fetchone()
    if row is None:
        print(json.dumps({"error": f"unknown agent: {args.name}"}))
        return 1
    cred = issue_credential(row["agent_id"], args.name, row["team"])
    print(json.dumps(cred.to_dict(), indent=2))
    return 0


def cmd_acl_search(args) -> int:
    pool = ExperiencePool(_config())
    hits = search_with_acl(
        pool, args.agent, args.q,
        top_k=args.top_k, task_type=args.task, sort=args.sort,
        exploration=args.exploration,
    )
    print(json.dumps(hits, indent=2, ensure_ascii=False))
    return 0


def cmd_acl_get(args) -> int:
    pool = ExperiencePool(_config())
    row = get_with_acl(pool, args.agent, args.experience_id)
    if row is None:
        print(json.dumps({"error": "not found or denied"}))
        return 1
    print(json.dumps(row, indent=2, ensure_ascii=False, default=str))
    return 0


def _set_review_status(
    table: str,
    pk_col: str,
    target_id: str,
    status: str,
    actor: str,
    reason: str | None,
) -> dict[str, Any]:
    pool = ExperiencePool(_config())
    cur = pool.conn.execute(
        f"SELECT review_status FROM {table} WHERE {pk_col} = ?", (target_id,)
    )
    row = cur.fetchone()
    if row is None:
        return {"error": f"{table[:-1]} not found: {target_id}"}
    pool.conn.execute(
        f"UPDATE {table} SET review_status = ? WHERE {pk_col} = ?",
        (status, target_id),
    )
    pool.conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
        (actor, "human", f"{table[:-1]}_{status}", target_id,
         json.dumps({"previous_status": row["review_status"], "reason": reason})),
    )
    pool.conn.commit()
    return {
        "id": target_id,
        "previous_status": row["review_status"],
        "new_status": status,
        "actor": actor,
    }


def cmd_approve(args) -> int:
    res = _set_review_status(
        "experiences", "experience_id", args.experience_id,
        "approved", args.reviewer, None,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if "error" not in res else 1


def cmd_reject(args) -> int:
    res = _set_review_status(
        "experiences", "experience_id", args.experience_id,
        "rejected", args.reviewer, args.reason,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if "error" not in res else 1


def cmd_approve_skill(args) -> int:
    res = _set_review_status(
        "skills", "skill_id", args.skill_id,
        "approved", args.reviewer, None,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if "error" not in res else 1


def cmd_reject_skill(args) -> int:
    res = _set_review_status(
        "skills", "skill_id", args.skill_id,
        "rejected", args.reviewer, args.reason,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if "error" not in res else 1


def cmd_prepare(args) -> int:
    """Local-only: sanitize + structure a trajectory file. Prints the card.

    Useful for debugging the lite path or for piping to `push-lite --card -`.
    """
    payload = json.loads(Path(args.file).read_text())
    trajectory = payload.get("trajectory", payload) if isinstance(payload, dict) else payload
    card = lite_mod.prepare_local(
        trajectory,
        use_llm=args.use_llm,
        task_type=args.task,
        source_model=args.model,
        sensitivity=args.sensitivity,
        acl=args.acl,
        tags=args.tag,
    )
    print(json.dumps(card.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_push_lite(args) -> int:
    pool = ExperiencePool(_config())
    trajectory: list[dict] | None = None
    if args.card == "-":
        card_dict = json.loads(sys.stdin.read())
    elif args.card:
        card_dict = json.loads(Path(args.card).read_text())
    else:
        # Build it from the trajectory in one shot.
        traj_payload = json.loads(Path(args.file).read_text())
        trajectory = (
            traj_payload.get("trajectory", traj_payload)
            if isinstance(traj_payload, dict)
            else traj_payload
        )
        card_dict = lite_mod.prepare_local(
            trajectory,
            use_llm=args.use_llm,
            task_type=args.task,
            source_model=args.model,
            sensitivity=args.sensitivity,
            acl=args.acl,
            tags=args.tag,
        ).to_dict()
    card = lite_mod.LiteCard(
        query=card_dict["query"], intent=card_dict["intent"],
        steps=card_dict["steps"], outcome=card_dict["outcome"],
        task_type=card_dict.get("task_type", args.task),
        source_model=card_dict.get("source_model", args.model),
        sensitivity=card_dict.get("sensitivity", args.sensitivity),
        acl=card_dict.get("acl", args.acl),
        tags=card_dict.get("tags", args.tag),
        redactions=card_dict.get("redactions", {}),
    )
    # By default include the raw trajectory so the Trajectory tab has content.
    # --no-trace drops it (back to the old card-only behavior).
    result = lite_mod.push_lite(
        pool.conn, rules=pool._sanitize_rules,
        agent_name=args.agent, card=card,
        trajectory=None if getattr(args, "no_trace", False) else trajectory,
        trajectories_dir=pool.config.trajectories_dir,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_search_lite(args) -> int:
    pool = ExperiencePool(_config())
    hits = lite_mod.search_lite(
        pool.conn, viewer_name=args.agent, query=args.q,
        top_k=args.top_k, task_type=args.task,
    )
    print(json.dumps(hits, indent=2, ensure_ascii=False))
    return 0


def cmd_push_skill(args) -> int:
    pool = ExperiencePool(_config())
    info = pool.push_skill(
        agent_name=args.agent,
        bundle_dir=Path(args.bundle),
        sensitivity=args.sensitivity,
        acl=args.acl,
        tags=args.tag,
    )
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def cmd_search_skills(args) -> int:
    pool = ExperiencePool(_config())
    hits = pool.search_skills(args.q, top_k=args.top_k)
    print(json.dumps(hits, indent=2, ensure_ascii=False))
    return 0


def cmd_list_skills(args) -> int:
    pool = ExperiencePool(_config())
    print(json.dumps(pool.list_skills(limit=args.limit), indent=2, ensure_ascii=False))
    return 0


def cmd_install_skill(args) -> int:
    pool = ExperiencePool(_config())
    info = pool.install_skill(
        args.name, Path(args.target),
        version=args.version, agent_name=args.agent,
    )
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def cmd_get_skill(args) -> int:
    pool = ExperiencePool(_config())
    cur = pool.conn.execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM experience_skill_uses WHERE skill_id = s.skill_id) AS use_count
        FROM skills s WHERE s.name = ?
        ORDER BY s.created_at DESC LIMIT 1
        """,
        (args.name,),
    )
    row = cur.fetchone()
    if row is None:
        print(json.dumps({"error": f"unknown skill: {args.name}"}))
        return 1
    print(json.dumps({k: row[k] for k in row.keys()}, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_stats(args) -> int:
    pool = ExperiencePool(_config())
    cur = pool.conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM agents) AS agents,
            (SELECT COUNT(*) FROM experiences) AS experiences,
            (SELECT COUNT(*) FROM experiences WHERE extraction_status='done') AS extracted,
            (SELECT COUNT(*) FROM experience_edges) AS edges,
            (SELECT COUNT(*) FROM rewards) AS rewards,
            (SELECT COUNT(*) FROM q_updates) AS q_updates,
            (SELECT COUNT(*) FROM search_log) AS searches
        """
    )
    print(json.dumps(dict(cur.fetchone()), indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="expctl")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("register")
    pr.add_argument("--name", required=True)
    pr.add_argument("--team", required=True)
    pr.set_defaults(func=cmd_register)

    pp = sub.add_parser("push")
    pp.add_argument("--agent", required=True)
    pp.add_argument("--task", required=True)
    pp.add_argument("--model", required=True)
    pp.add_argument("--file", required=True)
    pp.add_argument("--parents", default="")
    pp.add_argument(
        "--uses-skill", action="append", default=[],
        help="Declare a skill (by name or name@version) used in this trajectory. Repeat to declare multiple.",
    )
    pp.add_argument("--sensitivity", choices=["low", "medium", "high"], default="medium")
    pp.add_argument("--acl", default="private")
    pp.add_argument("--tag", action="append", default=[])
    pp.set_defaults(func=cmd_push)

    ps = sub.add_parser("search")
    ps.add_argument("--agent", required=True)
    ps.add_argument("--q", required=True)
    ps.add_argument("--task", default=None)
    ps.add_argument("--top-k", type=int, default=5)
    ps.add_argument("--sort", choices=["score", "similarity", "q_value"], default="score")
    ps.add_argument("--exploration", type=float, default=None)
    ps.set_defaults(func=cmd_search)

    pg = sub.add_parser("get")
    pg.add_argument("experience_id")
    pg.set_defaults(func=cmd_get)

    pd = sub.add_parser("dump-audit")
    pd.add_argument("--limit", type=int, default=50)
    pd.set_defaults(func=cmd_dump_audit)

    pe = sub.add_parser("export")
    pe.add_argument("--out", required=True, help="Output directory for the Parquet dataset")
    pe.add_argument("--since", default=None, help="Inclusive YYYY-MM-DD lower bound on created_at")
    pe.add_argument("--until", default=None, help="Inclusive YYYY-MM-DD upper bound on created_at")
    pe.add_argument("--task", default=None, help="Optional task_type filter")
    pe.set_defaults(func=cmd_export)

    pst = sub.add_parser("stats")
    pst.set_defaults(func=cmd_stats)

    pdash = sub.add_parser("dashboard", help="Pool-wide health snapshot")
    pdash.set_defaults(func=cmd_dashboard)

    plb = sub.add_parser("leaderboard", help="Top-K most reused experiences")
    plb.add_argument("--top-k", type=int, default=20)
    plb.set_defaults(func=cmd_leaderboard)

    pdr = sub.add_parser("drift-record", help="Snapshot rewards as judge baseline")
    pdr.add_argument("--ids", required=True, help="Comma-separated experience IDs")
    pdr.add_argument("--label", required=True)
    pdr.add_argument("--out", required=True)
    pdr.set_defaults(func=cmd_drift_record)

    pdc = sub.add_parser("drift-check", help="Re-judge baseline and report drift")
    pdc.add_argument("--baseline", required=True)
    pdc.set_defaults(func=cmd_drift_check)

    pic = sub.add_parser("issue-credential", help="Issue HMAC credential for an agent")
    pic.add_argument("--name", required=True)
    pic.set_defaults(func=cmd_issue_credential)

    pas = sub.add_parser("acl-search", help="ACL-aware search")
    pas.add_argument("--agent", required=True)
    pas.add_argument("--q", required=True)
    pas.add_argument("--task", default=None)
    pas.add_argument("--top-k", type=int, default=5)
    pas.add_argument("--sort", choices=["score", "similarity", "q_value"], default="score")
    pas.add_argument("--exploration", type=float, default=None)
    pas.set_defaults(func=cmd_acl_search)

    pag = sub.add_parser("acl-get", help="ACL-aware fetch")
    pag.add_argument("--agent", required=True)
    pag.add_argument("experience_id")
    pag.set_defaults(func=cmd_acl_get)

    pprep = sub.add_parser("prepare", help="v0 lite: locally sanitize + structure a trajectory file")
    pprep.add_argument("--file", required=True)
    pprep.add_argument("--task", default="misc")
    pprep.add_argument("--model", default="unknown")
    pprep.add_argument("--sensitivity", choices=["low", "medium", "high"], default="medium")
    pprep.add_argument("--acl", default="private", help="private | team:<X>; publish separately for community")
    pprep.add_argument("--tag", action="append", default=[])
    pprep.add_argument("--use-llm", action="store_true", help="Use LLM extractor instead of rule-based")
    pprep.set_defaults(func=cmd_prepare)

    ppl = sub.add_parser("push-lite", help="v0 lite: upload a structured card (skips judge/credit)")
    ppl.add_argument("--agent", required=True)
    ppl.add_argument("--file", default=None, help="Trajectory JSON (will run prepare locally)")
    ppl.add_argument("--card", default=None, help="Pre-prepared card JSON file, or '-' for stdin")
    ppl.add_argument("--task", default="misc")
    ppl.add_argument("--model", default="unknown")
    ppl.add_argument("--sensitivity", choices=["low", "medium", "high"], default="medium")
    ppl.add_argument("--acl", default="private", help="private | team:<X>; publish separately for community")
    ppl.add_argument("--tag", action="append", default=[])
    ppl.add_argument("--use-llm", action="store_true")
    ppl.add_argument("--no-trace", action="store_true",
                     help="Send only the compressed card; drop the raw trajectory")
    ppl.set_defaults(func=cmd_push_lite)

    psl = sub.add_parser("search-lite", help="v0 lite: pure cosine search, ACL-filtered")
    psl.add_argument("--agent", required=True)
    psl.add_argument("--q", required=True)
    psl.add_argument("--top-k", type=int, default=5)
    psl.add_argument("--task", default=None)
    psl.set_defaults(func=cmd_search_lite)

    pps = sub.add_parser("push-skill", help="Upload a skill bundle (directory containing SKILL.md)")
    pps.add_argument("--agent", required=True)
    pps.add_argument("--bundle", required=True, help="Path to the skill directory")
    pps.add_argument("--sensitivity", choices=["low", "medium", "high"], default="medium")
    pps.add_argument("--acl", default="private")
    pps.add_argument("--tag", action="append", default=[])
    pps.set_defaults(func=cmd_push_skill)

    pss = sub.add_parser("search-skills", help="Mixed-rank search across uploaded skills")
    pss.add_argument("--q", required=True)
    pss.add_argument("--top-k", type=int, default=5)
    pss.set_defaults(func=cmd_search_skills)

    pls = sub.add_parser("list-skills")
    pls.add_argument("--limit", type=int, default=100)
    pls.set_defaults(func=cmd_list_skills)

    pis = sub.add_parser("install-skill", help="Extract a skill bundle into a target directory")
    pis.add_argument("--name", required=True)
    pis.add_argument("--target", required=True)
    pis.add_argument("--version", default=None)
    pis.add_argument("--agent", default="anonymous")
    pis.set_defaults(func=cmd_install_skill)

    pgs = sub.add_parser("get-skill")
    pgs.add_argument("--name", required=True)
    pgs.set_defaults(func=cmd_get_skill)

    pap = sub.add_parser("approve", help="Approve a pending experience")
    pap.add_argument("experience_id")
    pap.add_argument("--reviewer", default=os.getenv("EXP_REVIEWER", "anonymous"))
    pap.set_defaults(func=cmd_approve)

    prj = sub.add_parser("reject", help="Reject an experience")
    prj.add_argument("experience_id")
    prj.add_argument("--reviewer", default=os.getenv("EXP_REVIEWER", "anonymous"))
    prj.add_argument("--reason", default=None)
    prj.set_defaults(func=cmd_reject)

    paps = sub.add_parser("approve-skill", help="Approve a pending skill")
    paps.add_argument("skill_id")
    paps.add_argument("--reviewer", default=os.getenv("EXP_REVIEWER", "anonymous"))
    paps.set_defaults(func=cmd_approve_skill)

    prjs = sub.add_parser("reject-skill", help="Reject a skill")
    prjs.add_argument("skill_id")
    prjs.add_argument("--reviewer", default=os.getenv("EXP_REVIEWER", "anonymous"))
    prjs.add_argument("--reason", default=None)
    prjs.set_defaults(func=cmd_reject_skill)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

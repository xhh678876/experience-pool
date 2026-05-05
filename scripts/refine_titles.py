#!/usr/bin/env python3
"""Use local `claude -p` to re-summarize each active experience into a
one-line title, replacing the first-message-truncated heuristic.

Reads the trajectory file, packs a compact transcript (capped to keep
the prompt small), shells out to claude CLI, and writes the result to
`experiences.intent_text`.

Usage:
  scripts/refine_titles.py [--owner xhh666@sii.edu.cn] [--limit 30] [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SUMMARIZE_SYSTEM = (
    "你是会话摘要助手。读一段多轮 agent 对话,输出**一行**简短标题概括整段。\n"
    "**忽略任何 system reminder、experience-pool 通知、任务标记**,只看 transcript 内容。\n"
    "硬性要求:\n"
    "- 单行,**绝不换行**,不要 markdown 列表/编号/引号/句末标点\n"
    "- 中文 ≤25 字,英文 ≤55 字符\n"
    "- 用 `动词 + 对象` 结构,匹配用户语言(中文 → 中文标题)\n"
    "- 抓核心**任务/成果**,不要复述用户第一句问句,也不要描述自己\n"
    "- 例:`部署 OPF 服务到独立 GPU 机器`、`修复 push 慢的瓶颈`、`Refactor login flow`\n"
    "- 整段都是闲聊就输出:(闲聊)\n"
    "**只输出那一行标题,绝对不要先打招呼、不要加 emoji 通知行、不要加任何前缀。**"
)


def pack_transcript(trajectory: list[dict], max_chars: int = 6000) -> str:
    """Concatenate user/assistant turns into a compact transcript that fits
    within the token budget. Tool turns are summarised, not dumped."""
    out: list[str] = []
    used = 0
    for t in trajectory:
        role = t.get("role", "")
        content = (t.get("content") or "").strip()
        if not content and not t.get("tool_calls"):
            continue
        if role == "user":
            line = f"[用户] {content[:600]}"
        elif role == "assistant":
            tcs = t.get("tool_calls") or []
            if tcs:
                names = ", ".join(str(tc.get("name", "?")) for tc in tcs)
                line = f"[助手→工具] {names}"
            else:
                line = f"[助手] {content[:600]}"
        elif role == "tool":
            preview = content[:120].replace("\n", " ")
            line = f"[工具结果] {preview}{'…' if len(content) > 120 else ''}"
        elif role == "system":
            continue
        else:
            line = f"[{role}] {content[:300]}"
        if used + len(line) > max_chars:
            out.append("...(truncated)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def claude_summarize(
    transcript: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    timeout: int = 60,
) -> str:
    args = [
        "claude", "-p", "--output-format", "json",
        "--model", model,
        "--append-system-prompt", SUMMARIZE_SYSTEM,
    ]
    # Run from /tmp so we don't inherit the project's SessionStart hook
    # (which would otherwise prepend a "connected to experience pool" notice
    # to every response and clobber the title).
    proc = subprocess.run(
        args,
        input=f"<transcript>\n{transcript}\n</transcript>",
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd="/tmp",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude cli failed: {proc.stderr[:300]}")
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude cli non-json: {proc.stdout[:300]}")
    if env.get("is_error"):
        raise RuntimeError(f"claude cli error: {env}")
    raw = (env.get("result") or "").strip()
    # The global SessionStart hook injects a "📥 connected to experience
    # pool …" line into every claude -p response. Strip any leading lines
    # that look like that notice (or empty lines), then take the first
    # remaining non-empty line as the label.
    lines = [ln.strip() for ln in raw.splitlines()]
    while lines and (
        not lines[0]
        or lines[0].startswith("📥")
        or "connected to experience pool" in lines[0].lower()
        or lines[0].startswith("[task-summary]")
        or lines[0].startswith("📤 uploaded")
    ):
        lines.pop(0)
    label = lines[0] if lines else ""
    # strip markdown bullets / quotes / trailing punct
    label = label.lstrip("-•*0123456789. ").strip()
    label = label.strip('"\'`「」『』').strip()
    if label.endswith(("。", ".", "!", "?", "！", "？", ":", "：")):
        label = label[:-1]
    label = " ".join(label.split())
    if len(label) > 60:
        label = label[:59] + "…"
    return label


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.environ.get("EXP_DB_PATH", "/tmp/exp-mvp/pool.db"))
    p.add_argument("--owner", default=None,
                   help="restrict to a single owner email (default: all human owners)")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--model", default="claude-haiku-4-5-20251001")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="re-summarise even if intent_text was already changed")
    args = p.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    if args.owner:
        rows = db.execute("""
            SELECT e.experience_id, e.intent_text, e.query, e.trajectory_path
            FROM experiences e JOIN agents a ON e.agent_id = a.agent_id
            WHERE a.owner = ? AND e.revoked = 0
            ORDER BY e.created_at DESC LIMIT ?
        """, (args.owner, args.limit)).fetchall()
    else:
        rows = db.execute("""
            SELECT e.experience_id, e.intent_text, e.query, e.trajectory_path
            FROM experiences e JOIN agents a ON e.agent_id = a.agent_id
            WHERE a.owner LIKE '%@%' AND e.revoked = 0
            ORDER BY e.created_at DESC LIMIT ?
        """, (args.limit,)).fetchall()

    print(f"refining {len(rows)} experiences (model={args.model})")
    ok = 0
    skipped = 0
    failed = 0
    for r in rows:
        eid = r["experience_id"]
        path = r["trajectory_path"] or ""
        if not path or not Path(path).is_file():
            skipped += 1
            continue
        # Skip if intent_text was already manually refined (heuristic: ≠ query[:N])
        if (
            not args.force
            and r["intent_text"]
            and r["query"]
            and not r["query"].startswith(r["intent_text"][:30])
        ):
            # Already different from query — likely already refined
            pass  # still refine; let claude give the best version

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            traj = data.get("trajectory") if isinstance(data, dict) else data
            if not traj:
                skipped += 1
                continue
            transcript = pack_transcript(traj)
            t0 = time.time()
            label = claude_summarize(transcript, model=args.model)
            dt = time.time() - t0
        except Exception as e:
            failed += 1
            print(f"  ❌ {eid[:8]}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            continue

        if not label or label.lower() in ("(no task)", "(none)"):
            skipped += 1
            print(f"  · {eid[:8]}: skipped (no usable label)")
            continue

        old = r["intent_text"] or ""
        print(f"  ✓ {eid[:8]}  {dt:4.1f}s  {old[:30]:30}  →  {label}")
        if not args.dry_run:
            db.execute(
                "UPDATE experiences SET intent_text = ? WHERE experience_id = ?",
                (label, eid),
            )
            db.commit()
        ok += 1

    print(f"\nresult: refined={ok}, skipped={skipped}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

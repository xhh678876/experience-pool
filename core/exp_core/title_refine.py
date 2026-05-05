"""Server-side post-push title refinement using local `claude -p`.

Wired as a FastAPI BackgroundTask after `/v1/lite/push`. For each new
experience this module:

  1. Reads the just-stored trajectory sidecar.
  2. Packs a compact transcript (capped at ~6 KB).
  3. Shells out to `claude -p` with a strict one-line-title system prompt.
  4. Filters out conversational / hook-injected / echo-input garbage.
  5. Updates `experiences.intent_text` if the new title looks usable.

Concerns handled:

- Title prober recursion — every spawned `claude -p` runs with
  `EXP_AUTO_UPLOAD=0 EXP_TITLE_DISABLE=1` env and `--no-session-persistence`,
  so its SessionEnd hook is a no-op and its session file isn't written to
  ~/.claude/projects (otherwise daemon-tick would suck them up as new
  sessions and create `<transcript>`-titled rows).
- Hook contamination — the global SessionStart hook injects a leading
  "📥 connected to experience pool" line into every claude -p response.
  We strip leading hook-noise lines after parsing.
- Bad-output rejection — model sometimes ignores the prompt and produces
  full-sentence assistant-style output. `_looks_bad_title()` catches the
  obvious patterns; we leave the existing title alone in that case.

Disabled when:
- `EXP_REFINE_TITLE_SERVER=0` — explicit opt-out.
- `EXP_LLM=mock` — server is in mock mode, no point calling.
- `claude` CLI is not on PATH.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from typing import Any

LOG = logging.getLogger(__name__)


_TITLE_SYSTEM = (
    "你是「会话标题」生成器。给定一段 agent 对话 transcript,输出一行简短标题。\n"
    "\n"
    "格式硬要求(违反就算失败):\n"
    "1. 只输出一行,绝不换行,不分段\n"
    "2. 中文 ≤25 字,英文 ≤8 词\n"
    "3. 用「动词 + 对象」结构(如 `部署 OPF 服务`、`Refactor login flow`)\n"
    "4. 标题语言匹配用户语言\n"
    "5. 不要任何 markdown/引号/句末标点/emoji\n"
    "6. 不要任何对话性、解释性、第一人称、提问语句\n"
    "7. 全是闲聊就输出:(闲聊)\n"
    "\n"
    "✅ 正确示范:\n"
    "  部署 OPF 服务到独立 GPU 机器\n"
    "  修复 push 慢的瓶颈\n"
    "  Refactor login flow\n"
    "\n"
    "❌ 错误示范(绝对不允许):\n"
    "  Waiting for your approval to write…\n"
    "  我需要澄清一下\n"
    "  I'll extract this trajectory\n"
    "  The transcript is truncated\n"
    "  <transcript>\n"
    "  📥 connected to experience pool\n"
    "\n"
    "**只输出标题那一行,前后无任何额外文字。**"
)


_BAD_TITLE_PREFIXES = (
    "<", "[", "(", "the ", "i ", "i'", "it ", "it'", "let ", "let's", "we ", "we'",
    "looking", "let me", "sure", "okay", "ok,", "hi,", "hi!", "hello",
    "yes,", "no,", "sorry", "waiting", "based on", "from the",
    "我需要", "我看到", "我注意", "我会", "这段", "这个", "这是", "这条",
    "看起来", "根据", "请告诉", "请提供", "你好",
)
_BAD_TITLE_SUBSTRINGS = (
    "approval", "permission", "transcript", "got cut off", "truncated",
    "incomplete", "could you clarify", "what would you", "what can i",
    "请确认", "需要确认", "需要权限", "请提供更多",
)


def _looks_bad_title(label: str) -> bool:
    if not label:
        return True
    if label == "(闲聊)":
        return False
    low = label.lower().strip()
    if low.startswith(_BAD_TITLE_PREFIXES):
        return True
    if any(s in low for s in _BAD_TITLE_SUBSTRINGS):
        return True
    if label.endswith(("…", "...")):
        return True
    return False


def _pack_transcript(trajectory: list[dict[str, Any]], max_chars: int = 6000) -> str:
    """Compact `[用户]/[助手]/[工具结果]` digest for the title prompt."""
    out: list[str] = []
    used = 0
    for t in trajectory:
        role = t.get("role", "")
        content = (t.get("content") or "").strip()
        tcs = t.get("tool_calls") or []
        if not content and not tcs:
            continue
        if role == "user":
            line = f"[用户] {content[:600]}"
        elif role == "assistant":
            if tcs:
                names = ", ".join(str(tc.get("name", "?")) for tc in tcs)
                line = f"[助手→工具] {names}"
            else:
                line = f"[助手] {content[:600]}"
        elif role == "tool":
            preview = content[:120].replace("\n", " ")
            line = f"[工具结果] {preview}{'…' if len(content) > 120 else ''}"
        else:
            continue
        if used + len(line) > max_chars:
            out.append("...(truncated)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _normalize_label(raw: str) -> str:
    # Strip leading hook-injected lines (the global SessionStart hook
    # prepends a "📥 connected to experience pool …" line to every
    # claude -p response). Take the first remaining non-empty line as
    # the candidate label.
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
    label = label.lstrip("-•*0123456789. ").strip()
    label = label.strip('"\'`「」『』').strip()
    if label.endswith(("。", ".", "!", "?", "！", "？", ":", "：")):
        label = label[:-1]
    label = " ".join(label.split())
    if len(label) > 60:
        label = label[:59] + "…"
    return label


def _enabled() -> bool:
    """Title refine has its own LLM pathway (shells out to `claude -p`),
    independent of the main `EXP_LLM` switch — `EXP_LLM=mock` only
    disables the in-process llm.py callsites (extractor / sanitizer
    judge), not this subprocess pathway. Operators who genuinely want
    the title refiner off should set `EXP_REFINE_TITLE_SERVER=0`."""
    if os.environ.get("EXP_REFINE_TITLE_SERVER", "1") not in {"1", "true", "yes"}:
        return False
    return shutil.which("claude") is not None


def _llm_summarize(trajectory: list[dict[str, Any]], timeout: int = 45) -> str | None:
    """Shell out to `claude -p`. Returns a usable label or None."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None
    transcript = _pack_transcript(trajectory)
    if not transcript.strip():
        return None
    model = os.environ.get("EXP_TITLE_MODEL", "claude-haiku-4-5-20251001")
    # Subprocess env isolation — without these the spawned claude's
    # SessionEnd hook would call `exp push-latest`, which would trigger
    # another title refine, which would spawn another claude, … infinite
    # recursion (the recursion bug that caused 363 spurious uploads).
    env = dict(os.environ)
    env["EXP_AUTO_UPLOAD"] = "0"
    env["EXP_REFINE_TITLES"] = "0"
    env["EXP_REFINE_TITLE_SERVER"] = "0"
    env["EXP_TITLE_DISABLE"] = "1"
    try:
        proc = subprocess.run(
            [
                claude_bin, "-p", "--output-format", "json",
                "--model", model,
                "--append-system-prompt", _TITLE_SYSTEM,
                # don't write a session file — otherwise daemon-tick
                # picks it up as a new "session" later
                "--no-session-persistence",
                "--disable-slash-commands",
            ],
            input=f"<transcript>\n{transcript}\n</transcript>",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            cwd="/tmp",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        LOG.info("title refine claude -p failed: %s", str(exc)[:120])
        return None
    if proc.returncode != 0:
        return None
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if envelope.get("is_error"):
        return None
    raw = (envelope.get("result") or "").strip()
    if not raw:
        return None
    label = _normalize_label(raw)
    if not label or label.lower() in ("(no task)", "(none)"):
        return None
    if _looks_bad_title(label):
        LOG.info("title refine rejected as bad: %r", label[:80])
        return None
    return label


def refine_title_for_experience(db_path: str, experience_id: str) -> dict[str, Any]:
    """Read the just-stored trajectory, call the LLM, update intent_text.

    Returns a small status dict for logging. Never raises — safe to call
    from a fire-and-forget BackgroundTask.
    """
    if not _enabled():
        return {"skipped": True, "reason": "disabled or claude CLI missing"}

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT trajectory_path, intent_text "
            "FROM experiences WHERE experience_id = ?",
            (experience_id,),
        ).fetchone()
        if not row:
            return {"skipped": True, "reason": "experience not found"}
        traj_path = row["trajectory_path"]
        if not traj_path or not os.path.exists(traj_path):
            return {"skipped": True, "reason": "no trajectory file"}

        with open(traj_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        traj = data["trajectory"] if isinstance(data, dict) else data
        if not traj:
            return {"skipped": True, "reason": "empty trajectory"}

        new_label = _llm_summarize(traj)
        if not new_label:
            return {"skipped": True, "reason": "llm returned no usable label"}

        with conn:
            conn.execute(
                "UPDATE experiences SET intent_text = ? WHERE experience_id = ?",
                (new_label, experience_id),
            )
        return {
            "ok": True,
            "experience_id": experience_id,
            "old_intent": (row["intent_text"] or "")[:60],
            "new_intent": new_label,
        }
    except Exception as exc:
        LOG.warning("title refine failed for %s: %s", experience_id, str(exc)[:200])
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def refine_titles_batch(db_path: str, *, owner: str | None = None,
                        limit: int = 100) -> dict[str, Any]:
    """One-shot pass over already-uploaded experiences. Used by an admin
    endpoint or a one-off CLI to backfill titles on rows that were
    pushed before this feature existed."""
    if not _enabled():
        return {"skipped": True, "reason": "disabled or claude CLI missing"}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if owner:
        cur = conn.execute(
            """SELECT e.experience_id FROM experiences e
               JOIN agents a ON e.agent_id = a.agent_id
               WHERE a.owner = ? AND e.revoked = 0
               ORDER BY e.created_at DESC LIMIT ?""",
            (owner, limit),
        )
    else:
        cur = conn.execute(
            "SELECT experience_id FROM experiences WHERE revoked = 0 "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    eids = [r["experience_id"] for r in cur.fetchall()]
    conn.close()

    refined = 0
    skipped = 0
    failed = 0
    for eid in eids:
        res = refine_title_for_experience(db_path, eid)
        if res.get("ok"):
            refined += 1
        elif res.get("skipped"):
            skipped += 1
        else:
            failed += 1
    return {
        "total": len(eids),
        "refined": refined,
        "skipped": skipped,
        "failed": failed,
    }

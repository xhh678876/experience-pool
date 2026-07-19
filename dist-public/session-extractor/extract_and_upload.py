#!/usr/bin/env python3
"""experience-pool session backfill — standalone extractor + uploader.

Style follows claude_sft_delivery: single self-contained Python,
zero non-stdlib deps for the basic flow. Drops local Claude Code /
Codex session JSONLs into your PRIVATE experience-pool repo via
HMAC-signed HTTP POST. ACL is hard-coded to `private` — uploads done
by this tool are NEVER visible to anyone but the owner.

Usage:
    EXP_AGENT_NAME='user-xxx' \\
    EXP_AGENT_SECRET='<hex>' \\
    EXP_BASE_URL='<portal /me 给的 vscode notebook proxy URL>' \\
    python3 extract_and_upload.py [options]

Options:
    --sources <list>    comma-separated; default auto-detect
                        (claude-code, codex, hermes, openclaw)
    --limit N           cap total uploads across all sources
    --since <iso>       only sessions modified after this ISO date
    --dry-run           list what would be uploaded, don't post
    --verbose, -v       per-session detail

Exit codes:
    0  success (some sessions may have been duplicates)
    1  no creds / no API
    2  partial — some uploads failed
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Always private. The /me page advertises this guarantee — never
# publicize what this script uploads.
HARDCODED_ACL = "private"

CLAUDE_DIR = Path.home() / ".claude" / "projects"
CODEX_DIR  = Path.home() / ".codex" / "sessions"
HERMES_DIR = Path.home() / ".hermes"
OPENCLAW_DIR = Path.home() / ".openclaw"
CODEX_TOOL_OUTPUT_CHARS = int(os.environ.get("EXP_CODEX_TOOL_OUTPUT_CHARS", "12000"))
CODEX_TASK_CHARS = int(os.environ.get("EXP_CODEX_TASK_CHARS", "1200000"))


# ---------- adapters: one per agent runtime ---------------------------

def _detect_sources(user_supplied: list[str] | None) -> list[str]:
    if user_supplied:
        return user_supplied
    detected: list[str] = []
    if CLAUDE_DIR.is_dir(): detected.append("claude-code")
    if CODEX_DIR.is_dir():  detected.append("codex")
    if HERMES_DIR.is_dir() and (HERMES_DIR / "sessions").is_dir(): detected.append("hermes")
    if OPENCLAW_DIR.is_dir() and (OPENCLAW_DIR / "sessions").is_dir(): detected.append("openclaw")
    return detected


def _list_sessions(source: str) -> list[Path]:
    """Return paths of session files for this source, newest first."""
    if source == "claude-code":
        if not CLAUDE_DIR.is_dir(): return []
        files = list(CLAUDE_DIR.glob("*/*.jsonl"))
    elif source == "codex":
        if not CODEX_DIR.is_dir(): return []
        files = []
        for ext in ("*.json", "*.jsonl"):
            files.extend(CODEX_DIR.rglob(ext))
    elif source in ("hermes", "openclaw"):
        root = HERMES_DIR if source == "hermes" else OPENCLAW_DIR
        sess = root / "sessions"
        files = list(sess.rglob("*.json")) if sess.is_dir() else []
    else:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _split_anthropic_blocks(role: str, content: Any) -> list[dict[str, Any]]:
    """Split an Anthropic-style content list into ONE turn per block.
    Each block (text / thinking / tool_use / tool_result / image) becomes
    its own {role, content} entry — no truncation, no encrypted opaques.
    """
    if isinstance(content, str):
        c = content.strip()
        return [{"role": role, "content": content}] if c else []
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt == "text":
            t = block.get("text") or ""
            if t.strip():
                out.append({"role": role, "content": t})
        elif bt == "thinking":
            # keep thinking text, drop opaque base64 `signature`
            t = block.get("thinking") or ""
            if t.strip():
                out.append({"role": role, "content": f"💭 思考\n\n{t}"})
        elif bt == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input")
            try:
                inp_str = json.dumps(inp, ensure_ascii=False, indent=2)
            except Exception:
                inp_str = str(inp)
            tool_id = block.get("id", "")
            id_suffix = f"  (id={tool_id[:12]})" if tool_id else ""
            out.append({
                "role": role,  # assistant
                "content": f"🔧 调用工具: {name}{id_suffix}\n\n```json\n{inp_str}\n```",
            })
        elif bt == "tool_result":
            tr_content = block.get("content")
            if isinstance(tr_content, list):
                inner = []
                for c in tr_content:
                    if isinstance(c, dict):
                        if c.get("type") == "text":
                            inner.append(c.get("text", ""))
                        elif c.get("type") == "image":
                            inner.append("[image]")
                tr_text = "\n".join(inner)
            elif isinstance(tr_content, str):
                tr_text = tr_content
            else:
                tr_text = json.dumps(tr_content, ensure_ascii=False)
            tool_id = block.get("tool_use_id", "")
            id_suffix = f"  (id={tool_id[:12]})" if tool_id else ""
            is_error = block.get("is_error")
            err_marker = " ❌" if is_error else ""
            out.append({
                "role": "tool",
                "content": f"📤 工具返回{err_marker}{id_suffix}\n\n{tr_text}",
            })
        elif bt == "image":
            out.append({"role": role, "content": "🖼️ [图片]"})
        elif bt:
            out.append({"role": role, "content": f"[{bt}]"})
    return out


def _iter_text_lines(path: Path) -> Iterable[str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            yield from handle
    except OSError:
        return


def _parse_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a Claude Code session JSONL into a flat trajectory list.

    Each Anthropic content block becomes its OWN turn (so tool_use stays
    visible alongside the assistant text that called it, instead of being
    crammed into a single assistant message). Meta-only lines (last-prompt
    / queue-operation / attachment / file-history-snapshot / permission-
    mode / summary) are dropped.
    """
    out: list[dict[str, Any]] = []
    SKIP_TYPES = {
        "last-prompt", "queue-operation", "attachment",
        "file-history-snapshot", "permission-mode", "summary",
    }
    for line in _iter_text_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") in SKIP_TYPES:
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else d
        role = msg.get("role") or d.get("type") or ""
        if role not in ("user", "assistant", "system", "tool"):
            continue
        raw_content = msg.get("content", d.get("content", ""))
        out.extend(_split_anthropic_blocks(role, raw_content))
    return out


def _compact_claude_segment(
    trajectory: list[dict[str, Any]], max_chars: int
) -> list[dict[str, Any]]:
    total = sum(len(str(turn.get("content") or "")) for turn in trajectory)
    if max_chars <= 0 or total <= max_chars:
        return trajectory
    for role, limit in (
        ("tool", max(2_000, min(48_000, max_chars // 8))),
        ("assistant", max(4_000, min(64_000, max_chars // 6))),
    ):
        for turn in trajectory:
            if total <= max_chars:
                break
            if turn.get("role") != role:
                continue
            before = len(str(turn.get("content") or ""))
            turn["content"] = _clip_codex_text(turn.get("content") or "", limit)
            total -= max(0, before - len(str(turn["content"])))
    return trajectory


def _split_claude_trajectory(
    trajectory: list[dict[str, Any]],
    *,
    max_turns: int,
    max_chars: int,
) -> list[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    if not trajectory:
        return []
    total_chars = sum(len(str(turn.get("content") or "")) for turn in trajectory)
    if len(trajectory) <= max_turns and total_chars <= max_chars:
        return [("", trajectory, {})]

    segments: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    active: list[dict[str, Any]] = []
    active_chars = 0
    active_start = 0

    def finish(turn_end: int) -> None:
        nonlocal active, active_chars, active_start
        if not active:
            return
        segment_id = f"seg-{len(segments) + 1:04d}"
        compacted = _compact_claude_segment(active, max_chars)
        segments.append(
            (
                segment_id,
                compacted,
                {
                    "segment_id": segment_id,
                    "source_turn_start": active_start,
                    "source_turn_end": turn_end,
                    "task_status": "complete",
                },
            )
        )
        active = []
        active_chars = 0
        active_start = turn_end + 1

    for turn_index, turn in enumerate(trajectory):
        content = str(turn.get("content") or "")
        starts_new_task = (
            turn.get("role") == "user" and _is_meaningful_task_text(content)
        )
        if active and starts_new_task and (
            len(active) >= max_turns or active_chars >= max_chars
        ):
            finish(turn_index - 1)
            active_start = turn_index
        active.append(turn)
        active_chars += len(content)
        has_summary = bool(_TASK_SUMMARY_RE.search(content))
        hard_limit = len(active) >= max_turns * 2 or active_chars >= max_chars * 2
        if has_summary or hard_limit:
            finish(turn_index)
    finish(len(trajectory) - 1)
    return segments


def _clip_codex_text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if limit <= 0 or len(text) <= limit:
        return text
    marker = f"\n\n[... truncated {len(text) - limit} chars from runtime output ...]\n\n"
    room = max(200, limit - len(marker))
    head = max(120, int(room * 0.7))
    return text[:head] + marker + text[-(room - head):]


def _codex_response_turns(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ptype = payload.get("type")
    if ptype == "message":
        role = {"developer": "system", "tool": "tool"}.get(payload.get("role"), payload.get("role"))
        if role not in ("user", "assistant", "system", "tool"):
            return []
        content = payload.get("content", "")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in ("input_text", "output_text", "text"):
                    parts.append(item.get("text") or "")
                elif item.get("type") in ("input_image", "image"):
                    parts.append("[image]")
        text = "\n".join(part for part in parts if part.strip())
        return [{"role": role, "content": _clip_codex_text(text, 48000)}] if text.strip() else []

    if ptype == "reasoning":
        parts: list[str] = []
        for item in payload.get("summary") or []:
            value = item.get("text") if isinstance(item, dict) else item
            if isinstance(value, str) and value.strip():
                parts.append(value)
        inline = payload.get("content")
        if isinstance(inline, str) and inline.strip():
            parts.append(inline)
        if parts:
            return [{"role": "assistant", "content": "💭 思考\n\n" + _clip_codex_text("\n\n".join(parts), 16000)}]
        return []

    if ptype in {"function_call", "custom_tool_call"}:
        raw_input = payload.get("arguments") if ptype == "function_call" else payload.get("input")
        try:
            parsed_input = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
        except (json.JSONDecodeError, TypeError):
            parsed_input = raw_input
        return [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": payload.get("call_id", ""),
                "name": payload.get("name", "tool"),
                "input": parsed_input,
                "kind": ptype,
            }],
        }]

    if ptype in {"function_call_output", "custom_tool_call_output"}:
        output = payload.get("output", "")
        display = output
        if isinstance(output, str):
            try:
                parsed = json.loads(output)
                if isinstance(parsed, dict):
                    display = parsed.get("output", parsed.get("content", parsed))
            except json.JSONDecodeError:
                pass
        return [{
            "role": "tool",
            "content": _clip_codex_text(display, CODEX_TOOL_OUTPUT_CHARS),
            "tool_result_for": payload.get("call_id", ""),
        }]
    return []


def _compact_codex_trajectory(trajectory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(len(str(turn.get("content") or "")) for turn in trajectory)
    if total <= CODEX_TASK_CHARS:
        return trajectory
    for role, limit in (("tool", 1800), ("assistant", 5000)):
        for turn in trajectory:
            if total <= CODEX_TASK_CHARS:
                break
            if turn.get("role") != role:
                continue
            before = len(str(turn.get("content") or ""))
            clipped = _clip_codex_text(turn.get("content") or "", limit)
            turn["content"] = clipped
            total -= max(0, before - len(clipped))
    return trajectory


def _iter_codex_tasks(path: Path, *, include_incomplete: bool = False) -> Iterable[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    """Yield task-sized Codex trajectories without loading the rollout file."""
    active: dict[str, Any] | None = None
    fallback: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {"model": "unknown", "cwd": "", "agent_version": ""}
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for raw in handle:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get("payload") or {}
            payload = payload if isinstance(payload, dict) else {}
            ptype = payload.get("type")
            if record.get("type") == "session_meta":
                metadata["cwd"] = payload.get("cwd") or metadata["cwd"]
                metadata["agent_version"] = payload.get("cli_version") or metadata["agent_version"]
                metadata["model"] = payload.get("model") or metadata["model"]
                continue
            if record.get("type") == "event_msg" and ptype == "task_started":
                if active and include_incomplete and active["trajectory"]:
                    yield active["turn_id"], _compact_codex_trajectory(active["trajectory"]), {**metadata, "task_status": "superseded"}
                active = {
                    "turn_id": str(payload.get("turn_id") or "task"),
                    "trajectory": [],
                }
                continue
            if record.get("type") == "turn_context":
                metadata["cwd"] = payload.get("cwd") or metadata["cwd"]
                metadata["model"] = payload.get("model") or metadata["model"]
                continue
            if record.get("type") == "response_item":
                turns = _codex_response_turns(payload)
                if active is not None:
                    active["trajectory"].extend(turns)
                else:
                    fallback.extend(turns)
                continue
            if record.get("type") == "event_msg" and ptype in {"task_complete", "turn_aborted"} and active:
                status = "complete" if ptype == "task_complete" else "aborted"
                if active["trajectory"]:
                    yield active["turn_id"], _compact_codex_trajectory(active["trajectory"]), {**metadata, "task_status": status}
                active = None
    if active and include_incomplete and active["trajectory"]:
        yield active["turn_id"], _compact_codex_trajectory(active["trajectory"]), {**metadata, "task_status": "open"}
    elif fallback:
        yield path.stem, _compact_codex_trajectory(fallback), {**metadata, "task_status": "legacy"}


def _parse_codex_json(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _, trajectory, _ in _iter_codex_tasks(path, include_incomplete=True):
        out.extend(trajectory)
    return out


def _build_trajectory(source: str, path: Path) -> list[dict[str, Any]]:
    if source == "claude-code":
        return _parse_jsonl(path)
    if source == "codex":
        # Codex rollouts are .jsonl with nested {type, payload} entries —
        # always go through the codex-aware parser regardless of suffix.
        return _parse_codex_json(path)
    return _parse_jsonl(path)


# ---------- card derivation -------------------------------------------

_TASK_WRAPPER_PREFIXES = (
    "# agents.md instructions",
    "<environment_context>",
    "<permissions instructions>",
    "<collaboration_mode>",
    "<subagent_notification>",
    "<local-command-caveat>",
    "<command-message>",
    "<command-name>",
    "<system-reminder>",
    "<transcript>",
)
_TRIVIAL_TASK_MESSAGES = {
    "", "1", "ok", "okay", "yes", "no", "hi", "hello", "hey",
    "thanks", "thank you", "continue", "go on", "done",
    "你好", "您好", "在吗", "在不在", "继续", "继续做", "继续吧", "好的",
    "好", "可以", "行", "收到", "谢谢", "完成", "搞定", "快点", "开始吧",
}
_GOAL_OBJECTIVE_RE = re.compile(
    r"<objective>\s*(.*?)\s*</objective>", re.IGNORECASE | re.DOTALL,
)
_TASK_SUMMARY_RE = re.compile(r"(?im)^\s*\[task-summary\]\s*[:：]\s*(.+?)\s*$")


def _clean_task_user_text(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    objective = _GOAL_OBJECTIVE_RE.search(text)
    if objective:
        text = objective.group(1).strip()
    elif text.lower().startswith(_TASK_WRAPPER_PREFIXES):
        return ""
    if re.fullmatch(r"(?:<image[^>]*>\s*)+", text, re.IGNORECASE):
        return ""
    return text


def _is_meaningful_task_text(content: str) -> bool:
    text = _clean_task_user_text(content)
    if not text:
        return False
    normalized = re.sub(r"[\s\W_]+", " ", text, flags=re.UNICODE).strip().lower()
    if normalized in _TRIVIAL_TASK_MESSAGES:
        return False
    compact = re.sub(r"\s+", "", text)
    return len(compact) >= 3 or bool(re.search(r"[A-Za-z][0-9]|[0-9][A-Za-z]", compact))


def _task_summary_from_traj(traj: list[dict[str, Any]]) -> str:
    for turn in reversed(traj):
        for label in reversed(_TASK_SUMMARY_RE.findall(str(turn.get("content") or ""))):
            label = " ".join(label.strip().split()).strip('"\'`「」『』')
            if label:
                return label[:120]
    return ""


def _trajectory_has_retrievable_task(traj: list[dict[str, Any]]) -> bool:
    return any(
        turn.get("role") == "user" and _is_meaningful_task_text(turn.get("content") or "")
        for turn in traj
    ) or bool(_task_summary_from_traj(traj))

def _derive_title(first_user: str, traj: list[dict[str, Any]], source: str) -> str:
    """Build a one-line title summarising the session.

    Strategy: take the first real user message, drop quoted blocks /
    URLs / code fences, take the first sentence (bounded by .。!?！？\\n),
    and trim to ~70 chars. Falls back to "<source> session" if no usable
    text is found. Result is what the UI renders as the card title.
    """
    text = (first_user or "").strip()
    # strip leading code fence / blockquote markers
    while text.startswith(("```", ">", "<")):
        nl = text.find("\n")
        if nl < 0:
            break
        text = text[nl + 1 :].strip()
    # drop blank lines, take first non-empty
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return f"{source} session"
    # cut at sentence boundary if there is one within first ~120 chars
    head = line[:120]
    cut_at = -1
    for sep in ("。", "！", "？", ". ", "! ", "? ", "\n"):
        idx = head.find(sep)
        if idx > 0 and (cut_at < 0 or idx < cut_at):
            cut_at = idx + len(sep)
    title = head[:cut_at].strip() if cut_at > 0 else head.strip()
    # tighten whitespace
    title = " ".join(title.split())
    if len(title) > 70:
        title = title[:69].rstrip() + "…"
    return title or f"{source} session"


def _card_from_trajectory(
    traj: list[dict[str, Any]],
    source: str,
    path: Path,
    *,
    session_id: str | None = None,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate the LiteCard fields from the trajectory.

    Heuristic: first user message → query, last assistant message → outcome,
    intent guessed from query. Server-side annotator can re-derive these
    later if you want better quality.
    """
    def _is_real_user(t: dict[str, Any]) -> bool:
        return t["role"] == "user" and _is_meaningful_task_text(t.get("content") or "")

    def _is_real_assistant(t: dict[str, Any]) -> bool:
        if t["role"] != "assistant":
            return False
        c = (t.get("content") or "").lstrip()
        if c.startswith(("🔧", "💭")):
            return False
        return bool(c)

    first_user = next(
        (_clean_task_user_text(t["content"]) for t in traj if _is_real_user(t)),
        "",
    )
    last_assistant = next(
        (t["content"] for t in reversed(traj) if _is_real_assistant(t)),
        "",
    )
    task_summary = _task_summary_from_traj(traj)
    title = task_summary or _derive_title(first_user, traj, source)
    return {
        "query": (first_user or task_summary or "(no user message)")[:512],
        "intent": title,
        "steps": [f"replay session {path.name} ({len(traj)} turns)"],
        "outcome": (last_assistant or "(no assistant reply)")[:512],
        "task_type": f"{source}-backfill",
        "source_model": str((source_meta or {}).get("model") or "unknown"),
        "sensitivity": "medium",
        "acl": HARDCODED_ACL,            # ← never public
        "tags": [f"backfill", f"src:{source}"],
        "trajectory": traj,
        "meta": {
            "agent_type": source,
            "session_id": session_id or path.stem,
            "source_path": str(path),
            "uploaded_via": "session-extractor",
            "extra": {
                "parent_session_id": path.stem,
                **(source_meta or {}),
            },
        },
    }


# ---------- HMAC + HTTP -----------------------------------------------

def _hmac_post(base_url: str, name: str, secret: str, path: str, body: dict) -> dict[str, Any]:
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    canonical = b"\n".join([b"POST", path.encode(), body_bytes])
    sig = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body_bytes,
        headers={
            "content-type": "application/json",
            "x-agent-name": name,
            "x-signature": sig,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


# ---------- main loop -------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--sources", default="",
                   help="comma-separated; default = auto-detect")
    p.add_argument("--limit", type=int, default=0,
                   help="cap total uploads (0 = unlimited)")
    p.add_argument("--since", default="",
                   help="only sessions modified after this ISO date")
    p.add_argument("--max-mb", type=float, default=0.0,
                   help="optional hard file-size skip cap in MB; default 0 "
                        "(large Claude sessions are segmented instead)")
    p.add_argument("--segment-mb", type=float, default=4.0,
                   help="maximum compacted Claude segment size in MB; default 4")
    p.add_argument("--segment-turns", type=int, default=240,
                   help="target turns per long Claude segment; default 240")
    p.add_argument("--sleep", type=float, default=0.5,
                   help="sleep between pushes (seconds), to give server "
                        "breathing room. Default 0.5")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    name = os.environ.get("EXP_AGENT_NAME", "").strip()
    secret = os.environ.get("EXP_AGENT_SECRET", "").strip()
    base = os.environ.get("EXP_BASE_URL", "").strip().rstrip("/")
    if not name or not secret or not base:
        print("ERROR: EXP_AGENT_NAME, EXP_AGENT_SECRET, EXP_BASE_URL all required.",
              file=sys.stderr)
        print("       Get them from your portal /me page (the bind script).",
              file=sys.stderr)
        return 1

    sources = _detect_sources(
        [s.strip() for s in args.sources.split(",") if s.strip()] or None
    )
    if not sources:
        print("no agent runtimes detected on this host.", file=sys.stderr)
        return 1
    print(f"[extractor] sources: {', '.join(sources)}")
    print(f"[extractor] target:  {base}  agent={name}")
    print(f"[extractor] acl:     {HARDCODED_ACL} (never public — by design)")

    since_ts: float = 0.0
    if args.since:
        try:
            since_ts = datetime.fromisoformat(args.since.replace("Z", "+00:00")).timestamp()
        except ValueError:
            print(f"--since not a valid ISO date: {args.since}", file=sys.stderr)
            return 1

    counts = {"uploaded": 0, "duplicate": 0, "skipped": 0, "failed": 0}
    total = 0

    for src in sources:
        sessions = _list_sessions(src)
        print(f"\n[{src}] found {len(sessions)} session file(s)")
        for path in sessions:
            if args.limit and total >= args.limit:
                print(f"[extractor] hit --limit={args.limit}, stopping.")
                return _summary(counts)
            if since_ts and path.stat().st_mtime < since_ts:
                counts["skipped"] += 1
                continue
            # --max-mb is now an explicit hard stop only. By default long
            # Claude sessions are split below instead of silently discarded.
            if src != "codex" and args.max_mb > 0:
                size_mb = path.stat().st_size / (1024 * 1024)
                if size_mb > args.max_mb:
                    counts["skipped"] += 1
                    if args.verbose:
                        print(f"  ⊘ {src}/{path.stem[:8]} too big "
                              f"({size_mb:.1f}MB > {args.max_mb}MB); skip "
                              f"(use --max-mb 0 to push anyway)")
                    continue
            if src == "codex":
                task_iter = (
                    (f"{path.stem}:{turn_id}", trajectory, meta)
                    for turn_id, trajectory, meta in _iter_codex_tasks(path)
                )
            elif src == "claude-code":
                trajectory = _build_trajectory(src, path)
                max_chars = max(64 * 1024, int(args.segment_mb * 1024 * 1024))
                split = _split_claude_trajectory(
                    trajectory,
                    max_turns=max(40, args.segment_turns),
                    max_chars=max_chars,
                )
                task_iter = (
                    (
                        path.stem if not segment_id else f"{path.stem}:{segment_id}",
                        segment,
                        meta,
                    )
                    for segment_id, segment, meta in split
                )
            else:
                trajectory = _build_trajectory(src, path)
                task_iter = iter([(path.stem, trajectory, {})])

            found_task = False
            for session_id, traj, source_meta in task_iter:
                if args.limit and total >= args.limit:
                    print(f"[extractor] hit --limit={args.limit}, stopping.")
                    return _summary(counts)
                if not traj:
                    continue
                found_task = True
                if not _trajectory_has_retrievable_task(traj):
                    counts["skipped"] += 1
                    if args.verbose:
                        print(f"  ⊘ {src}/{session_id[-8:]} runtime wrapper or trivial task; skip")
                    continue
                card = _card_from_trajectory(
                    traj,
                    src,
                    path,
                    session_id=session_id,
                    source_meta=source_meta,
                )
                total += 1
                short = (
                    session_id.rsplit(":", 1)[-1][:8]
                    if ":" in session_id
                    else path.stem[:8]
                )
                if args.dry_run:
                    print(f"  [{total}] would upload {src}/{short}  turns={len(traj)}")
                    counts["uploaded"] += 1
                    continue
                try:
                    resp = _hmac_post(base, name, secret, "/v1/lite/push", card)
                except urllib.error.HTTPError as e:
                    msg = e.read().decode("utf-8", errors="replace")[:200]
                    print(f"  ✗ [{total}] {src}/{short}  HTTP {e.code}  {msg}")
                    counts["failed"] += 1
                    continue
                except Exception as e:
                    print(f"  ✗ [{total}] {src}/{short}  {type(e).__name__}: {e}")
                    counts["failed"] += 1
                    continue
                eid = (resp.get("experience_id") or "?")[:8]
                if resp.get("ingest_path") == "lite-dup":
                    counts["duplicate"] += 1
                    if args.verbose:
                        print(f"  ⏎ [{total}] {src}/{short} → {eid} (already in pool)")
                else:
                    counts["uploaded"] += 1
                    print(f"  ✓ [{total}] {src}/{short} → {eid}  (acl=private)")
                if args.sleep > 0:
                    time.sleep(args.sleep)
            if not found_task:
                counts["skipped"] += 1
                if args.verbose:
                    print(f"  ⊘ {path.name} empty or incomplete trajectory; skip")

    return _summary(counts)


def _summary(counts: dict[str, int]) -> int:
    print()
    print(f"[extractor] DONE — uploaded={counts['uploaded']}  "
          f"duplicate={counts['duplicate']}  "
          f"skipped={counts['skipped']}  "
          f"failed={counts['failed']}")
    print(f"[extractor] visit your portal /me to review or revoke.")
    return 0 if counts["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

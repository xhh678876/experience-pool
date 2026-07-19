#!/usr/bin/env python3
"""
exp_uploader — universal Experience Pool uploader.

Single file, stdlib only. Detects the agent's session storage on the local
machine, normalizes to a canonical {trajectory, model, ...} shape, runs a
high-confidence client-side sanitizer, and HMAC-signs an upload to
`/v1/lite/push`. The full normalized trajectory plus optional raw bytes
travel together so the server can reconstruct the complete session.

Supported sources:
    claude-code     ~/.claude/projects/**/*.jsonl  (also openclaw-sjtu)
    hermes          ~/.hermes/sessions/*.json[l]   (skips request_dump_*)
    agents-chat     ~/agents-chat/messages.db      (groups by thread_id)
    cursor          ~/Library/Application Support/Cursor/User/**/state.vscdb
    aider           <cwd>/.aider.chat.history.md
    codex           ~/.codex/sessions/**.json[l]
    generic         any JSON containing {"messages":[...]} or {"trajectory":[...]}

Usage:
    exp_uploader register --name <agent> --team <team>
    exp_uploader list-sessions [--source auto|claude-code|hermes|agents-chat|...]
    exp_uploader push --session <id-or-path> [--source X] [--task ...] [--acl ...]
    exp_uploader push-latest [--source auto] [--acl private]
    exp_uploader push-all --source X [--since 2026-04-01] [--limit 50]
    exp_uploader push-file --file traj.json [--acl public]
    exp_uploader whoami

Env:
    EXP_BASE_URL       gateway URL (default from EXP_BIND_BASE_URL or https://expool.clawsii.com)
    EXP_API_KEY        Bearer API key minted from the portal
    EXP_AGENT_NAME     agent identifier (override register)
    EXP_AGENT_SECRET   HMAC secret (override register)
    EXP_CRED_DIR       credential storage dir (default ~/.experience-pool/credentials)

Credentials are stored at $EXP_CRED_DIR/<agent>.json or api-key.json (mode 0600).
"""
from __future__ import annotations

__version__ = "0.3.3"

import argparse
import base64
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable

def _gateway_from_ui_public_url() -> str | None:
    ui = (os.environ.get("EXP_UI_PUBLIC_URL") or "").rstrip("/")
    marker = "/proxy/"
    if marker not in ui:
        return None
    prefix = ui.rsplit(marker, 1)[0]
    return f"{prefix}/proxy/3080"


DEFAULT_BASE_URL = (
    os.environ.get("EXP_BASE_URL")
    or os.environ.get("EXPOOL_BASE")
    or os.environ.get("EXP_BIND_BASE_URL")
    or os.environ.get("EXP_PUBLIC_BASE_URL")
    or _gateway_from_ui_public_url()
    or "https://expool.clawsii.com"
)
DEFAULT_CRED_DIR = Path(
    os.environ.get("EXP_CRED_DIR", str(Path.home() / ".experience-pool" / "credentials"))
)
USER_AGENT = "exp_uploader/0.2 (python-stdlib)"


# ---------------------------------------------------------------------------
# Sanitizer (client-side; server runs the full Layer1+2+3 set again on top).
#
# This rule set is intentionally a superset-of-secrets: we lean toward over-
# masking on the client because the agent host owns the only copy of raw L1.
# Categories tagged HIGH cause the uploader to emit a warning and (when
# `--strict-redact` is set) abort upload entirely.
# ---------------------------------------------------------------------------

_HOME_RE = re.compile(r"/(?:Users|home)/[^/\s'\"]+/")

_RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    # --- Vendor secret tokens (HIGH severity) ----------------------------
    ("anthropic_key",      re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),                      "<SECRET>",        "high"),
    ("openai_key",         re.compile(r"\bsk-(?!ant-)[A-Za-z0-9]{20,}\b"),                     "<SECRET>",        "high"),
    ("openai_proj_key",    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b"),                     "<SECRET>",        "high"),
    ("xai_key",            re.compile(r"\bxai-[A-Za-z0-9]{40,}\b"),                            "<SECRET>",        "high"),
    ("groq_key",           re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b"),                            "<SECRET>",        "high"),
    ("google_api_key",     re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),                          "<SECRET>",        "high"),
    ("hf_token",           re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),                             "<SECRET>",        "high"),
    ("mimo_token",         re.compile(r"\btp-[A-Za-z0-9]{30,}\b"),                             "<SECRET>",        "high"),
    ("stripe_secret",      re.compile(r"\bsk_(?:live|test)_[0-9a-zA-Z]{16,}\b"),               "<SECRET>",        "high"),
    ("stripe_publishable", re.compile(r"\bpk_(?:live|test)_[0-9a-zA-Z]{16,}\b"),               "<SECRET>",        "high"),
    ("github_token",       re.compile(r"\bgh[pousr]_[0-9a-zA-Z]{20,}\b"),                      "<SECRET>",        "high"),
    ("gitlab_token",       re.compile(r"\bglpat-[0-9a-zA-Z_\-]{20,}\b"),                       "<SECRET>",        "high"),
    ("npm_token",          re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"),                            "<SECRET>",        "high"),
    ("vercel_token",       re.compile(r"\b(?:vercel|vc)_[A-Za-z0-9]{20,}\b"),                  "<SECRET>",        "high"),
    ("supabase_token",     re.compile(r"\bsbp_[A-Za-z0-9]{30,}\b"),                            "<SECRET>",        "high"),
    ("cloudflare_token",   re.compile(r"\bcfk_[A-Za-z0-9_\-]{30,}\b"),                         "<SECRET>",        "high"),
    ("sentry_dsn",         re.compile(r"\bsntrys_[A-Za-z0-9_\-]{30,}\b"),                      "<SECRET>",        "high"),
    ("slack_token",        re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),                   "<SECRET>",        "high"),
    ("aws_access_key",     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                       "<KEY>",           "high"),
    ("gcp_sa_key",         re.compile(r"\"private_key\"\s*:\s*\"-----BEGIN[^\"]+\""),          "\"private_key\":\"<PRIVATE_KEY>\"", "high"),
    ("pem_private_key",    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
                                                                                                "<PRIVATE_KEY>",   "high"),
    ("ssh_pubkey_w_email", re.compile(r"ssh-(?:rsa|ed25519|dss)\s+[A-Za-z0-9+/=]{80,}(?:\s+\S+)?"),
                                                                                                "<SSH_KEY>",       "high"),
    ("jwt",                re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
                                                                                                "<JWT>",           "high"),
    ("bearer_authz",       re.compile(r"(?i)Authorization\s*:\s*Bearer\s+[A-Za-z0-9._\-]{16,}"),
                                                                                                "Authorization: Bearer <TOKEN>", "high"),
    ("generic_assignment", re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|client[_-]?secret|password|passwd|pwd)\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{16,})[\"']?"),
                                                                                                r"\1=<SECRET>",    "high"),
    # url_with_credentials must run BEFORE db_uri so the credentialled form
    # produces the more readable "<scheme>://<USER>:<PASS>@host" output.
    ("url_with_credentials", re.compile(r"\b(https?|ftp|ssh|sftp|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://([^\s/:@]+):([^\s/@]+)@"),
                                                                                                r"\1://<USER>:<PASS>@", "high"),
    ("db_uri",             re.compile(r"\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|clickhouse)://[^\s\"'<>]+"),
                                                                                                r"\1://<DB_URI>",  "high"),

    # --- PII (MEDIUM) -----------------------------------------------------
    ("email",              re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "<EMAIL>",         "medium"),
    ("phone_intl",         re.compile(r"(?<![\w@])\+\d{1,3}[\s.\-]?\d{2,4}[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}\b"),
                                                                                                "<PHONE>",         "medium"),
    ("phone_cn",           re.compile(r"(?<![\w@\d])(?:\+?86[\s\-]?)?1[3-9]\d{9}\b"),           "<PHONE>",         "medium"),
    ("idcard_cn",          re.compile(r"(?<![\w\d])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\w\d])"),
                                                                                                "<ID_CARD>",       "high"),

    # --- Network identifiers (LOW) ---------------------------------------
    ("ipv4",               re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"),
                                                                                                "<IP>",            "low"),
    ("ipv6",               re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b"),         "<IP>",            "low"),

    # --- Path → username strip (LOW) -------------------------------------
    # /Users/xiehaohui/foo  →  /Users/<USER>/foo
    ("home_path",          _HOME_RE,                                                            "<HOMEDIR>/",      "low"),
]

# Categories whose hits should bump the upload to high severity.
_HIGH_CATEGORIES = {name for (name, _p, _r, sev) in _RULES if sev == "high"}


def sanitize(text: str) -> tuple[str, dict[str, int]]:
    """Apply every rule in order. Returns (clean, hits_by_category)."""
    counts: dict[str, int] = {}
    out = text
    for name, pat, placeholder, _sev in _RULES:
        new, n = pat.subn(placeholder, out)
        if n:
            counts[name] = counts.get(name, 0) + n
            out = new
    return out, counts


# Keys that are pure identifiers / structure — sanitizing them would corrupt
# routing on the server side (tool_use_id, role, etc.). Skip from recursion.
_SKIP_KEYS = frozenset({
    "id", "type", "role", "tool_use_id", "tool_call_id",
    "name", "subtype", "model", "stop_reason", "stop_sequence",
    "usage", "index", "ts", "tool_result_for",
})


def sanitize_node(node: Any, counts: dict[str, int]) -> Any:
    """Recursively sanitize every string in a dict/list/scalar tree.

    Mutates the `counts` dict in place. Pure structural keys are skipped so
    routing identifiers stay intact."""
    if isinstance(node, str):
        cleaned, c = sanitize(node)
        for k, v in c.items():
            counts[k] = counts.get(k, 0) + v
        return cleaned
    if isinstance(node, list):
        return [sanitize_node(item, counts) for item in node]
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _SKIP_KEYS or not isinstance(v, (str, list, dict)):
                out[k] = v
            else:
                out[k] = sanitize_node(v, counts)
        return out
    return node


def has_high_severity(counts: dict[str, int]) -> bool:
    """True iff any high-severity rule fired."""
    return any(name in _HIGH_CATEGORIES and n > 0 for name, n in counts.items())


# ---------------------------------------------------------------------------
# Canonical session shape produced by every adapter.
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str  # already string-coerced for upload
    ts: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_result_for: str = ""


@dataclass
class Session:
    agent_type: str
    session_id: str
    started_at: str
    ended_at: str
    model: str
    cwd: str
    agent_version: str
    trajectory: list[Turn]
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "model": self.model,
            "cwd": self.cwd,
            "agent_version": self.agent_version,
            "trajectory": [asdict(t) for t in self.trajectory],
            "extra": self.extra,
        }


def _iter_text_lines(path: Path) -> Iterable[str]:
    """Yield a potentially huge JSONL file without retaining its raw text."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        yield from handle


def _clip_runtime_content(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    marker = f"\n\n[... truncated {len(text) - limit} chars of runtime output ...]\n\n"
    room = max(200, limit - len(marker))
    head = max(120, int(room * 0.7))
    return text[:head] + marker + text[-(room - head):]


def _compact_session_segment(turns: list[Turn], max_chars: int) -> list[Turn]:
    """Bound transport noise while keeping the task's beginning and outcome."""
    total = sum(len(turn.content or "") for turn in turns)
    if max_chars <= 0 or total <= max_chars:
        return turns
    per_role = (
        ("tool", max(2_000, min(48_000, max_chars // 8))),
        ("assistant", max(4_000, min(64_000, max_chars // 6))),
    )
    for role, limit in per_role:
        for turn in turns:
            if total <= max_chars:
                break
            if turn.role != role:
                continue
            before = len(turn.content or "")
            turn.content = _clip_runtime_content(turn.content or "", limit)
            total -= max(0, before - len(turn.content))
    if total > max_chars:
        per_turn = max(1_000, max_chars // max(1, len(turns)))
        for turn in turns:
            if total <= max_chars:
                break
            before = len(turn.content or "")
            turn.content = _clip_runtime_content(turn.content or "", per_turn)
            total -= max(0, before - len(turn.content))
    return turns


# ---------------------------------------------------------------------------
# Adapter: Claude Code (~/.claude/projects/<encoded-cwd>/<uuid>.jsonl)
# ---------------------------------------------------------------------------

class ClaudeCodeAdapter:
    """Claude Code + any compatible fork (OpenClaw, hermes-claude bridges)
    that stores transcript JSONL files in <root>/<encoded-cwd>/<uuid>.jsonl.

    Roots are configurable via $EXP_CLAUDE_ROOTS (comma-separated). Defaults
    cover ~/.claude/projects, ~/.openclaw/projects, ~/.openclaw-sjtu/projects.
    """

    name = "claude-code"

    @staticmethod
    def roots() -> list[Path]:
        env = os.environ.get("EXP_CLAUDE_ROOTS")
        if env:
            return [Path(p).expanduser() for p in env.split(",") if p.strip()]
        candidates = [
            Path.home() / ".claude" / "projects",
            Path.home() / ".openclaw" / "projects",
            Path.home() / ".openclaw-sjtu" / "projects",
            Path.home() / "openclaw-sjtu" / "data" / "projects",
        ]
        return [p for p in candidates if p.is_dir()]

    @classmethod
    def available(cls) -> bool:
        return any(r.is_dir() for r in cls.roots())

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        files: list[Path] = []
        for root in cls.roots():
            files.extend(root.glob("*/*.jsonl"))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[dict[str, Any]] = []
        for f in files[:limit]:
            head: dict[str, Any] = {}
            # cwd/version may not appear on the FIRST line; scan up to 5.
            try:
                with f.open(encoding="utf-8") as fp:
                    for i, line in enumerate(fp):
                        if i >= 5:
                            break
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("cwd") and not head.get("cwd"):
                            head["cwd"] = obj["cwd"]
                        if obj.get("version") and not head.get("version"):
                            head["version"] = obj["version"]
                        if head.get("cwd") and head.get("version"):
                            break
            except OSError:
                pass
            out.append({
                "id": f.stem,
                "path": str(f),
                "mtime": _dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                "size_bytes": f.stat().st_size,
                "cwd": head.get("cwd", ""),
                "version": head.get("version", ""),
                "root": str(f.parent.parent),
            })
        return out

    @classmethod
    def resolve_session(cls, ident: str) -> Path:
        p = Path(ident).expanduser()
        if p.is_file():
            return p
        for root in cls.roots():
            for f in root.glob(f"*/{ident}.jsonl"):
                return f
            for f in root.glob("*/*.jsonl"):
                if f.stem.startswith(ident):
                    return f
        raise FileNotFoundError(f"claude-code session not found: {ident}")

    @classmethod
    def latest_session(cls) -> Path:
        files: list[Path] = []
        for root in cls.roots():
            files.extend(root.glob("*/*.jsonl"))
        if not files:
            raise FileNotFoundError(
                "no claude-code sessions; checked: "
                + ", ".join(str(r) for r in cls.roots())
            )
        return max(files, key=lambda p: p.stat().st_mtime)

    @classmethod
    def parse(cls, path: Path) -> Session:
        """Order-based extraction (avoids parentUuid breakage). Mirrors the
        single-turn approach used in claude_sft_delivery/extractor/scanner.py
        and builder.py — emit user/assistant/tool turns in file order.
        """
        latest_version = ""
        latest_model = ""
        latest_cwd = ""
        started_at = ""
        ended_at = ""
        turns: list[Turn] = []

        for raw in _iter_text_lines(path):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue

            v = obj.get("version", "")
            if v:
                latest_version = v
            cwd = obj.get("cwd", "")
            if cwd:
                latest_cwd = cwd
            ts = obj.get("timestamp", "")
            if ts:
                if not started_at:
                    started_at = ts
                ended_at = ts

            t = obj.get("type")
            if t == "assistant":
                msg = obj.get("message", {}) or {}
                m = msg.get("model", "")
                if m and m != "<synthetic>":
                    latest_model = m
                content = msg.get("content", [])
                if isinstance(content, list):
                    text_parts: list[str] = []
                    thinking_parts: list[str] = []
                    tool_calls: list[dict[str, Any]] = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type")
                        if bt == "text":
                            text_parts.append(block.get("text", ""))
                        elif bt == "thinking":
                            # keep the readable text, drop opaque base64 signature
                            tk = block.get("thinking") or ""
                            if tk:
                                thinking_parts.append(tk)
                        elif bt == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": block.get("input", {}),
                            })
                    # Emit thinking blocks as their own assistant turns so the
                    # UI renders them in dedicated thinking-styled bubbles.
                    for tk in thinking_parts:
                        turns.append(Turn(
                            role="assistant",
                            content="💭 思考\n\n" + tk,
                            ts=ts,
                        ))
                    text = "\n".join(p for p in text_parts if p.strip())
                    if text or tool_calls:
                        turns.append(Turn(
                            role="assistant",
                            content=text,
                            ts=ts,
                            tool_calls=tool_calls,
                        ))
            elif t == "user":
                msg = obj.get("message", {}) or {}
                if msg.get("model") == "<synthetic>":
                    continue
                content = msg.get("content", [])
                if isinstance(content, str):
                    if content.strip():
                        turns.append(Turn(role="user", content=content, ts=ts))
                    continue
                if not isinstance(content, list):
                    continue
                tool_results: list[Turn] = []
                text_parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "tool_result":
                        result_text = block.get("content", "")
                        if isinstance(result_text, list):
                            result_text = "\n".join(
                                str(b.get("text", "")) for b in result_text
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        elif not isinstance(result_text, str):
                            result_text = json.dumps(result_text, ensure_ascii=False)
                        tool_results.append(Turn(
                            role="tool",
                            content=result_text,
                            ts=ts,
                            tool_result_for=block.get("tool_use_id", ""),
                        ))
                    elif bt == "text":
                        text_parts.append(block.get("text", ""))
                user_text = "\n".join(p for p in text_parts if p.strip())
                if user_text.strip() and not user_text.startswith("[Request interrupted"):
                    turns.append(Turn(role="user", content=user_text, ts=ts))
                turns.extend(tool_results)

        return Session(
            agent_type=cls.name,
            session_id=path.stem,
            started_at=started_at,
            ended_at=ended_at,
            model=latest_model or "unknown",
            cwd=latest_cwd,
            agent_version=latest_version,
            trajectory=turns,
            extra={"source_path": str(path)},
        )

    @classmethod
    def parse_tasks(
        cls,
        ident: str | Path,
        *,
        max_turns: int = 240,
        max_chars: int = 4 * 1024 * 1024,
    ) -> list[Session]:
        """Split a long Claude transcript into stable, parent-linked tasks."""
        path = (
            Path(ident).expanduser()
            if Path(ident).expanduser().is_file()
            else cls.resolve_session(str(ident))
        )
        parent = cls.parse(path)
        total_chars = sum(len(turn.content or "") for turn in parent.trajectory)
        if len(parent.trajectory) <= max_turns and total_chars <= max_chars:
            return [parent]

        segments: list[Session] = []
        active: list[Turn] = []
        active_chars = 0
        active_start = 0

        def finish(turn_end: int) -> None:
            nonlocal active, active_chars, active_start
            if not active:
                return
            index = len(segments) + 1
            segment_id = f"seg-{index:04d}"
            compacted = _compact_session_segment(active, max_chars)
            segments.append(
                Session(
                    agent_type=parent.agent_type,
                    session_id=f"{parent.session_id}:{segment_id}",
                    started_at=active[0].ts or parent.started_at,
                    ended_at=active[-1].ts or parent.ended_at,
                    model=parent.model,
                    cwd=parent.cwd,
                    agent_version=parent.agent_version,
                    trajectory=compacted,
                    extra={
                        **parent.extra,
                        "parent_session_id": parent.session_id,
                        "segment_id": segment_id,
                        "source_turn_start": active_start,
                        "source_turn_end": turn_end,
                        "task_status": "complete",
                        "stored_chars": sum(len(turn.content or "") for turn in compacted),
                    },
                )
            )
            active = []
            active_chars = 0
            active_start = turn_end + 1

        for turn_index, turn in enumerate(parent.trajectory):
            starts_new_task = turn.role == "user" and _is_meaningful_task_text(turn.content)
            over_target = len(active) >= max_turns or active_chars >= max_chars
            if active and starts_new_task and over_target:
                finish(turn_index - 1)
                active_start = turn_index
            active.append(turn)
            active_chars += len(turn.content or "")
            has_summary = bool(
                re.search(r"(?im)^\s*\[task-summary\]\s*[:：]", turn.content or "")
            )
            hard_limit = len(active) >= max_turns * 2 or active_chars >= max_chars * 2
            if has_summary or hard_limit:
                finish(turn_index)
        finish(len(parent.trajectory) - 1)
        return segments


# ---------------------------------------------------------------------------
# Adapter: Cursor (state.vscdb SQLite + protobuf blobs).
#
# This wraps the proven cursor_sft_delivery extractor when present locally
# (preferred — it handles the protobuf wire format and v13 schema). Falls
# back to a minimal "list workspaces" probe otherwise so the user gets a
# clear "install the official extractor" message rather than a silent miss.
# ---------------------------------------------------------------------------

class CursorAdapter:
    name = "cursor"

    @staticmethod
    def user_dirs() -> list[Path]:
        candidates = [
            Path.home() / "Library" / "Application Support" / "Cursor" / "User",
            Path.home() / ".config" / "Cursor" / "User",
            Path(os.environ.get("APPDATA", "")) / "Cursor" / "User"
            if os.environ.get("APPDATA") else None,
        ]
        return [p for p in candidates if p and p.is_dir()]

    @classmethod
    def available(cls) -> bool:
        return any(d.exists() for d in cls.user_dirs())

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for user_dir in cls.user_dirs():
            for state_db in user_dir.glob("workspaceStorage/*/state.vscdb"):
                if not state_db.is_file():
                    continue
                try:
                    conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
                    try:
                        rows = list(conn.execute(
                            "SELECT key FROM cursorDiskKV WHERE key LIKE 'composerData:%' LIMIT ?",
                            (limit,),
                        ))
                    finally:
                        conn.close()
                except sqlite3.Error:
                    continue
                for (key,) in rows:
                    composer_id = key.replace("composerData:", "")
                    out.append({
                        "id": composer_id,
                        "path": str(state_db),
                        "mtime": _dt.datetime.fromtimestamp(state_db.stat().st_mtime).isoformat(timespec="seconds"),
                        "workspace": state_db.parent.parent.name,
                    })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return out[:limit]

    @classmethod
    def parse(cls, ident: str) -> Session:
        """Cursor extraction needs the v13_data_convert pipeline (protobuf parse,
        ~1000 LoC). We prefer to delegate to a sibling cursor_sft_delivery
        checkout when present, then re-read its enriched JSONL."""
        helper_root = _find_cursor_extractor()
        if helper_root is None:
            raise SystemExit(
                "[cursor] Cursor session extraction needs the cursor_sft_delivery\n"
                "extractor (it parses the protobuf-encoded v13 schema). Either:\n"
                "  1. clone https://expool.clawsii.com/cursor_sft_delivery (or your\n"
                "     internal mirror) under ~/cursor_sft_delivery, or\n"
                "  2. run that pipeline yourself and pass --source generic with\n"
                "     the resulting v13_training_data_enriched.jsonl"
            )
        # Resolve the requested session id within the latest enriched JSONL,
        # or run the pipeline first if it hasn't been run.
        cache_dir = Path.home() / ".experience-pool" / "cache" / "cursor"
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_jsonl = cache_dir / "v13_training_data_enriched.jsonl"
        if not out_jsonl.exists() or _stale(out_jsonl, hours=6):
            _run_cursor_extractor(helper_root, cache_dir)
        return _read_cursor_enriched(out_jsonl, ident)


def _find_cursor_extractor() -> Path | None:
    candidates = [
        Path.home() / "cursor_sft_delivery",
        Path.home() / "Downloads" / "cursor_sft_delivery",
        Path(os.environ.get("CURSOR_SFT_DIR", "")) if os.environ.get("CURSOR_SFT_DIR") else None,
    ]
    for c in candidates:
        if c and (c / "scripts" / "full_pipeline.py").exists():
            return c
    return None


def _stale(p: Path, hours: int) -> bool:
    age_h = (_dt.datetime.now().timestamp() - p.stat().st_mtime) / 3600.0
    return age_h > hours


def _run_cursor_extractor(root: Path, cache: Path) -> None:
    import subprocess
    rc = subprocess.call(
        [sys.executable, str(root / "collector" / "v13_data_convert.py"),
         "--output", str(cache / "v13_training_data.jsonl"),
         "--meta", str(cache / "v13_data_convert_meta.jsonl")],
        cwd=str(root),
    )
    if rc != 0:
        raise SystemExit(f"[cursor] extractor exit {rc}")
    rc = subprocess.call(
        [sys.executable, str(root / "collector" / "v13_enrich_turns.py"),
         str(cache / "v13_training_data.jsonl"),
         "-o", str(cache / "v13_training_data_enriched.jsonl")],
        cwd=str(root),
    )
    if rc != 0:
        raise SystemExit(f"[cursor] enricher exit {rc}")


def _read_cursor_enriched(jsonl_path: Path, ident: str) -> Session:
    target = ident.lower()
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = rec.get("session_id") or rec.get("composer_id") or rec.get("id") or ""
        if target == "latest" or sid.lower().startswith(target):
            messages = rec.get("messages") or rec.get("trajectory") or []
            turns: list[Turn] = []
            model = ""
            for m in messages:
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                turns.append(Turn(role=role or "user", content=content))
                if not model and m.get("model"):
                    model = m["model"]
            return Session(
                agent_type=CursorAdapter.name,
                session_id=sid,
                started_at=rec.get("started_at", ""),
                ended_at=rec.get("ended_at", ""),
                model=model or rec.get("model", "unknown"),
                cwd=rec.get("cwd", ""),
                agent_version=rec.get("cursor_version", ""),
                trajectory=turns,
                extra={"mode": rec.get("mode", "")},
            )
    raise FileNotFoundError(f"cursor session id not found in enriched output: {ident}")


# ---------------------------------------------------------------------------
# Adapter: Hermes Agent (~/.hermes/sessions/*.json[l])
#
# File patterns:
#   request_dump_*.json         debug request dumps — skipped
#   session_*.json              JSON object with request.body.messages
#   <yyyymmdd>_<hash>.jsonl     line-per-turn {role, content[, ...]}
# ---------------------------------------------------------------------------

class HermesAdapter:
    name = "hermes"

    @staticmethod
    def roots() -> list[Path]:
        env = os.environ.get("EXP_HERMES_ROOT")
        if env:
            return [Path(env).expanduser()]
        seen: set[str] = set()
        out: list[Path] = []
        for p in (Path.home() / ".hermes" / "sessions",
                  Path.home() / ".Hermes" / "sessions"):
            if p.is_dir():
                key = str(p.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    out.append(p)
        return out

    @classmethod
    def available(cls) -> bool:
        return bool(cls.roots())

    @staticmethod
    def _is_session_file(p: Path) -> bool:
        if p.name.startswith("request_dump_"):
            return False
        return p.suffix in {".json", ".jsonl"}

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        files: list[Path] = []
        for root in cls.roots():
            for f in root.iterdir():
                if f.is_file() and cls._is_session_file(f):
                    files.append(f)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for f in files[:limit]:
            kind = "json-bundle" if f.suffix == ".json" else "jsonl"
            out.append({
                "id": f.stem,
                "path": str(f),
                "mtime": _dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                "size_bytes": f.stat().st_size,
                "format": kind,
            })
        return out

    @classmethod
    def resolve_session(cls, ident: str) -> Path:
        p = Path(ident).expanduser()
        if p.is_file():
            return p
        for root in cls.roots():
            for cand in root.iterdir():
                if not cand.is_file() or not cls._is_session_file(cand):
                    continue
                if cand.stem == ident or cand.stem.startswith(ident):
                    return cand
        raise FileNotFoundError(f"hermes session not found: {ident}")

    @classmethod
    def parse(cls, ident: str | Path) -> Session:
        path = Path(ident).expanduser() if isinstance(ident, (str, Path)) and Path(ident).expanduser().is_file() \
               else cls.resolve_session(str(ident))
        if path.suffix == ".jsonl":
            return cls._parse_jsonl(path)
        return cls._parse_json_bundle(path)

    @classmethod
    def _parse_jsonl(cls, path: Path) -> Session:
        turns: list[Turn] = []
        model = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = obj.get("role") or obj.get("type") or "user"
            content = obj.get("content", "")
            tool_calls: list[dict[str, Any]] = []
            tool_result_for = ""
            if isinstance(content, list):
                # Anthropic-style content blocks
                text_parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "text":
                        text_parts.append(block.get("text", ""))
                    elif bt == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input": block.get("input", {}),
                        })
                    elif bt == "tool_result":
                        tool_result_for = block.get("tool_use_id", "")
                        rc = block.get("content", "")
                        if isinstance(rc, list):
                            rc = "\n".join(b.get("text", "") for b in rc
                                           if isinstance(b, dict) and b.get("type") == "text")
                        text_parts.append(rc if isinstance(rc, str) else json.dumps(rc, ensure_ascii=False))
                content = "\n".join(p for p in text_parts if p)
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            if not content.strip() and not tool_calls:
                continue
            turns.append(Turn(
                role=role, content=content,
                ts=obj.get("ts", obj.get("timestamp", "")),
                tool_calls=tool_calls,
                tool_result_for=tool_result_for,
            ))
            if not model:
                model = obj.get("model", "") or obj.get("source_model", "")
        return Session(
            agent_type=cls.name,
            session_id=path.stem,
            started_at="",
            ended_at=_dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            model=model or "hermes-unknown",
            cwd="",
            agent_version="",
            trajectory=turns,
            extra={"source_path": str(path), "format": "jsonl"},
        )

    @classmethod
    def _parse_json_bundle(cls, path: Path) -> Session:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"[hermes] {path.name} is not valid JSON: {e}")
        if not isinstance(data, dict):
            raise SystemExit(f"[hermes] {path.name} top-level is not an object")
        # Two layouts seen in the wild:
        #   A) {"messages": [...], "model": "...", "session_id": "..."}
        #   B) {"request": {"body": {"messages": [...], "model": "..."}}, ...}
        body = data.get("request", {}).get("body", {}) if isinstance(data.get("request"), dict) else {}
        messages = data.get("messages") or data.get("history") or data.get("turns") \
            or body.get("messages") or []
        model = data.get("model") or body.get("model") or ""
        system_msgs: list[dict[str, Any]] = []
        turns: list[Turn] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                content = "\n".join(text_parts)
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            if role == "system":
                system_msgs.append({"role": "system", "content": content})
                continue
            if not content.strip():
                continue
            turns.append(Turn(role=role, content=content))
        return Session(
            agent_type=cls.name,
            session_id=data.get("session_id") or path.stem,
            started_at=data.get("timestamp", ""),
            ended_at=_dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            model=model or "hermes-unknown",
            cwd=data.get("cwd", ""),
            agent_version=data.get("hermes_version", data.get("version", "")),
            trajectory=turns,
            extra={
                "source_path": str(path),
                "format": "json-bundle",
                "system": system_msgs,
                "tools": body.get("tools", []),
                "reason": data.get("reason", ""),
            },
        )


# ---------------------------------------------------------------------------
# Adapter: agents-chat (~/agents-chat/messages.db, SQLite, multi-agent peer)
# Each thread_id is one "session". Trace is the chronological message list.
# ---------------------------------------------------------------------------

class AgentsChatAdapter:
    name = "agents-chat"

    @staticmethod
    def db_paths() -> list[Path]:
        env = os.environ.get("EXP_AGENTS_CHAT_DB")
        if env:
            return [Path(env).expanduser()]
        return [p for p in [
            Path.home() / "agents-chat" / "messages.db",
        ] if p.is_file()]

    @classmethod
    def available(cls) -> bool:
        return bool(cls.db_paths())

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for db in cls.db_paths():
            try:
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                rows = conn.execute(
                    "SELECT thread_id, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
                    "GROUP_CONCAT(DISTINCT sender) AS senders "
                    "FROM messages GROUP BY thread_id ORDER BY last_ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                conn.close()
            except sqlite3.Error as e:
                continue
            for thread_id, n, first_ts, last_ts, senders in rows:
                out.append({
                    "id": thread_id,
                    "path": str(db),
                    "msg_count": n,
                    "started_at": first_ts,
                    "ended_at": last_ts,
                    "participants": (senders or "").split(","),
                })
        return out

    @classmethod
    def parse(cls, ident: str) -> Session:
        db = cls.db_paths()[0] if cls.db_paths() else None
        if db is None:
            raise SystemExit("[agents-chat] no messages.db found")
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT id, sender, recipient, content, attachments, workflow, stage, ts, reply_to "
            "FROM messages WHERE thread_id = ? ORDER BY ts ASC", (ident,),
        ).fetchall()
        conn.close()
        if not rows:
            # Allow prefix match
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT id, sender, recipient, content, attachments, workflow, stage, ts, reply_to, thread_id "
                "FROM messages WHERE thread_id LIKE ? ORDER BY ts ASC LIMIT 500", (f"{ident}%",),
            ).fetchall()
            conn.close()
            if not rows:
                raise FileNotFoundError(f"agents-chat thread not found: {ident}")
        turns: list[Turn] = []
        senders = set()
        first_ts = rows[0][7]
        last_ts = rows[-1][7]
        for row in rows:
            mid, sender, recipient, content, attachments, workflow, stage, ts = row[:8]
            senders.add(sender)
            # Map sender→role: anything that's the human user is "user"; agents "assistant"
            role = "assistant" if sender not in {"xiehaohui", "xiaohui", "user", "human"} else "user"
            tag = f"[{sender}→{recipient}{f' /{workflow}/{stage}' if workflow else ''}] "
            turns.append(Turn(role=role, content=tag + (content or ""), ts=ts or ""))
        return Session(
            agent_type=cls.name,
            session_id=ident,
            started_at=first_ts or "",
            ended_at=last_ts or "",
            model="multi-agent",
            cwd=str(db.parent),
            agent_version="",
            trajectory=turns,
            extra={"participants": sorted(senders), "db": str(db)},
        )


# ---------------------------------------------------------------------------
# Adapter: Aider (project-local .aider.chat.history.md)
# ---------------------------------------------------------------------------

_AIDER_TURN_RE = re.compile(r"^####\s+(.*)$", re.MULTILINE)


class AiderAdapter:
    name = "aider"

    @staticmethod
    def history_paths(cwd: Path | None = None) -> list[Path]:
        cwd = cwd or Path.cwd()
        return [p for p in [cwd / ".aider.chat.history.md"] if p.exists()]

    @classmethod
    def available(cls) -> bool:
        return bool(cls.history_paths())

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for h in cls.history_paths():
            out.append({
                "id": h.parent.name,
                "path": str(h),
                "mtime": _dt.datetime.fromtimestamp(h.stat().st_mtime).isoformat(timespec="seconds"),
                "size_bytes": h.stat().st_size,
            })
        return out[:limit]

    @classmethod
    def parse(cls, path: Path | str) -> Session:
        p = Path(path).expanduser()
        if p.is_dir():
            p = p / ".aider.chat.history.md"
        text = p.read_text(encoding="utf-8")
        # Aider history uses `#### user message` then `<assistant text>` blocks.
        # Split on user-turn markers.
        chunks = re.split(r"^####\s+", text, flags=re.MULTILINE)
        turns: list[Turn] = []
        # First chunk is preamble — skip.
        for chunk in chunks[1:]:
            head, _, rest = chunk.partition("\n")
            user_msg = head.strip()
            assistant_msg = rest.strip()
            if user_msg:
                turns.append(Turn(role="user", content=user_msg))
            if assistant_msg:
                turns.append(Turn(role="assistant", content=assistant_msg))
        return Session(
            agent_type=cls.name,
            session_id=p.parent.name,
            started_at="",
            ended_at=_dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            model="aider-unknown",
            cwd=str(p.parent),
            agent_version="",
            trajectory=turns,
            extra={"source_path": str(p)},
        )


# ---------------------------------------------------------------------------
# Adapter: Continue.dev (VSCode extension; sessions under ~/.continue/sessions)
# ---------------------------------------------------------------------------

class ContinueDevAdapter:
    name = "continue-dev"

    @staticmethod
    def root() -> Path:
        return Path.home() / ".continue" / "sessions"

    @classmethod
    def available(cls) -> bool:
        return cls.root().is_dir()

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        root = cls.root()
        if not root.is_dir():
            return []
        files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{
            "id": p.stem,
            "path": str(p),
            "mtime": _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "size_bytes": p.stat().st_size,
        } for p in files[:limit]]

    @classmethod
    def parse(cls, ident: str) -> Session:
        p = Path(ident).expanduser()
        if not p.is_file():
            for f in cls.root().glob(f"{ident}*.json"):
                p = f
                break
        if not p.is_file():
            raise FileNotFoundError(f"continue.dev session not found: {ident}")
        data = json.loads(p.read_text(encoding="utf-8"))
        # Continue.dev shape: {"history":[{"message":{"role,content},"contextItems":[...]}]}
        history = data.get("history") or data.get("messages") or []
        turns: list[Turn] = []
        model = data.get("model") or ""
        for item in history:
            msg = item.get("message", item) if isinstance(item, dict) else {}
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            if not content.strip():
                continue
            turns.append(Turn(role=role, content=content))
        return Session(
            agent_type=cls.name,
            session_id=p.stem,
            started_at="",
            ended_at=_dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            model=model or "continue-unknown",
            cwd="",
            agent_version="",
            trajectory=turns,
            extra={"source_path": str(p)},
        )


# ---------------------------------------------------------------------------
# Adapter: Open Interpreter (~/Library/Application Support/Open Interpreter/...)
# Files: <profile>/conversations/<id>.json — OpenAI-shaped messages.
# ---------------------------------------------------------------------------

class OpenInterpreterAdapter:
    name = "open-interpreter"

    @staticmethod
    def roots() -> list[Path]:
        candidates = [
            Path.home() / "Library" / "Application Support" / "Open Interpreter" / "profiles",
            Path.home() / ".config" / "Open Interpreter" / "profiles",
            Path.home() / ".cache" / "open-interpreter" / "conversations",
        ]
        return [p for p in candidates if p.is_dir()]

    @classmethod
    def available(cls) -> bool:
        return bool(cls.roots())

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        files: list[Path] = []
        for r in cls.roots():
            files.extend(r.rglob("*.json"))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [{
            "id": p.stem,
            "path": str(p),
            "mtime": _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "size_bytes": p.stat().st_size,
        } for p in files[:limit]]

    @classmethod
    def parse(cls, ident: str) -> Session:
        p = Path(ident).expanduser()
        if not p.is_file():
            for r in cls.roots():
                for f in r.rglob(f"{ident}*"):
                    if f.is_file():
                        p = f
                        break
        if not p.is_file():
            raise FileNotFoundError(f"open-interpreter session not found: {ident}")
        return GenericAdapter.parse(str(p))


# ---------------------------------------------------------------------------
# Adapter: Codex CLI (~/.codex/sessions/*.json[l])
# ---------------------------------------------------------------------------

CODEX_TOOL_OUTPUT_CHARS = int(os.environ.get("EXP_CODEX_TOOL_OUTPUT_CHARS", "12000"))
CODEX_MESSAGE_CHARS = int(os.environ.get("EXP_CODEX_MESSAGE_CHARS", "48000"))
CODEX_REASONING_CHARS = int(os.environ.get("EXP_CODEX_REASONING_CHARS", "16000"))
CODEX_TASK_CHARS = int(os.environ.get("EXP_CODEX_TASK_CHARS", "1200000"))


def _clip_codex_content(text: Any, limit: int) -> str:
    value = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    if limit <= 0 or len(value) <= limit:
        return value
    marker = f"\n\n[... truncated {len(value) - limit} chars from runtime output ...]\n\n"
    available = max(200, limit - len(marker))
    head = max(120, int(available * 0.7))
    tail = max(80, available - head)
    return value[:head] + marker + value[-tail:]


def _compact_codex_turns(turns: list[Turn]) -> tuple[list[Turn], dict[str, int]]:
    """Bound runtime dumps while preserving user goals, actions and outcomes."""
    original_chars = sum(len(t.content or "") for t in turns)
    truncated = 0
    for turn in turns:
        limit = CODEX_MESSAGE_CHARS
        if turn.role == "tool":
            limit = CODEX_TOOL_OUTPUT_CHARS
        elif (turn.content or "").startswith("💭 思考"):
            limit = CODEX_REASONING_CHARS
        clipped = _clip_codex_content(turn.content or "", limit)
        if clipped != (turn.content or ""):
            truncated += 1
            turn.content = clipped

    total = sum(len(t.content or "") for t in turns)
    if total > CODEX_TASK_CHARS:
        # Tool output is the least useful part for storage/retrieval. Shrink it
        # first, then reasoning. User prompts, tool arguments and final answers
        # remain intact unless the task is still over the hard budget.
        for role, marker, limit in (
            ("tool", "", 1800),
            ("assistant", "💭 思考", 3200),
            ("assistant", "", 12000),
        ):
            for turn in turns:
                if total <= CODEX_TASK_CHARS:
                    break
                if turn.role != role:
                    continue
                if marker and not (turn.content or "").startswith(marker):
                    continue
                if not marker and role == "assistant" and (turn.content or "").startswith("💭 思考"):
                    continue
                before = len(turn.content or "")
                clipped = _clip_codex_content(turn.content or "", limit)
                if len(clipped) < before:
                    turn.content = clipped
                    total -= before - len(clipped)
                    truncated += 1

    return turns, {
        "original_chars": original_chars,
        "stored_chars": sum(len(t.content or "") for t in turns),
        "truncated_turns": truncated,
    }


def _codex_response_turns(payload: dict[str, Any], ts: str) -> list[Turn]:
    ptype = payload.get("type")
    if ptype == "message":
        role = {"developer": "system", "tool": "tool"}.get(
            payload.get("role"), payload.get("role")
        )
        if role not in ("user", "assistant", "system", "tool"):
            return []
        content = payload.get("content", "")
        text_parts: list[str] = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in ("input_text", "output_text", "text"):
                    text_parts.append(item.get("text", "") or "")
                elif item.get("type") in ("input_image", "image"):
                    text_parts.append("[image]")
        merged = "\n".join(part for part in text_parts if part.strip())
        return [Turn(role=role, content=merged, ts=ts)] if merged.strip() else []

    if ptype == "reasoning":
        parts: list[str] = []
        summary = payload.get("summary") or []
        if isinstance(summary, list):
            for item in summary:
                if isinstance(item, dict):
                    value = item.get("text") or ""
                else:
                    value = item if isinstance(item, str) else ""
                if value.strip():
                    parts.append(value)
        inline = payload.get("content")
        if isinstance(inline, str) and inline.strip():
            parts.append(inline)
        if parts:
            return [Turn(role="assistant", content="💭 思考\n\n" + "\n\n".join(parts), ts=ts)]
        return []

    if ptype in {"function_call", "custom_tool_call"}:
        name = payload.get("name", "tool")
        raw_input = payload.get("arguments") if ptype == "function_call" else payload.get("input")
        try:
            parsed_input = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
        except (json.JSONDecodeError, TypeError):
            parsed_input = raw_input
        return [Turn(
            role="assistant",
            content="",
            ts=ts,
            tool_calls=[{
                "id": payload.get("call_id", ""),
                "name": name,
                "input": parsed_input,
                "kind": ptype,
            }],
        )]

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
        if not isinstance(display, str):
            display = json.dumps(display, ensure_ascii=False)
        return [Turn(
            role="tool",
            content=display,
            ts=ts,
            tool_result_for=payload.get("call_id", ""),
        )]
    return []

class CodexAdapter:
    name = "codex"

    @staticmethod
    def root() -> Path:
        return Path.home() / ".codex" / "sessions"

    @classmethod
    def available(cls) -> bool:
        return cls.root().is_dir()

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        root = cls.root()
        if not root.is_dir():
            return []
        files = sorted(
            list(root.rglob("*.json")) + list(root.rglob("*.jsonl")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [{
            "id": p.stem,
            "path": str(p),
            "mtime": _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "size_bytes": p.stat().st_size,
        } for p in files[:limit]]

    @classmethod
    def resolve_session(cls, ident: str) -> Path:
        path = Path(ident).expanduser()
        if path.is_file():
            return path
        stem = Path(ident).name
        for candidate in cls.root().rglob(f"{stem}*"):
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(f"codex session not found: {ident}")

    @classmethod
    def _metadata(cls, path: Path) -> dict[str, str]:
        meta = {"model": "", "cwd": "", "agent_version": "", "started_at": ""}
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for index, raw in enumerate(handle):
                    if index >= 256:
                        break
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    ts = str(record.get("timestamp") or "")
                    if ts and not meta["started_at"]:
                        meta["started_at"] = ts
                    payload = record.get("payload") or {}
                    if not isinstance(payload, dict):
                        continue
                    if record.get("type") == "session_meta":
                        meta["cwd"] = meta["cwd"] or str(payload.get("cwd") or "")
                        meta["agent_version"] = meta["agent_version"] or str(payload.get("cli_version") or "")
                        meta["model"] = meta["model"] or str(payload.get("model") or "")
                    elif record.get("type") == "turn_context":
                        meta["cwd"] = str(payload.get("cwd") or meta["cwd"])
                        meta["model"] = str(payload.get("model") or meta["model"])
                    if meta["cwd"] and meta["model"] and meta["agent_version"]:
                        break
        except OSError:
            pass
        return meta

    @classmethod
    def parse_tasks(
        cls,
        ident: str,
        *,
        start_offset: int = 0,
        include_incomplete: bool = False,
        max_tasks: int | None = None,
    ) -> tuple[list[Session], int]:
        """Stream completed Codex tasks and return a safe resume byte offset.

        Codex keeps appending many independent tasks to one rollout file. The
        returned offset stops before an unfinished task, so the next daemon
        tick can resume without re-reading the full file or losing that task.
        """
        path = cls.resolve_session(ident)
        metadata = cls._metadata(path)
        tasks: list[Session] = []
        active: dict[str, Any] | None = None
        fallback_turns: list[Turn] = []
        safe_offset = max(0, start_offset)
        partial_offset: int | None = None

        def finish(status: str, end_offset: int, ended_at: str) -> None:
            nonlocal active, safe_offset
            if active is None:
                safe_offset = end_offset
                return
            turns, compact = _compact_codex_turns(active["turns"])
            turn_id = str(active.get("turn_id") or active["start_offset"])
            if turns:
                tasks.append(Session(
                    agent_type=cls.name,
                    session_id=f"{path.stem}:{turn_id}",
                    started_at=str(active.get("started_at") or metadata["started_at"]),
                    ended_at=ended_at or str(active.get("last_ts") or ""),
                    model=str(active.get("model") or metadata["model"] or "codex-unknown"),
                    cwd=str(active.get("cwd") or metadata["cwd"] or path.parent),
                    agent_version=metadata["agent_version"],
                    trajectory=turns,
                    extra={
                        "source_path": str(path),
                        "parent_session_id": path.stem,
                        "codex_turn_id": turn_id,
                        "byte_start": int(active["start_offset"]),
                        "byte_end": end_offset,
                        "task_status": status,
                        **compact,
                    },
                ))
            safe_offset = end_offset
            active = None

        with path.open("rb") as handle:
            handle.seek(max(0, start_offset))
            while True:
                line_start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                line_end = handle.tell()
                try:
                    record = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    if not raw.endswith(b"\n"):
                        partial_offset = line_start
                        break
                    continue
                if not isinstance(record, dict):
                    continue
                ts = str(record.get("timestamp") or record.get("ended_at") or "")
                payload = record.get("payload") or {}
                payload = payload if isinstance(payload, dict) else {}
                ptype = payload.get("type")

                if record.get("type") == "event_msg" and ptype == "task_started":
                    if active is not None:
                        if include_incomplete:
                            finish("superseded", line_start, str(active.get("last_ts") or ts))
                        else:
                            safe_offset = int(active["start_offset"])
                            break
                    active = {
                        "turn_id": payload.get("turn_id") or "",
                        "start_offset": line_start,
                        "started_at": ts,
                        "last_ts": ts,
                        "model": metadata["model"],
                        "cwd": metadata["cwd"],
                        "turns": [],
                    }
                    continue

                if record.get("type") == "turn_context":
                    if active is not None:
                        active["model"] = str(payload.get("model") or active["model"])
                        active["cwd"] = str(payload.get("cwd") or active["cwd"])
                        active["last_ts"] = ts or active["last_ts"]
                    continue

                if record.get("type") == "response_item":
                    parsed = _codex_response_turns(payload, ts)
                    if active is not None:
                        active["turns"].extend(parsed)
                        active["last_ts"] = ts or active["last_ts"]
                    elif start_offset == 0:
                        fallback_turns.extend(parsed)
                    continue

                if record.get("type") == "event_msg" and ptype in {"task_complete", "turn_aborted"}:
                    if active is not None:
                        finish("complete" if ptype == "task_complete" else "aborted", line_end, ts)
                        if max_tasks is not None and len(tasks) >= max_tasks:
                            break
                    else:
                        safe_offset = line_end

            eof_offset = handle.tell()

        if active is not None:
            if include_incomplete:
                finish("open", partial_offset or eof_offset, str(active.get("last_ts") or ""))
            else:
                safe_offset = int(active["start_offset"])
        elif partial_offset is not None:
            safe_offset = min(safe_offset or partial_offset, partial_offset)
        elif not tasks and fallback_turns and start_offset == 0:
            turns, compact = _compact_codex_turns(fallback_turns)
            tasks.append(Session(
                agent_type=cls.name,
                session_id=path.stem,
                started_at=metadata["started_at"],
                ended_at=_dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                model=metadata["model"] or "codex-unknown",
                cwd=metadata["cwd"] or str(path.parent),
                agent_version=metadata["agent_version"],
                trajectory=turns,
                extra={"source_path": str(path), "byte_start": 0, "byte_end": eof_offset, **compact},
            ))
            safe_offset = eof_offset
        elif active is None and partial_offset is None and (max_tasks is None or len(tasks) < max_tasks):
            safe_offset = max(safe_offset, eof_offset)
        return tasks, safe_offset

    @classmethod
    def parse(cls, ident: str) -> Session:
        path = cls.resolve_session(ident)
        tasks, _ = cls.parse_tasks(str(path), include_incomplete=True)
        if not tasks:
            metadata = cls._metadata(path)
            return Session(
                agent_type=cls.name,
                session_id=path.stem,
                started_at=metadata["started_at"],
                ended_at=_dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                model=metadata["model"] or "codex-unknown",
                cwd=metadata["cwd"] or str(path.parent),
                agent_version=metadata["agent_version"],
                trajectory=[],
                extra={"source_path": str(path)},
            )
        if len(tasks) == 1 and tasks[0].session_id == path.stem:
            return tasks[0]
        turns = [turn for task in tasks for turn in task.trajectory]
        return Session(
            agent_type=cls.name,
            session_id=path.stem,
            started_at=tasks[0].started_at,
            ended_at=tasks[-1].ended_at,
            model=tasks[-1].model,
            cwd=tasks[-1].cwd,
            agent_version=tasks[-1].agent_version,
            trajectory=turns,
            extra={"source_path": str(path), "task_count": len(tasks)},
        )


# ---------------------------------------------------------------------------
# Adapter: Generic JSON ({"messages":[...]} or {"trajectory":[...]} or [...])
# ---------------------------------------------------------------------------

class GenericAdapter:
    """Universal fallback. Auto-detects common shapes:

    1. JSON array of messages — `[{role, content}, ...]` (OpenAI chat-completion,
       LangChain `chat_history`, AutoGen logs, CrewAI traces).
    2. JSON object with one of: `trajectory`, `messages`, `turns`, `history`,
       `conversation`, `chat_history`, `request.body.messages`.
    3. JSONL — one JSON object per line, each with `role`/`content` (plus
       optional `tool_calls`, `tool_call_id`, `name`, `timestamp`).
    4. LangSmith-style runs: `{"runs":[{"inputs":{"messages":[...]},"outputs":...}]}`.
    5. AutoGen group-chat: `[{"name":"agent_x","content":"..."}]`.
    6. Plain text — read as a single user turn.
    """

    name = "generic"

    @classmethod
    def available(cls) -> bool:
        return True

    @classmethod
    def list_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def _extract_message_list(raw: Any) -> tuple[list[Any], dict[str, Any]]:
        if isinstance(raw, list):
            return raw, {}
        if isinstance(raw, dict):
            for key in ("trajectory", "messages", "turns", "history",
                        "conversation", "chat_history", "events"):
                if isinstance(raw.get(key), list) and raw[key]:
                    meta = {k: v for k, v in raw.items() if k != key}
                    return raw[key], meta
            # request.body.messages (OpenAI request dump)
            body = raw.get("request", {}).get("body", {}) if isinstance(raw.get("request"), dict) else {}
            if isinstance(body.get("messages"), list):
                meta = {k: v for k, v in raw.items() if k != "request"}
                meta["request_meta"] = {k: v for k, v in body.items() if k != "messages"}
                return body["messages"], meta
            # LangSmith-style
            runs = raw.get("runs")
            if isinstance(runs, list) and runs:
                msgs: list[Any] = []
                for r in runs:
                    inp = r.get("inputs", {}).get("messages") if isinstance(r, dict) else None
                    if inp:
                        msgs.extend(inp)
                    out = r.get("outputs") if isinstance(r, dict) else None
                    if isinstance(out, dict):
                        msgs.append({"role": "assistant", "content": json.dumps(out, ensure_ascii=False)})
                if msgs:
                    return msgs, {k: v for k, v in raw.items() if k != "runs"}
        return [], {}

    @staticmethod
    def _coerce_role(name: str, role: str) -> str:
        """AutoGen / CrewAI use `name` for agent identity; map to assistant/user."""
        r = (role or "").lower().strip()
        if r in {"user", "human"}:
            return "user"
        if r in {"assistant", "ai", "bot", "agent"}:
            return "assistant"
        if r in {"system"}:
            return "system"
        if r in {"tool", "function"}:
            return "tool"
        # If only `name` is given, treat as assistant turn from that agent.
        return "assistant" if name else "user"

    @classmethod
    def _normalize_message(cls, m: Any) -> Turn | None:
        if not isinstance(m, dict):
            return None
        role = cls._coerce_role(m.get("name", ""), m.get("role", m.get("type", "")))
        content = m.get("content")
        if content is None:
            content = m.get("text", m.get("message", ""))
        # Anthropic content blocks
        tool_calls: list[dict[str, Any]] = []
        tool_result_for = m.get("tool_call_id", "") or m.get("tool_use_id", "")
        if isinstance(content, list):
            text_parts: list[str] = []
            for b in content:
                if not isinstance(b, dict):
                    text_parts.append(str(b))
                    continue
                bt = b.get("type", "text")
                if bt == "text":
                    text_parts.append(b.get("text", ""))
                elif bt in ("tool_use", "function_call"):
                    tool_calls.append({
                        "id": b.get("id", ""),
                        "name": b.get("name", ""),
                        "input": b.get("input", b.get("arguments", {})),
                    })
                elif bt == "tool_result":
                    tool_result_for = b.get("tool_use_id", tool_result_for)
                    rc = b.get("content", "")
                    if isinstance(rc, list):
                        rc = "\n".join(b2.get("text", "") for b2 in rc
                                       if isinstance(b2, dict) and b2.get("type") == "text")
                    text_parts.append(rc if isinstance(rc, str) else json.dumps(rc, ensure_ascii=False))
                else:
                    text_parts.append(json.dumps(b, ensure_ascii=False))
            content = "\n".join(p for p in text_parts if p)
        elif not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        # OpenAI tool_calls field on message
        for tc in m.get("tool_calls", []) or []:
            if isinstance(tc, dict):
                fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": fn.get("name", tc.get("name", "")),
                    "input": fn.get("arguments", tc.get("arguments", {})),
                })
        if not content.strip() and not tool_calls:
            return None
        return Turn(
            role=role,
            content=content,
            ts=str(m.get("timestamp", m.get("ts", m.get("created_at", "")))),
            tool_calls=tool_calls,
            tool_result_for=tool_result_for,
        )

    @classmethod
    def parse(cls, path_str: str) -> Session:
        p = Path(path_str).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"file not found: {p}")
        text = p.read_text(encoding="utf-8")
        # Try JSONL first.
        records: list[Any] = []
        meta: dict[str, Any] = {}
        try_jsonl = True
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except json.JSONDecodeError:
                try_jsonl = False
                records = []
                break
        if try_jsonl and len(records) > 1 and all(isinstance(r, dict) for r in records):
            messages = records
        else:
            try:
                raw = json.loads(text)
                messages, meta = cls._extract_message_list(raw)
                if not messages and isinstance(raw, dict) and ("role" in raw or "content" in raw):
                    messages = [raw]
            except json.JSONDecodeError:
                # Plain text — treat as single user turn.
                messages = [{"role": "user", "content": text.strip()}]
                meta = {}
        turns = [t for t in (cls._normalize_message(m) for m in messages) if t is not None]
        return Session(
            agent_type=str(meta.get("agent_type", "generic")),
            session_id=str(meta.get("session_id", p.stem)),
            started_at=str(meta.get("started_at", "")),
            ended_at=str(meta.get("ended_at",
                _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"))),
            model=str(meta.get("model", meta.get("source_model", "unknown"))),
            cwd=str(meta.get("cwd", "")),
            agent_version=str(meta.get("agent_version", meta.get("version", ""))),
            trajectory=turns,
            extra={**meta, "source_path": str(p)},
        )


ADAPTERS: dict[str, Any] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    HermesAdapter.name: HermesAdapter,
    AgentsChatAdapter.name: AgentsChatAdapter,
    ContinueDevAdapter.name: ContinueDevAdapter,
    OpenInterpreterAdapter.name: OpenInterpreterAdapter,
    CursorAdapter.name: CursorAdapter,
    AiderAdapter.name: AiderAdapter,
    CodexAdapter.name: CodexAdapter,
    GenericAdapter.name: GenericAdapter,
}


def _parse_row_time(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return 0.0
    if raw.isdigit():
        return float(raw)
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return _dt.datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _adapter_newest_score(adapter: Any) -> float:
    try:
        rows = adapter.list_sessions(limit=1)
    except Exception:
        return 0.0
    if not rows:
        return 0.0
    row = rows[0]
    score = _parse_row_time(
        row.get("mtime")
        or row.get("ended_at")
        or row.get("updated_at")
        or row.get("created_at")
        or row.get("started_at")
    )
    if score:
        return score
    path = row.get("path")
    if path:
        try:
            return Path(str(path)).expanduser().stat().st_mtime
        except OSError:
            return 0.0
    return 0.0


def detect_source(explicit: str | None = None) -> str:
    if explicit and explicit != "auto":
        return explicit
    # Hook env wins (Claude Code Stop hook sets CLAUDE_SESSION_PATH).
    if os.environ.get("CLAUDE_SESSION_PATH"):
        return ClaudeCodeAdapter.name
    if os.environ.get("HERMES_SESSION_PATH"):
        return HermesAdapter.name
    # Pick the runtime with the newest local session. This makes `/expool:upload`
    # behave correctly on machines where users run several agents side-by-side.
    candidates: list[tuple[float, str]] = []
    for adapter in (
        HermesAdapter, ClaudeCodeAdapter, AgentsChatAdapter,
        ContinueDevAdapter, OpenInterpreterAdapter,
        CursorAdapter, AiderAdapter, CodexAdapter,
    ):
        try:
            if adapter.available():
                candidates.append((_adapter_newest_score(adapter), adapter.name))
        except Exception:
            continue
    candidates = [item for item in candidates if item[0] > 0]
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return GenericAdapter.name


def _adapter_parse(src: str, ident: str) -> Session:
    """Single dispatch for `parse` across all adapters."""
    if src == ClaudeCodeAdapter.name:
        return ClaudeCodeAdapter.parse(ClaudeCodeAdapter.resolve_session(ident))
    if src == HermesAdapter.name:
        return HermesAdapter.parse(ident)
    if src == AgentsChatAdapter.name:
        return AgentsChatAdapter.parse(ident)
    if src == ContinueDevAdapter.name:
        return ContinueDevAdapter.parse(ident)
    if src == OpenInterpreterAdapter.name:
        return OpenInterpreterAdapter.parse(ident)
    if src == AiderAdapter.name:
        return AiderAdapter.parse(ident)
    if src == CodexAdapter.name:
        return CodexAdapter.parse(ident)
    if src == CursorAdapter.name:
        return CursorAdapter.parse(ident)
    return GenericAdapter.parse(ident)


def _adapter_latest_path_or_id(src: str) -> str:
    """Return an identifier the adapter's parse() can consume for the most
    recent session of that source."""
    if src == ClaudeCodeAdapter.name:
        return str(
            Path(os.environ["CLAUDE_SESSION_PATH"]) if os.environ.get("CLAUDE_SESSION_PATH")
            else ClaudeCodeAdapter.latest_session()
        )
    if src == HermesAdapter.name:
        rows = HermesAdapter.list_sessions(limit=1)
        if not rows:
            raise SystemExit("no hermes sessions found")
        return rows[0]["path"]
    if src == AgentsChatAdapter.name:
        rows = AgentsChatAdapter.list_sessions(limit=1)
        if not rows:
            raise SystemExit("no agents-chat threads found")
        return rows[0]["id"]
    if src == ContinueDevAdapter.name:
        rows = ContinueDevAdapter.list_sessions(limit=1)
        if not rows:
            raise SystemExit("no continue.dev sessions found")
        return rows[0]["path"]
    if src == OpenInterpreterAdapter.name:
        rows = OpenInterpreterAdapter.list_sessions(limit=1)
        if not rows:
            raise SystemExit("no open-interpreter sessions found")
        return rows[0]["path"]
    if src == AiderAdapter.name:
        paths = AiderAdapter.history_paths()
        if not paths:
            raise SystemExit("no aider history under cwd")
        return str(paths[0])
    if src == CodexAdapter.name:
        rows = CodexAdapter.list_sessions(limit=1)
        if not rows:
            raise SystemExit("no codex sessions found")
        return rows[0]["path"]
    if src == CursorAdapter.name:
        return "latest"
    raise SystemExit(f"--source {src!r} doesn't support push-latest; use push --session <path>")


# ---------------------------------------------------------------------------
# Lite-card builder (matches cli/src/lite.ts shape).
# ---------------------------------------------------------------------------

def _derive_title_heuristic(query: str) -> str:
    """Fallback title from first user message — used if LLM refine is off
    or fails. Kept as a separate function so the LLM path can wrap it."""
    text = (query or "").strip()
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return "unspecified task"
    head = line[:120]
    cut_at = -1
    for sep in ("。", "！", "？", ". ", "! ", "? ", "\n"):
        idx = head.find(sep)
        if idx > 0 and (cut_at < 0 or idx < cut_at):
            cut_at = idx + len(sep)
    title = head[:cut_at].strip() if cut_at > 0 else head.strip()
    title = " ".join(title.split())
    if len(title) > 70:
        title = title[:69].rstrip() + "…"
    return title or "unspecified task"


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
    "  配置 Claude Code 状态栏\n"
    "  Refactor login flow\n"
    "  Diagnose proxy connectivity issue\n"
    "\n"
    "❌ 错误示范(绝对不允许):\n"
    "  Waiting for your approval to write…\n"
    "  我需要澄清一下\n"
    "  I'll extract this trajectory\n"
    "  The transcript is truncated\n"
    "  <transcript>\n"
    "  Looking at this conversation\n"
    "  📥 connected to experience pool\n"
    "\n"
    "**只输出标题那一行,前后无任何额外文字。**"
)


# Post-LLM filter: reject the response and fall back to heuristic if the
# label looks like model rambling (conversational opener, English filler,
# echoed input markers, etc).
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
    # Ends with ellipsis → was a wrapped sentence, not a title
    if label.endswith(("…", "...")):
        return True
    return False


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


def _clean_task_user_text(content: str) -> str:
    """Return the retrievable task hidden inside runtime wrapper messages."""
    text = str(content or "").strip()
    if not text:
        return ""
    objective = _GOAL_OBJECTIVE_RE.search(text)
    if objective:
        text = objective.group(1).strip()
    elif text.lower().startswith(_TASK_WRAPPER_PREFIXES):
        return ""
    # Runtime image placeholders are useful only when accompanied by an
    # actual request. A bare placeholder has no searchable task semantics.
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
    # A one-character acknowledgement or menu selection is not an
    # independently retrievable task. Keep short technical identifiers.
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 3 and not re.search(r"[A-Za-z][0-9]|[0-9][A-Za-z]", compact):
        return False
    return True


def _session_has_retrievable_task(session: Session) -> bool:
    if any(
        t.role == "user" and _is_meaningful_task_text(t.content)
        for t in session.trajectory
    ):
        return True
    return bool(_extract_task_summary_title(session.trajectory))


def _turn_value(turn: Any, key: str, default: Any = None) -> Any:
    if isinstance(turn, dict):
        return turn.get(key, default)
    return getattr(turn, key, default)


def _pack_transcript(trajectory: list[Any], max_chars: int = 6000) -> str:
    out: list[str] = []
    used = 0
    for t in trajectory:
        role = _turn_value(t, "role", "") or ""
        content = str(_turn_value(t, "content", "") or "").strip()
        tcs = _turn_value(t, "tool_calls", []) or []
        if not content and not tcs:
            continue
        if role == "user":
            content = _clean_task_user_text(content)
            if not _is_meaningful_task_text(content):
                continue
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


def _llm_summarize_title(trajectory: list[Any], timeout: int = 45) -> str | None:
    """Shell out to local `claude -p` to get a one-line title that
    summarises the WHOLE conversation. Returns None on any failure so the
    caller falls back to the heuristic.

    Disabled when EXP_REFINE_TITLES != "1" or claude CLI unavailable.
    """
    if os.environ.get("EXP_REFINE_TITLES", "1") != "1":
        return None
    if os.environ.get("EXP_TITLE_DISABLE", "0") == "1":
        return None
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None
    try:
        transcript = _pack_transcript(trajectory)
        if not transcript.strip():
            return None
        model = os.environ.get("EXP_TITLE_MODEL", "claude-haiku-4-5-20251001")
        # Critical: disable auto-upload + skip session-start in the spawned
        # claude subprocess. Otherwise its SessionEnd hook fires and calls
        # `exp push-latest` again → infinite recursion (push spawns title,
        # title spawns claude, claude spawns push, …).
        env = dict(os.environ)
        env["EXP_AUTO_UPLOAD"] = "0"
        env["EXP_REFINE_TITLES"] = "0"
        env["EXP_TITLE_DISABLE"] = "1"
        proc = subprocess.run(
            [
                claude_bin, "-p", "--output-format", "json",
                "--model", model,
                "--append-system-prompt", _TITLE_SYSTEM,
                # don't write a session file to ~/.claude/projects/ —
                # otherwise daemon-tick picks it up as a new "session"
                # and uploads it (with title `<transcript>` etc.)
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
        if proc.returncode != 0:
            return None
        env = json.loads(proc.stdout)
        if env.get("is_error"):
            return None
        raw = (env.get("result") or "").strip()
        # Strip leading hook-injected "📥 connected to experience pool …"
        # lines and other auto-prepended notices.
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
        if not label or label.lower() in ("(no task)", "(none)"):
            return None
        if _looks_bad_title(label):
            return None
        if len(label) > 60:
            label = label[:59] + "…"
        return label
    except Exception:
        return None


def _derive_title(query: str, trajectory: list[Any] | None = None) -> str:
    """Return an LLM-summarised title when possible, else the heuristic."""
    fallback = _derive_title_heuristic(query)
    if not trajectory:
        return fallback
    llm = _llm_summarize_title(trajectory)
    return llm or fallback


_TASK_SUMMARY_RE = re.compile(r"(?im)^\s*\[task-summary\]\s*[:：]\s*(.+?)\s*$")


def _extract_task_summary_title(trajectory: list[Any]) -> str:
    """Prefer the explicit task-summary marker when the agent emitted one.

    This keeps titles useful even when the LLM title pass is disabled, rate
    limited, or unavailable.
    """
    for t in reversed(trajectory):
        content = _turn_value(t, "content", "") or ""
        if not content:
            continue
        matches = _TASK_SUMMARY_RE.findall(str(content))
        if not matches:
            continue
        label = " ".join(matches[-1].strip().split())
        label = label.strip('"\'`「」『』').strip()
        if label.endswith(("。", ".", "!", "?", "！", "？", ":", "：")):
            label = label[:-1].strip()
        if label and not _looks_bad_title(label):
            return label[:60] + ("…" if len(label) > 60 else "")
    return ""


def build_lite_card(
    session: Session,
    *,
    task_type: str,
    sensitivity: str,
    acl: str,
    tags: list[str],
) -> dict[str, Any]:
    query = ""
    steps: list[str] = []
    outcome = ""
    totals: dict[str, int] = {}
    sanitized_traj: list[dict[str, Any]] = []

    for t in session.trajectory:
        body, counts = sanitize(t.content)
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        # Recursively scrub tool_calls payloads (arguments dicts often contain
        # secrets, file paths, query strings). The flat sanitize() above only
        # handled the role-level `content` string.
        clean_tool_calls = sanitize_node(t.tool_calls, totals)
        sanitized_traj.append({
            "role": t.role,
            "content": body,
            "ts": t.ts,
            "tool_calls": clean_tool_calls,
            "tool_result_for": t.tool_result_for,
        })
        if t.role == "user" and not query:
            clean_user = _clean_task_user_text(body)
            if _is_meaningful_task_text(clean_user):
                query = clean_user[:4000]
        elif t.role == "assistant" and body.strip():
            steps.append(body[:280])
            outcome = body

    task_summary = _extract_task_summary_title(sanitized_traj)
    query = query or task_summary
    intent = task_summary or _derive_title(query, sanitized_traj)
    return {
        "card": {
            "query": query or "(no user turn)",
            "intent": intent,
            "steps": steps,
            "outcome": outcome[:500] or "(no assistant turn)",
            "task_type": task_type,
            "source_model": session.model,
            "sensitivity": sensitivity,
            "acl": acl,
            "tags": tags,
            "redactions": totals,
        },
        "trajectory": sanitized_traj,
    }


# ---------------------------------------------------------------------------
# HMAC signing + HTTP client.
# ---------------------------------------------------------------------------

def sign(secret: str, method: str, path: str, body: bytes) -> str:
    canonical = method.upper().encode() + b"\n" + path.encode() + b"\n" + body
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def http_request(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    cred: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    body_bytes = body_str.encode("utf-8")
    url = base_url.rstrip("/") + path
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if cred:
        api_key = cred.get("api_key") or cred.get("bearer")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            if cred.get("agent_name"):
                headers["X-Agent-Name"] = cred["agent_name"]
        else:
            headers["X-Agent-Name"] = cred["agent_name"]
            headers["X-Signature"] = sign(cred["secret"], method, path, body_bytes)
    req = urllib.request.Request(
        url,
        data=body_bytes if method != "GET" else None,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"[gateway] {method} {path} -> {e.code}: {msg}")
    except urllib.error.URLError as e:
        raise SystemExit(f"[gateway] {method} {path} -> network error: {e.reason}")
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit(f"[gateway] non-JSON response from {path}: {payload[:200]}")


# ---------------------------------------------------------------------------
# Credential storage.
# ---------------------------------------------------------------------------

def cred_path(name: str | None = None) -> Path:
    DEFAULT_CRED_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if name:
        return DEFAULT_CRED_DIR / f"{name}.json"
    # Prefer named file via env, otherwise pick newest.
    env_name = os.environ.get("EXP_AGENT_NAME")
    if env_name:
        env_path = DEFAULT_CRED_DIR / f"{env_name}.json"
        if env_path.exists():
            return env_path
    files = sorted(DEFAULT_CRED_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else DEFAULT_CRED_DIR / "default.json"


def load_credential() -> dict[str, str] | None:
    env_api_key = os.environ.get("EXP_API_KEY") or os.environ.get("EXPOOL_API_KEY")
    if env_api_key:
        return {"auth_type": "api_key", "api_key": env_api_key}
    env_name = os.environ.get("EXP_AGENT_NAME")
    env_secret = os.environ.get("EXP_AGENT_SECRET")
    if env_name and env_secret:
        return {"auth_type": "hmac", "agent_name": env_name, "secret": env_secret}
    p = cred_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("api_key"):
            return {
                "auth_type": "api_key",
                "api_key": data["api_key"],
                "agent_name": data.get("agent_name", ""),
            }
        return {
            "auth_type": "hmac",
            "agent_name": data["agent_name"],
            "secret": data["secret"],
        }
    except Exception:
        return None


def save_credential(cred: dict[str, Any]) -> Path:
    if cred.get("api_key") and not cred.get("agent_name"):
        p = DEFAULT_CRED_DIR / "api-key.json"
    else:
        p = cred_path(cred.get("agent_name"))
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.write_text(json.dumps(cred, indent=2), encoding="utf-8")
    p.chmod(0o600)
    return p


# ---------------------------------------------------------------------------
# CLI commands.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Consent — local opt-in/opt-out gate.
# ---------------------------------------------------------------------------

# exp_consent ships alongside this file in dist-public/. We import it
# defensively so a corrupted install (consent.py missing) still allows
# `exp register`, `exp whoami`, etc.
try:
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import exp_consent  # type: ignore[import-not-found]
except Exception as _exc:
    exp_consent = None  # type: ignore[assignment]
    _CONSENT_LOAD_ERROR = str(_exc)
else:
    _CONSENT_LOAD_ERROR = ""


def _consent_check(*, agent: str, cwd: str, session_id: str = "",
                   force: bool = False, dry_run: bool = False) -> tuple[bool, str, "Any"]:
    """Single gate every push goes through. Returns (allow, reason, decision).

    `force=True` skips the prompt path (used by `exp push --yes`).
    `dry_run=True` short-circuits to allow regardless of consent — the
    caller is responsible for not actually transmitting.
    """
    if exp_consent is None:
        return True, f"consent module unavailable: {_CONSENT_LOAD_ERROR}", None
    if dry_run:
        return True, "dry_run", None
    decision = exp_consent.decide(agent=agent, cwd=cwd, session_id=session_id)
    if decision.mode == "never":
        return False, f"never ({decision.reason})", decision
    if decision.mode == "always" or force:
        return True, decision.mode, decision
    if decision.mode == "dry-run":
        return False, "dry-run mode (saved to pending/)", decision
    # 'ask' or 'prompt-on-start' — interactive
    answer = exp_consent.prompt(agent=agent, cwd=cwd, session_id=session_id)
    if answer == "yes":
        # Remember decision so re-tries on the same session don't re-ask.
        if session_id:
            exp_consent.record_session_override(session_id, "always",
                                               ttl_seconds=24 * 3600)
        return True, "user_yes", decision
    if answer == "never_cwd":
        exp_consent.set_cwd(cwd, "never", reason="user said never_cwd at prompt")
        return False, "user_never_cwd", decision
    if answer == "never_agent":
        exp_consent.set_agent(agent, "never",
                              comment="user said never_agent at prompt")
        return False, "user_never_agent", decision
    # 'no' or timeout
    if session_id:
        exp_consent.record_session_override(session_id, "never",
                                            ttl_seconds=24 * 3600)
    return False, "user_no", decision


# ---------------------------------------------------------------------------
# Consent CLI
# ---------------------------------------------------------------------------

def cmd_consent_show(args: argparse.Namespace) -> int:
    if exp_consent is None:
        print(f"consent module unavailable: {_CONSENT_LOAD_ERROR}", file=sys.stderr)
        return 2
    data = exp_consent.load_consent()
    if args.simulate:
        agent = args.agent or "claude-code"
        cwd = args.cwd or os.getcwd()
        print(json.dumps(exp_consent.explain(agent, cwd, args.session or ""),
                         indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"\nfile: {exp_consent.CONSENT_PATH}", file=sys.stderr)
    return 0


def cmd_consent_set(args: argparse.Namespace) -> int:
    if exp_consent is None:
        print("consent module unavailable", file=sys.stderr)
        return 2
    if args.session and args.mode:
        exp_consent.record_session_override(args.session, args.mode)
        print(f"[exp] session {args.session} → {args.mode}")
    elif args.cwd:
        exp_consent.set_cwd(args.cwd, args.mode, reason=args.reason or "")
        print(f"[exp] cwd {args.cwd} → {args.mode}")
    elif args.agent:
        exp_consent.set_agent(args.agent, args.mode,
                              default_acl=args.acl or None,
                              comment=args.reason or "")
        print(f"[exp] agent {args.agent} → {args.mode}")
    else:
        exp_consent.set_global(args.mode)
        print(f"[exp] global → {args.mode}")
    return 0


def cmd_consent_reset(args: argparse.Namespace) -> int:
    if exp_consent is None:
        return 2
    exp_consent.reset()
    print("[exp] consent reset to defaults")
    return 0


def cmd_consent_decide(args: argparse.Namespace) -> int:
    """Used by hook scripts: prints the decision mode (one word) on stdout
    so shell scripts can `case $(exp consent decide ...) in ...`."""
    if exp_consent is None:
        print("ask")  # safe default — let the prompt path run
        return 0
    decision = exp_consent.decide(
        agent=args.agent or "claude-code",
        cwd=args.cwd or os.getcwd(),
        session_id=args.session or "",
    )
    if args.interactive and decision.mode in ("ask", "prompt-on-start"):
        # Drive the prompt right here.
        answer = exp_consent.prompt(
            agent=args.agent or "claude-code",
            cwd=args.cwd or os.getcwd(),
            session_id=args.session or "",
        )
        if answer == "yes":
            print("upload")
            if args.session:
                exp_consent.record_session_override(args.session, "always",
                                                   ttl_seconds=24 * 3600)
        elif answer == "never_cwd":
            exp_consent.set_cwd(args.cwd or os.getcwd(), "never",
                                reason="prompt:never_cwd")
            print("never")
        elif answer == "never_agent":
            exp_consent.set_agent(args.agent or "claude-code", "never",
                                  comment="prompt:never_agent")
            print("never")
        else:
            print("skip")
            if args.session:
                exp_consent.record_session_override(args.session, "never",
                                                   ttl_seconds=24 * 3600)
        return 0
    # Map decide() output to the simple verb the shell expects.
    print({
        "always": "upload",
        "never": "never",
        "ask": "ask",
        "prompt-on-start": "ask",
        "dry-run": "dry-run",
    }.get(decision.mode, "ask"))
    return 0


def cmd_consent_pending(args: argparse.Namespace) -> int:
    if exp_consent is None:
        return 2
    items = exp_consent.list_pending()
    if args.prune:
        removed = exp_consent.prune_pending()
        print(f"[exp] pruned {removed} pending file(s)")
        return 0
    if not items:
        print("(no pending sessions)")
        return 0
    for it in items:
        print(f"{it['mtime']}  {it['size_bytes']:>8}b  {it['name']}")
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    """GET /v1/me/quota — show this agent's publish_count + community
    unlock state. Useful for users + scripts to check progress."""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp_uploader register` first.")
    res = http_request(args.base, "GET", "/v1/me/quota", cred=cred)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """POST /v1/lite/publish — publish an experience to the community pool.
    Strict sanitize (file://, local resources, localhost, UUIDs) runs first;
    on block the response includes the offending hits + locations."""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp_uploader register` first.")
    res = http_request(
        args.base, "POST", "/v1/lite/publish",
        body={"experience_id": args.eid}, cred=cred,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def cmd_unpublish(args: argparse.Namespace) -> int:
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp_uploader register` first.")
    res = http_request(
        args.base, "POST", "/v1/lite/unpublish",
        body={"experience_id": args.eid}, cred=cred,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1


# ---------------------------------------------------------------------------
# Reading-side commands — these are the API surface every plugin needs.
# CLI 包装的设计目标:让插件 / 外部脚本不必直连 HTTP + HMAC,只用一行
# `exp <verb> --json` 就能拿到结构化结果。
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> int:
    """POST /v1/lite/search — 语义搜索经验池。

    给定 query 文本, 服务端按 (intent + query) 的向量做余弦 top-k, 按
    viewer 身份做 ACL 过滤, 返回 personal + community 两段结果。

    用法 (插件调用最多的命令):
        exp search --q "FastAPI HMAC 签名失败" --top-k 5
        exp search --q "..." --scope personal --json   # 只看自己
        exp search --q "..." --scope community --json  # 只看 community
    """
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp register` or bind first.")
    body = {"q": args.q, "top_k": args.top_k, "scope": args.scope}
    if args.task_type:
        body["task_type"] = args.task_type
    res = http_request(args.base, "POST", "/v1/lite/search", body=body, cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    # 人类可读输出
    results = res.get("results") or []
    if not results:
        print("(no matches)")
        return 0
    for i, r in enumerate(results, 1):
        sim = r.get("similarity", 0)
        src = r.get("source", "?")
        eid = (r.get("experience_id") or "")[:8]
        intent = r.get("intent") or r.get("query") or "(no intent)"
        print(f"{i:2}. [{src:8}] {eid}  sim={sim:.2f}  {intent[:80]}")
        steps = r.get("steps") or []
        for s in steps[:2]:
            print(f"      • {s[:90]}")
        if len(steps) > 2:
            print(f"      • ...({len(steps)-2} more steps)")
    quota = res.get("quota") or {}
    if quota.get("community_locked_hint"):
        print(f"\n  ℹ {quota.get('hint','')}")
    return 0


def cmd_rag_context(args: argparse.Namespace) -> int:
    """POST /v1/rag/context — server-side chunk RAG + ACL + context packing."""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp register` or bind first.")
    body = {"q": args.q, "top_k": args.top_k, "scope": args.scope}
    if args.task_type:
        body["task_type"] = args.task_type
    if args.project:
        body["project"] = args.project
    res = http_request(args.base, "POST", "/v1/rag/context", body=body, cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    context = res.get("context") or ""
    if context:
        print(context)
    else:
        print("(no rag context)")
    return 0


def _split_arg_values(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _last_recall_paths() -> list[Path]:
    paths: list[Path] = []
    if os.environ.get("EXPOOL_RUNTIME_DIR"):
        paths.append(Path(os.environ["EXPOOL_RUNTIME_DIR"]) / "last-recall.json")
    if os.environ.get("EXP_CRED_DIR"):
        paths.append(Path(os.environ["EXP_CRED_DIR"]) / "runtime" / "last-recall.json")
    paths.append(Path.home() / ".config" / "expool" / "runtime" / "last-recall.json")
    paths.append(DEFAULT_CRED_DIR / "runtime" / "last-recall.json")
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key not in seen:
            deduped.append(path.expanduser())
            seen.add(key)
    return deduped


def _load_last_recall() -> dict[str, Any]:
    for path in _last_recall_paths():
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def cmd_reuse_feedback(args: argparse.Namespace) -> int:
    """POST /v1/reuse/feedback — mark recalled RAG chunks helpful/harmful."""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp register` or bind first.")

    event_id = args.event_id.strip()
    last = _load_last_recall() if (args.last or not event_id) else {}
    if not event_id:
        event_id = str(last.get("event_id") or "").strip()
    if not event_id:
        raise SystemExit("missing --event-id (or use --last after an automatic recall)")

    chunk_ids = _split_arg_values(args.chunk_id)
    exp_ids = _split_arg_values(args.experience_id)
    if last and not chunk_ids and not exp_ids:
        chunk_ids = [
            str(c.get("chunk_id") or "").strip()
            for c in (last.get("chunks") or [])
            if str(c.get("chunk_id") or "").strip()
        ]
    items: list[dict[str, Any]] = []
    for chunk_id in chunk_ids:
        items.append(
            {
                "chunk_id": chunk_id,
                "reward": args.reward,
                "confidence": args.confidence,
                "used": not args.not_used,
                "reason": args.reason,
            }
        )
    for exp_id in exp_ids:
        items.append(
            {
                "experience_id": exp_id,
                "reward": args.reward,
                "confidence": args.confidence,
                "used": not args.not_used,
                "reason": args.reason,
            }
        )

    body: dict[str, Any] = {
        "event_id": event_id,
        "confidence": args.confidence,
        "used": not args.not_used,
        "reason": args.reason,
        "feedback_source": args.source,
        "final_status": args.final_status,
    }
    if items:
        body["items"] = items
    else:
        body["reward"] = args.reward

    res = http_request(args.base, "POST", "/v1/reuse/feedback", body=body, cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print(
        f"feedback stored event={str(res.get('event_id') or event_id)[:8]} "
        f"items={res.get('items_updated', 0)} experiences={res.get('experiences_updated', 0)}"
    )
    for update in (res.get("updates") or [])[:5]:
        eid = str(update.get("experience_id") or "")[:8]
        delta = (update.get("delta") or {}).get("outcome", 0.0)
        reward = update.get("reward", args.reward)
        print(f"  {eid} reward={float(reward):+.2f} delta_outcome={float(delta):+.4f}")
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    """GET /v1/projects — list project pools visible to this credential."""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp register` or bind first.")
    res = http_request(args.base, "GET", "/v1/projects", cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    projects = res.get("projects") or []
    if not projects:
        print("(no projects)")
        return 0
    for i, p in enumerate(projects, 1):
        slug = p.get("slug") or p.get("project_id")
        name = p.get("name") or slug
        relation = p.get("role") or p.get("relation") or "project"
        owners = p.get("shared_owners", "?")
        print(f"{i:2}. {slug:24} {relation:8} owners={owners}  {name}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    """GET /v1/experiences/{eid} — 拿单条经验的卡片(可选含完整 trajectory)。

    主要给 search 之后的 follow-up 用:用户挑了一条, 插件需要把完整
    steps / trajectory 显示出来。
    """
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found.")
    path = f"/v1/experiences/{args.eid}"
    if args.include_trajectory:
        path += "?include_trajectory=1"
    res = http_request(args.base, "GET", path, cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print(f"experience_id : {res.get('experience_id')}")
    print(f"task_type     : {res.get('task_type')}")
    print(f"intent        : {res.get('intent_text') or res.get('intent')}")
    print(f"acl           : {res.get('acl')}")
    print(f"created_at    : {res.get('created_at')}")
    print(f"turn_count    : {res.get('turn_count', '?')}")
    print(f"\n[query]\n{res.get('query','')}")
    print(f"\n[outcome]\n{res.get('outcome','')}")
    steps = res.get('steps') or res.get('script_steps') or []
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:
            steps = [steps]
    if steps:
        print("\n[steps]")
        for s in steps:
            print(f"  • {s}")
    if args.include_trajectory and res.get("trajectory"):
        print(f"\n[trajectory] ({len(res['trajectory'])} turns)")
        for i, t in enumerate(res["trajectory"][:20]):
            c = (t.get("content") or "")[:100].replace("\n", " ")
            print(f"  [{i:3}] {t.get('role','?'):10} {c}")
        if len(res["trajectory"]) > 20:
            print(f"  ... ({len(res['trajectory']) - 20} more turns; --json to dump all)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """POST /v1/lite/search 但用空 q 拿 personal pool 全部 — 列出本人 row。

    服务端没有 dedicated /v1/me/experiences,但 search 在空 query 时会
    返回最近的全部 personal 行(按 created_at desc)。等价于 /me 页内容。
    """
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found.")
    body = {"q": "", "top_k": args.limit, "scope": "personal"}
    res = http_request(args.base, "POST", "/v1/lite/search", body=body, cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    rows = res.get("personal") or res.get("results") or []
    if not rows:
        print("(empty pool)")
        return 0
    print(f"{'eid':10}  {'task':12}  {'turns':>5}  {'acl':8}  intent")
    print("-" * 100)
    for r in rows:
        eid = (r.get("experience_id") or "")[:8]
        task = (r.get("task_type") or "")[:12]
        turns = r.get("turn_count") or "?"
        acl = (r.get("acl") or "")[:8]
        intent = (r.get("intent") or r.get("intent_text") or r.get("query") or "")[:60]
        print(f"{eid:10}  {task:12}  {str(turns):>5}  {acl:8}  {intent}")
    return 0


def cmd_show_quota(args: argparse.Namespace) -> int:
    """alias for `quota` 但可选 --json,给插件用"""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found.")
    res = http_request(args.base, "GET", "/v1/me/quota", cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"owner          : {res.get('owner')}")
        print(f"publish_count  : {res.get('publish_count')}")
        print(f"threshold      : {res.get('threshold')}")
        print(f"unlocked       : {res.get('community_unlocked')}")
        if res.get("hint"):
            print(f"hint           : {res['hint']}")
    return 0


def cmd_skills_search(args: argparse.Namespace) -> int:
    """GET /v1/skills/search — 在已 crystallize 的 skills 库里搜。

    skill 是经验池高频经验被「结晶」出来的可复用模板。这个端点存在但
    skills 功能还没全启用,主要返回空 list — 不用慌。
    """
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found.")
    qs = urllib.parse.urlencode({"q": args.q, "top_k": args.top_k})
    res = http_request(args.base, "GET", f"/v1/skills/search?{qs}", cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    items = res.get("results") or []
    if not items:
        print("(no skills matched)")
        return 0
    for i, s in enumerate(items, 1):
        print(f"{i:2}. {s.get('name','?')}  v{s.get('version','?')}")
        print(f"      {(s.get('description') or '')[:100]}")
    return 0


def cmd_skills_install(args: argparse.Namespace) -> int:
    """GET /v1/skills/install?name=X — 拉一个 skill 的 SKILL.md/scripts。

    返回 tarball / 内容,本地由 `--target` 决定写到哪。如果 server 端没
    skills(MVP 阶段),返回 404,作为正常情况处理。
    """
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found.")
    qs = urllib.parse.urlencode({"name": args.name})
    try:
        res = http_request(args.base, "GET", f"/v1/skills/install?{qs}", cred=cred)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_opf_status(args: argparse.Namespace) -> int:
    """GET /v1/admin/opf-status — 看 OPF 后台 worker 的状态(layer1_only
    队列还有多少行待补、最近一次跑是什么时候)。运维向命令。"""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found.")
    res = http_request(args.base, "GET", "/v1/admin/opf-status", cred=cred)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_admin_dashboard(args: argparse.Namespace) -> int:
    """GET /v1/admin/dashboard — 全局指标 (push 量 / 用户数 / 各 sanitize 状态计数)。"""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found.")
    res = http_request(args.base, "GET", "/v1/admin/dashboard", cred=cred)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    """alias for `consent revoke` — 部分插件作者会 grep `revoke` 找命令,
    给个直接的入口。"""
    return cmd_consent_revoke(args)


def cmd_consent_revoke(args: argparse.Namespace) -> int:
    """Ask the server to revoke a previously uploaded experience.

    The server marks the row revoked=1, deletes the trajectory file,
    excludes it from search/clusters, and appends an audit_log entry."""
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp_uploader register` first.")
    body = {"experience_id": args.eid, "reason": args.reason or "user_request"}
    res = http_request(
        args.base, "POST", "/v1/lite/revoke", body=body, cred=cred,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") or res.get("status") == "ok" else 1


def cmd_register(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {"name": args.name, "team": args.team}
    if args.owner:
        body["owner"] = args.owner
    res = http_request(args.base, "POST", "/v1/agents/register", body)
    save_path = save_credential(res)
    res["credentials_path"] = str(save_path)
    res_safe = {**res, "secret": "***"}
    print(json.dumps(res_safe, indent=2, ensure_ascii=False))
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    cred = load_credential()
    if cred is None:
        print("(no credential found — run `exp_uploader register` first)")
        return 1
    if cred.get("api_key"):
        print(json.dumps({
            "auth_type": "api_key",
            "agent_name": cred.get("agent_name") or "(server-derived)",
            "api_key": cred["api_key"][:9] + "..." if cred.get("api_key") else "***",
        }, indent=2))
    else:
        print(json.dumps({
            "auth_type": "hmac",
            "agent_name": cred["agent_name"],
            "secret": "***",
        }, indent=2))
    return 0


def cmd_bind_api(args: argparse.Namespace) -> int:
    """Drop a portal-issued Bearer API key into place.

    This is the plugin-first auth path. The key is minted from /me/api-keys
    and sent as `Authorization: Bearer expk_...` on future requests.
    """
    api_key = (args.api_key or os.environ.get("EXP_BIND_API_KEY") or "").strip()
    if not api_key:
        print("缺少 api key：请通过 --api-key 或环境变量 EXP_BIND_API_KEY 提供",
              file=sys.stderr)
        return 2
    if not api_key.startswith("expk_"):
        print("warning: API key does not start with expk_", file=sys.stderr)

    cred_dir = Path(os.environ.get("EXP_CRED_DIR",
                                   str(Path.home() / ".experience-pool" / "credentials")))
    cred_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cred_dir, 0o700)
    except OSError:
        pass

    cred = {
        "auth_type": "api_key",
        "agent_name": args.agent_name.strip(),
        "api_key": api_key,
    }
    cred_path = cred_dir / (f"{cred['agent_name']}.json" if cred["agent_name"] else "api-key.json")
    cred_path.write_text(json.dumps(cred, indent=2))
    try:
        os.chmod(cred_path, 0o600)
    except OSError:
        pass

    server_ok = False
    verify_error = ""
    if not args.no_verify:
        try:
            res = http_request(args.base, "GET", "/v1/me/quota", None, cred=cred)
            server_ok = True
            if not cred["agent_name"]:
                owner = str(res.get("owner") or "").strip()
                if owner:
                    cred["agent_name"] = owner
                    cred_path.write_text(json.dumps(cred, indent=2))
        except SystemExit as exc:
            verify_error = str(exc)
        except Exception as exc:
            verify_error = str(exc)

    out = {
        "status": "bound",
        "auth_type": "api_key",
        "agent_name": cred.get("agent_name") or "",
        "credential_path": str(cred_path),
        "server_reachable": server_ok,
        "base": args.base,
    }
    if verify_error:
        out["verify_error"] = verify_error
    print(json.dumps(out, indent=2))
    return 0 if server_ok or args.no_verify else 1


def cmd_pair(args: argparse.Namespace) -> int:
    """Exchange a short-lived portal pairing code for a local API key."""
    code = (args.code or os.environ.get("EXP_PAIR_CODE") or "").strip()
    if not code:
        print("缺少配对码：请通过 --code 或环境变量 EXP_PAIR_CODE 提供",
              file=sys.stderr)
        return 2
    if not code.startswith("expair_"):
        print("warning: pairing code does not start with expair_", file=sys.stderr)

    try:
        res = http_request(
            args.base,
            "POST",
            "/v1/plugin/pair",
            {
                "code": code,
                "agent_name": args.agent_name.strip(),
            },
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    api_key = str(res.get("api_key") or "").strip()
    if not api_key:
        print("gateway did not return an api key", file=sys.stderr)
        return 1

    agent_name = str(res.get("agent_name") or args.agent_name or "").strip()
    cred_dir = Path(os.environ.get("EXP_CRED_DIR",
                                   str(Path.home() / ".experience-pool" / "credentials")))
    cred_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cred_dir, 0o700)
    except OSError:
        pass

    cred = {
        "auth_type": "api_key",
        "agent_name": agent_name,
        "api_key": api_key,
        "key_id": res.get("key_id", ""),
    }
    cred_path = cred_dir / (f"{agent_name}.json" if agent_name else "api-key.json")
    cred_path.write_text(json.dumps(cred, indent=2))
    try:
        os.chmod(cred_path, 0o600)
    except OSError:
        pass

    server_ok = False
    verify_error = ""
    if not args.no_verify:
        try:
            quota = http_request(args.base, "GET", "/v1/me/quota", None, cred=cred)
            server_ok = True
            owner = str(quota.get("owner") or "").strip()
            if owner and owner != agent_name:
                cred["agent_name"] = owner
                new_path = cred_dir / f"{owner}.json"
                new_path.write_text(json.dumps(cred, indent=2))
                try:
                    os.chmod(new_path, 0o600)
                except OSError:
                    pass
                if new_path != cred_path:
                    try:
                        cred_path.unlink()
                    except OSError:
                        pass
                    cred_path = new_path
                agent_name = owner
        except SystemExit as exc:
            verify_error = str(exc)
        except Exception as exc:
            verify_error = str(exc)

    out = {
        "status": "paired",
        "auth_type": "api_key",
        "agent_name": agent_name,
        "credential_path": str(cred_path),
        "server_reachable": server_ok,
        "base": args.base,
    }
    if verify_error:
        out["verify_error"] = verify_error
    print(json.dumps(out, indent=2))
    return 0 if server_ok or args.no_verify else 1


def cmd_bind(args: argparse.Namespace) -> int:
    """Drop a portal-issued credential into place without re-running install.

    Use case: user already has experience-pool installed, then registers (or
    rotates) at the web portal and gets a bind script. They can either:
      1. Run the curl one-liner (re-runs install.sh — heavier).
      2. Run `exp bind --name X --secret Y --team Z` (this command).

    Both end up at the same place: ~/.experience-pool/credentials/X.json
    written with the supplied secret, and ~/.claude/settings.json env block
    updated to lock the agent identity.
    """
    name = args.name.strip()
    secret = (args.secret or os.environ.get("EXP_BIND_SECRET") or "").strip()
    if not name:
        print("缺少 name：请通过 --name 提供", file=sys.stderr)
        return 2
    if not secret:
        print("缺少 secret：请通过 --secret 或环境变量 EXP_BIND_SECRET 提供",
              file=sys.stderr)
        return 2

    cred_dir = Path(os.environ.get("EXP_CRED_DIR",
                                   str(Path.home() / ".experience-pool" / "credentials")))
    cred_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cred_dir, 0o700)
    except OSError:
        pass

    import uuid as _uuid_mod
    agent_id = args.agent_id or str(_uuid_mod.uuid4())
    team = args.team or "default"
    cred = {"agent_id": agent_id, "agent_name": name, "team": team, "secret": secret}
    cred_path = cred_dir / f"{name}.json"
    cred_path.write_text(json.dumps(cred, indent=2))
    try:
        os.chmod(cred_path, 0o600)
    except OSError:
        pass

    # Patch ~/.claude/settings.json env to lock identity for future sessions.
    patched_settings = False
    if not args.skip_claude_settings:
        settings_path = Path.home() / ".claude" / "settings.json"
        try:
            if settings_path.exists():
                data = json.loads(settings_path.read_text())
            else:
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                data = {}
            env = data.setdefault("env", {})
            env["EXP_AGENT_NAME"] = name
            settings_path.write_text(json.dumps(data, indent=2))
            patched_settings = True
        except Exception as exc:
            print(f"(warning) couldn't patch {settings_path}: {exc}", file=sys.stderr)

    # Smoke-test: hit /v1/users/me-style or /healthz with HMAC to confirm
    # secret is correct. We use whoami-equivalent: just ensure server is
    # reachable; any signed request is sufficient.
    server_ok = False
    if not args.no_verify:
        os.environ["EXP_AGENT_NAME"] = name  # ensure load_credential() picks it up
        try:
            http_request(args.base, "GET", "/healthz", None)
            server_ok = True
        except Exception:
            server_ok = False

    out = {
        "status": "bound",
        "agent_name": name,
        "agent_id": agent_id,
        "team": team,
        "credential_path": str(cred_path),
        "claude_settings_patched": patched_settings,
        "server_reachable": server_ok,
        "base": args.base,
    }
    print(json.dumps(out, indent=2))
    return 0 if server_ok or args.no_verify else 1


def cmd_list_sessions(args: argparse.Namespace) -> int:
    src = detect_source(args.source)
    adapter = ADAPTERS[src]
    all_rows = adapter.list_sessions(limit=100_000)
    total = len(all_rows)
    rows = all_rows[: max(0, args.limit)]
    if getattr(args, "with_model", False):
        enriched: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if not item.get("model"):
                ident = item.get("path") or item.get("id")
                if ident:
                    try:
                        session = _adapter_parse(src, str(ident))
                        item["model"] = session.model
                        item["agent_type"] = session.agent_type
                    except Exception:
                        item["model"] = item.get("model") or "unknown"
            enriched.append(item)
        rows = enriched
    print(json.dumps(
        {"source": src, "total": total, "sessions": rows},
        indent=2,
        ensure_ascii=False,
    ))
    return 0


def _maybe_attach_raw(session: Session, source_path: str | Path) -> None:
    """Embed base64 of the raw source file under session.extra.raw_b64
    so the server can preserve byte-exact recovery (capped at 8 MiB)."""
    p = Path(source_path) if source_path else None
    if not p or not p.is_file():
        return
    size = p.stat().st_size
    cap = int(os.environ.get("EXP_RAW_CAP_BYTES", str(8 * 1024 * 1024)))
    if size > cap:
        session.extra["raw_truncated"] = True
        session.extra["raw_size_bytes"] = size
        return
    session.extra["raw_b64"] = base64.b64encode(p.read_bytes()).decode("ascii")
    session.extra["raw_size_bytes"] = size
    session.extra["raw_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()


def _maybe_annotate(session: Session, args: argparse.Namespace) -> dict[str, Any] | None:
    """Optionally annotate per-turn rewards using the synergy schema."""
    if not getattr(args, "annotate", False):
        return None
    try:
        here = Path(__file__).parent
        sys.path.insert(0, str(here))
        from exp_annotator import annotate_session, pick_backend  # type: ignore
    except ImportError as e:
        print(f"[annotate] exp_annotator.py not found alongside uploader: {e}", file=sys.stderr)
        return None
    try:
        backend = pick_backend(getattr(args, "annotate_backend", "auto"),
                               getattr(args, "annotate_model", None))
    except SystemExit as e:
        print(f"[annotate] no backend available: {e}", file=sys.stderr)
        return None
    payload = session.to_payload()
    return annotate_session(
        payload, backend,
        subsequent_k=getattr(args, "annotate_subsequent_k", 4),
        max_turns=getattr(args, "annotate_max_turns", 8),
        strategy=getattr(args, "annotate_pick", "even"),
        verbose=getattr(args, "verbose", False),
    )


def _post_rewards(base: str, cred: dict[str, str], experience_id: str,
                  rewards: dict[str, Any]) -> None:
    """POST per-turn rewards to /v1/lite/rewards (separate from the trace push)."""
    body: dict[str, Any] = {
        "experience_id": experience_id,
        "rewards": [
            {
                "turn_index": r["turn_index"],
                "user_turn_index": r.get("user_turn_index"),
                "outcome": r["outcome"],
                "intent": r["intent"],
                "execution": r["execution"],
                "orchestration": r["orchestration"],
                "expression": r["expression"],
                "confidence": r["confidence"],
                "reason": r.get("reason", ""),
            }
            for r in rewards.get("rewards", [])
        ],
        "summary": rewards.get("summary", {}),
        "judge_model": rewards.get("model_used", "unknown"),
        "judge_backend": rewards.get("backend", "unknown"),
        "annotated_at": rewards.get("annotated_at", ""),
        "replace": True,
    }
    if not body["rewards"]:
        return
    res = http_request(base, "POST", "/v1/lite/rewards", body, cred=cred)
    print(json.dumps({"rewards_posted": res.get("rewards_stored"),
                      "judge_model": res.get("judge_model"),
                      "experience_id": res.get("experience_id")}, ensure_ascii=False),
          file=sys.stderr)


def _push(session: Session, args: argparse.Namespace) -> int:
    setattr(args, "_push_outcome", "pending")
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp_uploader register` first.")

    # ---- Consent gate ------------------------------------------------
    # Every actual transmission runs through this. Returns False → skip
    # upload (optionally save to ~/.experience-pool/pending/ for later).
    dry_run = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "yes", False))
    allow, reason, decision = _consent_check(
        agent=session.agent_type,
        cwd=session.cwd or "",
        session_id=session.session_id,
        force=force,
        dry_run=dry_run,
    )
    if not allow:
        # Save to pending/ so the user can review + push later.
        save_pending = (
            exp_consent is not None
            and exp_consent.load_consent().get("save_pending_on_skip", True)
        )
        pending_path = ""
        if save_pending and not dry_run:
            try:
                snapshot = {
                    "agent_type": session.agent_type,
                    "session_id": session.session_id,
                    "cwd": session.cwd,
                    "started_at": session.started_at,
                    "ended_at": session.ended_at,
                    "skipped_reason": reason,
                    "trajectory_preview_len": sum(len(t.content or "") for t in session.trajectory),
                    "turn_count": len(session.trajectory),
                }
                pending_path = str(exp_consent.save_pending(
                    snapshot, session_id=session.session_id
                ))
            except Exception as exc:
                print(f"[exp] pending-save failed: {exc}", file=sys.stderr)
        print(json.dumps({
            "session": session.session_id,
            "agent_type": session.agent_type,
            "skipped": True,
            "reason": reason,
            "pending_saved_to": pending_path,
        }, ensure_ascii=False))
        setattr(args, "_push_outcome", "skipped")
        return 0
    # ------------------------------------------------------------------

    if getattr(args, "full_trace", False):
        src = session.extra.get("source_path") or session.extra.get("db") or ""
        if src:
            _maybe_attach_raw(session, src)
    rewards = _maybe_annotate(session, args)
    if rewards is not None:
        session.extra["rewards"] = rewards
    parts = build_lite_card(
        session,
        task_type=args.task,
        sensitivity=args.sensitivity,
        acl=args.acl,
        tags=args.tag or [],
    )
    body: dict[str, Any] = {
        **parts["card"],
        "trajectory": None if args.no_trace else parts["trajectory"],
        "system": session.extra.get("system"),
        "tools": session.extra.get("tools"),
        "meta": {
            "agent_type": session.agent_type,
            "session_id": session.session_id,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "cwd": session.cwd,
            "agent_version": session.agent_version,
            "extra": session.extra,
            "uploader_version": "0.2",
        },
    }
    if body["trajectory"] is None:
        body.pop("trajectory")
    if body["system"] is None:
        body.pop("system")
    if body["tools"] is None:
        body.pop("tools")
    res = http_request(args.base, "POST", "/v1/lite/push", body, cred=cred)
    setattr(args, "_push_outcome", "uploaded")
    res_min = {
        "experience_id": res.get("experience_id"),
        "review_status": res.get("review_status"),
        "sanitization_status": res.get("sanitization_status"),
        "redactions": res.get("redactions"),
        "source_model": session.model,
    }
    print(json.dumps({"session": session.session_id, "agent_type": session.agent_type, **res_min},
                     ensure_ascii=False))
    # If rewards were just computed, also POST them to /v1/lite/rewards so they
    # land in turn_rewards (not just in meta.extra). This keeps them
    # query-able and re-attachable later.
    if rewards is not None and res.get("experience_id"):
        try:
            _post_rewards(args.base, cred, res["experience_id"], rewards)
        except SystemExit as e:
            print(f"[rewards] post failed: {e}", file=sys.stderr)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    src = detect_source(args.source)
    session = _adapter_parse(src, args.session)
    return _push(session, args)


def cmd_push_latest(args: argparse.Namespace) -> int:
    src = detect_source(args.source)
    ident = _adapter_latest_path_or_id(src)
    if src == CodexAdapter.name:
        tasks, _ = CodexAdapter.parse_tasks(ident, include_incomplete=True)
        if not tasks:
            raise SystemExit("latest codex rollout has no task trajectory")
        session = tasks[-1]
    else:
        session = _adapter_parse(src, ident)
    return _push(session, args)


def cmd_push_file(args: argparse.Namespace) -> int:
    session = GenericAdapter.parse(args.file)
    return _push(session, args)


def cmd_annotate_existing(args: argparse.Namespace) -> int:
    """Re-annotate an already-uploaded trace by running the local annotator
    against the local source session, then POSTing rewards to /v1/lite/rewards.

    Useful when you want to add (or replace) rewards on an experience that was
    pushed earlier without --annotate, or to compare two judge models on the
    same trace.
    """
    cred = load_credential()
    if cred is None:
        raise SystemExit("no credential found. run `exp_uploader register` first.")
    src = detect_source(args.source)
    session = _adapter_parse(src, args.session)
    # Force annotation on
    args.annotate = True
    rewards = _maybe_annotate(session, args)
    if rewards is None:
        raise SystemExit("annotation failed (no backend available)")
    _post_rewards(args.base, cred, args.experience_id, rewards)
    print(json.dumps({"experience_id": args.experience_id,
                      "n_rewards": len(rewards.get("rewards", [])),
                      "judge_model": rewards.get("model_used")},
                     ensure_ascii=False))
    return 0


def cmd_get_rewards(args: argparse.Namespace) -> int:
    cred = load_credential()
    path = f"/v1/lite/rewards/{args.experience_id}"
    if args.judge_model:
        path += f"?judge_model={urllib.parse.quote(args.judge_model)}"
    res = http_request(args.base, "GET", path, body=None, cred=cred)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Background auto-sync daemon (one-shot tick; scheduled by launchd/systemd).
# State at $EXP_INSTALL_DIR/state.json:
#   {"by_source": {"claude-code": {"uploaded_ids": [...], "last_seen_mtime": "2026..."}}}
# ---------------------------------------------------------------------------

DEFAULT_AUTO_SOURCES = ["claude-code", "hermes", "continue-dev", "codex"]


def _state_path() -> Path:
    p = Path(os.environ.get("EXP_STATE_PATH",
                            str(Path.home() / ".experience-pool" / "state.json")))
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def _load_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"version": 1, "by_source": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "by_source": {}}


def _save_state(state: dict[str, Any]) -> None:
    p = _state_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    p.chmod(0o600)


def _daemon_progress_line(source: str, current: int, total: int, sid: str, action: str) -> str:
    width = 18
    total = max(1, total)
    current = min(max(0, current), total)
    filled = int(width * current / total)
    bar = "#" * filled + "." * (width - filled)
    pct = int(100 * current / total)
    return f"[daemon] {source} [{bar}] {current}/{total} {pct:3d}% {sid[:24]}: {action}"


def _daemon_sync_codex(
    rows: list[dict[str, Any]],
    bookkeeping: dict[str, Any],
    args: argparse.Namespace,
    push_ns: argparse.Namespace,
    *,
    cap_per_source: int,
    cap_per_session_kb: int,
) -> tuple[int, int, int]:
    """Incrementally upload completed tasks from append-only Codex rollouts."""
    file_state = bookkeeping.setdefault("file_offsets", {})
    remembered = list(bookkeeping.get("uploaded_ids", []))
    remembered_set = set(remembered)
    uploaded = skipped = failed = 0

    for row in rows:
        if uploaded >= cap_per_source:
            break
        sid = str(row.get("id") or row.get("path") or "")
        ident = str(row.get("path") or row.get("id") or "")
        size_bytes = int(row.get("size_bytes") or 0)
        current = file_state.get(sid) if isinstance(file_state.get(sid), dict) else {}
        offset = int(current.get("offset") or 0)
        if size_bytes < offset:
            # File was rotated/truncated. Re-scan from the beginning; stable
            # task ids plus server dedup keep this safe.
            offset = 0
        if size_bytes and size_bytes <= offset:
            skipped += 1
            continue

        remaining = max(1, cap_per_source - uploaded)
        try:
            sessions, parsed_offset = CodexAdapter.parse_tasks(
                ident,
                start_offset=offset,
                include_incomplete=False,
                max_tasks=remaining,
            )
        except Exception as exc:
            failed += 1
            if args.verbose:
                print(f"[daemon] codex/{sid[:24]} parse failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        if not sessions:
            # Advance over complete non-task metadata, but never past an open
            # task (parse_tasks deliberately returns its start offset).
            if parsed_offset > offset:
                current["offset"] = parsed_offset
            current["size_bytes"] = size_bytes
            current["mtime"] = row.get("mtime") or ""
            file_state[sid] = current
            skipped += 1
            continue

        committed_offset = offset
        for session in sessions:
            task_id = session.session_id
            if task_id in remembered_set:
                committed_offset = max(committed_offset, int(session.extra.get("byte_end") or 0))
                skipped += 1
                continue
            if not _session_has_retrievable_task(session):
                committed_offset = max(committed_offset, int(session.extra.get("byte_end") or 0))
                remembered.append(task_id)
                remembered_set.add(task_id)
                skipped += 1
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            "codex", uploaded + skipped, len(sessions), task_id,
                            "skipped runtime wrapper or trivial task",
                        ),
                        file=sys.stderr,
                    )
                continue
            stored_kb = int(session.extra.get("stored_chars") or 0) / 1024
            if stored_kb > cap_per_session_kb:
                # The parser already compacts runtime output. If a task still
                # exceeds the configured cap, leave the offset in place so an
                # operator can retry with a larger limit rather than losing it.
                skipped += 1
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            "codex", uploaded + skipped, len(sessions), task_id,
                            f"task payload {stored_kb:.0f}KB > cap {cap_per_session_kb}KB",
                        ),
                        file=sys.stderr,
                    )
                break
            try:
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            "codex", uploaded + 1, len(sessions), task_id,
                            f"uploading task ({len(session.trajectory)} turns)",
                        ),
                        file=sys.stderr,
                    )
                if args.dry_run:
                    print(f"[dry-run] would upload codex/{task_id} ({len(session.trajectory)} turns)")
                    outcome = "uploaded"
                else:
                    _push(session, push_ns)
                    outcome = str(getattr(push_ns, "_push_outcome", "uploaded"))
                if outcome == "uploaded":
                    uploaded += 1
                else:
                    skipped += 1
                committed_offset = max(committed_offset, int(session.extra.get("byte_end") or 0))
                remembered.append(task_id)
                remembered_set.add(task_id)
            except (SystemExit, Exception) as exc:
                failed += 1
                if args.verbose:
                    print(f"[daemon] codex/{task_id[:24]} failed: {exc}", file=sys.stderr)
                break

        current["offset"] = committed_offset
        current["size_bytes"] = size_bytes
        current["mtime"] = row.get("mtime") or ""
        file_state[sid] = current

    bookkeeping["uploaded_ids"] = remembered[-5000:]
    bookkeeping["file_offsets"] = file_state
    mtimes = [str(row.get("mtime") or "") for row in rows if row.get("mtime")]
    if mtimes:
        bookkeeping["last_mtime"] = max(mtimes)
    return uploaded, skipped, failed


def _daemon_sync_claude(
    rows: list[dict[str, Any]],
    bookkeeping: dict[str, Any],
    args: argparse.Namespace,
    push_ns: argparse.Namespace,
    *,
    cap_per_source: int,
    cap_per_session_kb: int,
) -> tuple[int, int, int]:
    """Upload Claude sessions as stable child segments, including appends."""
    file_state = bookkeeping.setdefault("file_state", {})
    segment_turn_counts = bookkeeping.setdefault("segment_turn_counts", {})
    remembered = list(bookkeeping.get("uploaded_ids", []))
    remembered_set = set(remembered)
    uploaded = skipped = failed = 0

    def remember(session_id: str, turn_count: int) -> None:
        if session_id not in remembered_set:
            remembered.append(session_id)
            remembered_set.add(session_id)
        segment_turn_counts[session_id] = turn_count

    for row in rows:
        if uploaded >= cap_per_source:
            break
        sid = str(row.get("id") or row.get("path") or "")
        ident = str(row.get("path") or row.get("id") or "")
        mtime = str(row.get("mtime") or "")
        size_bytes = int(row.get("size_bytes") or 0)
        current = file_state.get(sid) if isinstance(file_state.get(sid), dict) else {}
        if (
            mtime
            and str(current.get("mtime") or "") >= mtime
            and int(current.get("size_bytes") or 0) == size_bytes
        ):
            skipped += 1
            continue
        try:
            sessions = ClaudeCodeAdapter.parse_tasks(
                ident,
                max_turns=max(40, int(os.environ.get("EXP_CLAUDE_SEGMENT_TURNS", "240"))),
                max_chars=max(64, cap_per_session_kb) * 1024,
            )
        except Exception as exc:
            failed += 1
            if args.verbose:
                print(
                    f"[daemon] claude-code/{sid[:24]} parse failed: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
            continue
        completed_file = True
        for session in sessions:
            task_id = session.session_id
            previous_turns = int(segment_turn_counts.get(task_id) or 0)
            current_turns = len(session.trajectory)
            if task_id in remembered_set and current_turns <= previous_turns:
                skipped += 1
                continue
            if uploaded >= cap_per_source:
                completed_file = False
                break
            if not _session_has_retrievable_task(session):
                remember(task_id, current_turns)
                skipped += 1
                continue
            stored_kb = sum(len(turn.content or "") for turn in session.trajectory) / 1024
            if stored_kb > cap_per_session_kb * 1.05:
                completed_file = False
                skipped += 1
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            "claude-code",
                            uploaded + skipped,
                            len(sessions),
                            task_id,
                            f"segment payload {stored_kb:.0f}KB > cap {cap_per_session_kb}KB",
                        ),
                        file=sys.stderr,
                    )
                break
            try:
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            "claude-code",
                            uploaded + 1,
                            len(sessions),
                            task_id,
                            f"uploading segment ({current_turns} turns)",
                        ),
                        file=sys.stderr,
                    )
                if args.dry_run:
                    print(
                        f"[dry-run] would upload claude-code/{task_id} "
                        f"({current_turns} turns)"
                    )
                    outcome = "uploaded"
                else:
                    _push(session, push_ns)
                    outcome = str(getattr(push_ns, "_push_outcome", "uploaded"))
                if outcome == "uploaded":
                    uploaded += 1
                else:
                    skipped += 1
                remember(task_id, current_turns)
            except (SystemExit, Exception) as exc:
                failed += 1
                completed_file = False
                if args.verbose:
                    print(
                        f"[daemon] claude-code/{task_id[:24]} failed: {exc}",
                        file=sys.stderr,
                    )
                break
        if completed_file:
            file_state[sid] = {"mtime": mtime, "size_bytes": size_bytes}

    bookkeeping["uploaded_ids"] = remembered[-5000:]
    bookkeeping["file_state"] = file_state
    bookkeeping["segment_turn_counts"] = {
        key: segment_turn_counts[key]
        for key in remembered[-5000:]
        if key in segment_turn_counts
    }
    mtimes = [str(row.get("mtime") or "") for row in rows if row.get("mtime")]
    if mtimes:
        bookkeeping["last_mtime"] = max(mtimes)
    return uploaded, skipped, failed


def cmd_daemon_tick(args: argparse.Namespace) -> int:
    """One-shot incremental sync. Designed to be run by launchd / systemd
    every few minutes. Idempotent: tracks uploaded session ids per source
    and an mtime watermark so the same session is never uploaded twice.
    """
    cred = load_credential()
    if cred is None:
        print(json.dumps({"status": "no_credential", "uploaded": 0}))
        return 0
    enabled = (args.sources or os.environ.get("EXP_AUTO_SOURCES")
               or ",".join(DEFAULT_AUTO_SOURCES)).split(",")
    enabled = [s.strip() for s in enabled if s.strip() and s.strip() in ADAPTERS]
    state = _load_state()
    by_source = state.setdefault("by_source", {})
    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    summary: dict[str, Any] = {
        "started_at": started_at,
        "sources": {},
        "total_uploaded": 0,
        "total_skipped": 0,
        "total_failed": 0,
    }
    cap_per_source = max(1, args.max_per_source)
    cap_per_session_kb = max(64, args.max_session_kb)
    default_acl = args.acl or os.environ.get("EXP_AUTO_ACL", "private")
    default_task = args.task or os.environ.get("EXP_AUTO_TASK", "auto-sync")

    # Synthesize an args namespace for _push to consume.
    push_ns = argparse.Namespace(
        base=args.base,
        task=default_task,
        sensitivity="medium",
        acl=default_acl,
        tag=["auto-sync"],
        no_trace=False,
        full_trace=False,
        annotate=False,
        verbose=args.verbose,
    )
    for src in enabled:
        adapter = ADAPTERS[src]
        if not adapter.available():
            summary["sources"][src] = {"status": "unavailable"}
            continue
        bookkeeping = by_source.setdefault(src, {"uploaded_ids": [], "last_mtime": ""})
        already = set(bookkeeping.get("uploaded_ids", []))
        try:
            rows = adapter.list_sessions(limit=200)
        except Exception as e:
            summary["sources"][src] = {"status": "list_failed", "error": str(e)}
            summary["total_failed"] += 1
            continue
        # Sort oldest-first so we upload chronologically.
        rows.sort(key=lambda r: (r.get("mtime") or r.get("ended_at") or ""))
        if src == ClaudeCodeAdapter.name:
            uploaded, skipped, failed = _daemon_sync_claude(
                rows,
                bookkeeping,
                args,
                push_ns,
                cap_per_source=cap_per_source,
                cap_per_session_kb=cap_per_session_kb,
            )
            bookkeeping["last_tick_uploaded"] = uploaded
            bookkeeping["last_tick_at"] = started_at
            summary["sources"][src] = {
                "uploaded": uploaded,
                "skipped": skipped,
                "failed": failed,
                "available_now": len(rows),
                "tracked_files": len(bookkeeping.get("file_state", {})),
            }
            summary["total_uploaded"] += uploaded
            summary["total_skipped"] += skipped
            summary["total_failed"] += failed
            continue
        if src == CodexAdapter.name:
            uploaded, skipped, failed = _daemon_sync_codex(
                rows,
                bookkeeping,
                args,
                push_ns,
                cap_per_source=cap_per_source,
                cap_per_session_kb=cap_per_session_kb,
            )
            bookkeeping["last_tick_uploaded"] = uploaded
            bookkeeping["last_tick_at"] = started_at
            summary["sources"][src] = {
                "uploaded": uploaded,
                "skipped": skipped,
                "failed": failed,
                "available_now": len(rows),
                "tracked_files": len(bookkeeping.get("file_offsets", {})),
            }
            summary["total_uploaded"] += uploaded
            summary["total_skipped"] += skipped
            summary["total_failed"] += failed
            continue
        remaining_rows = [
            r for r in rows
            if str(r.get("id") or r.get("path") or "") not in already
        ]
        if args.verbose:
            print(
                f"[daemon] {src}: scanning {len(rows)} session(s), "
                f"{len(remaining_rows)} new candidate(s), upload cap={cap_per_source}",
                file=sys.stderr,
            )
        uploaded, skipped, failed = 0, 0, 0
        candidate_seen = 0
        candidate_total = max(1, len(remaining_rows))
        new_ids: list[str] = []
        last_mtime_seen = bookkeeping.get("last_mtime", "")
        for row in rows:
            sid = str(row.get("id") or row.get("path") or "")
            mtime = row.get("mtime") or row.get("ended_at") or ""
            if sid in already:
                skipped += 1
                continue
            candidate_seen += 1
            size_kb = (row.get("size_bytes") or 0) / 1024
            if size_kb and size_kb > cap_per_session_kb:
                skipped += 1
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            src,
                            candidate_seen,
                            candidate_total,
                            sid,
                            f"skipped size {size_kb:.0f}KB > cap {cap_per_session_kb}KB",
                        ),
                        file=sys.stderr,
                    )
                continue
            if uploaded >= cap_per_source:
                if args.verbose:
                    print(f"[daemon] {src}: upload cap reached ({cap_per_source}); remaining candidates stay queued",
                          file=sys.stderr)
                break
            ident = row.get("path") or row.get("id")
            try:
                session = _adapter_parse(src, ident)
                if not session.trajectory:
                    skipped += 1
                    if args.verbose:
                        print(
                            _daemon_progress_line(
                                src, candidate_seen, candidate_total, sid,
                                "skipped empty trajectory",
                            ),
                            file=sys.stderr,
                        )
                    continue
                if not _session_has_retrievable_task(session):
                    skipped += 1
                    if args.verbose:
                        print(
                            _daemon_progress_line(
                                src, candidate_seen, candidate_total, sid,
                                "skipped runtime wrapper or trivial task",
                            ),
                            file=sys.stderr,
                        )
                    continue
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            src,
                            candidate_seen,
                            candidate_total,
                            sid,
                            f"uploading {len(session.trajectory)} turn(s)",
                        ),
                        file=sys.stderr,
                    )
                if args.dry_run:
                    print(f"[dry-run] would upload {src}/{sid} ({len(session.trajectory)} turns)")
                else:
                    _push(session, push_ns)
                uploaded += 1
                new_ids.append(sid)
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            src, candidate_seen, candidate_total, sid, "done",
                        ),
                        file=sys.stderr,
                    )
                if mtime > last_mtime_seen:
                    last_mtime_seen = mtime
            except SystemExit as e:
                failed += 1
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            src, candidate_seen, candidate_total, sid, f"failed {e}",
                        ),
                        file=sys.stderr,
                    )
            except Exception as e:
                failed += 1
                if args.verbose:
                    print(
                        _daemon_progress_line(
                            src,
                            candidate_seen,
                            candidate_total,
                            sid,
                            f"failed {type(e).__name__}: {e}",
                        ),
                        file=sys.stderr,
                    )
        # Persist bookkeeping (cap remembered ids at 5000 to keep state small).
        all_ids = list(already) + new_ids
        if len(all_ids) > 5000:
            all_ids = all_ids[-5000:]
        bookkeeping["uploaded_ids"] = all_ids
        bookkeeping["last_mtime"] = last_mtime_seen
        bookkeeping["last_tick_uploaded"] = uploaded
        bookkeeping["last_tick_at"] = started_at
        summary["sources"][src] = {
            "uploaded": uploaded, "skipped": skipped, "failed": failed,
            "available_now": len(rows),
        }
        summary["total_uploaded"] += uploaded
        summary["total_skipped"] += skipped
        summary["total_failed"] += failed
        if args.verbose:
            print(
                f"[daemon] {src}: uploaded={uploaded} skipped={skipped} failed={failed}",
                file=sys.stderr,
            )
    state["last_tick_at"] = started_at
    if not args.dry_run:
        _save_state(state)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def cmd_daemon_state(args: argparse.Namespace) -> int:
    state = _load_state()
    # Don't dump full uploaded_ids list — only counts.
    out = {"last_tick_at": state.get("last_tick_at"), "by_source": {}}
    for src, info in state.get("by_source", {}).items():
        out["by_source"][src] = {
            "uploaded_count": len(info.get("uploaded_ids", [])),
            "tracked_files": max(
                len(info.get("file_offsets", {})),
                len(info.get("file_state", {})),
            ),
            "last_mtime": info.get("last_mtime", ""),
            "last_tick_uploaded": info.get("last_tick_uploaded", 0),
            "last_tick_at": info.get("last_tick_at", ""),
        }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_daemon_reset(args: argparse.Namespace) -> int:
    """Forget what we've uploaded so the next tick will re-scan from scratch."""
    state = _load_state()
    if args.source:
        state.get("by_source", {}).pop(args.source, None)
    else:
        state["by_source"] = {}
    _save_state(state)
    print(json.dumps({"reset": args.source or "all"}, ensure_ascii=False))
    return 0


def cmd_push_all(args: argparse.Namespace) -> int:
    src = detect_source(args.source)
    adapter = ADAPTERS[src]
    rows = adapter.list_sessions(limit=args.limit)
    if not rows:
        print(json.dumps({"source": src, "uploaded": 0, "reason": "no sessions found"}))
        return 1
    since_ts = args.since
    uploaded = 0
    failed: list[dict[str, Any]] = []
    for row in rows:
        if since_ts:
            row_ts = row.get("mtime") or row.get("ended_at") or ""
            if row_ts and row_ts < since_ts:
                continue
        ident = row.get("path") or row.get("id")
        try:
            session = _adapter_parse(src, ident)
            _push(session, args)
            uploaded += 1
        except SystemExit as e:
            failed.append({"id": row.get("id"), "error": str(e)})
        except Exception as e:
            failed.append({"id": row.get("id"), "error": f"{type(e).__name__}: {e}"})
    print(json.dumps({"source": src, "uploaded": uploaded, "failed": failed}, ensure_ascii=False))
    return 0 if not failed else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="exp_uploader", description=__doc__.split("\n")[1])
    p.add_argument("--base", default=DEFAULT_BASE_URL, help=f"gateway URL (default {DEFAULT_BASE_URL})")
    p.add_argument("--version", action="version", version=f"exp_uploader {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("register", help="注册新代理并保存 HMAC 凭据到本地（首次安装专用；常规绑定请用 pair / bind-api）")
    sp.add_argument("--name", required=True)
    sp.add_argument("--team", required=True)
    sp.add_argument("--owner", default="",
                    help="stable handle (e.g. github username, email) that "
                         "groups multiple agents into one personal pool. "
                         "Defaults to the agent name on first register.")
    sp.set_defaults(func=cmd_register)

    sp = sub.add_parser("whoami")
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("bind",
                        help="用门户颁发的 agent_name + HMAC secret 绑定本机（无需重跑 install.sh）")
    sp.add_argument("--name", required=True,
                    help="agent_name issued by the portal (e.g. user-alice)")
    sp.add_argument("--secret", required=False, default=None,
                    help="HMAC secret issued by the portal "
                         "(可改为环境变量 EXP_BIND_SECRET 提供，避免 argv 泄露)")
    sp.add_argument("--agent-id", default="",
                    help="optional: agent_id from the portal (else random UUID)")
    sp.add_argument("--team", default="default")
    sp.add_argument("--skip-claude-settings", action="store_true",
                    help="don't patch ~/.claude/settings.json env block")
    sp.add_argument("--no-verify", action="store_true",
                    help="skip the post-bind /healthz check")
    sp.set_defaults(func=cmd_bind)

    sp = sub.add_parser("bind-api",
                        help="用门户颁发的 Bearer API Key（expk_...）绑定本机")
    sp.add_argument("--api-key", required=False, default=None,
                    help="API key minted by the portal, e.g. expk_... "
                         "(可改为环境变量 EXP_BIND_API_KEY 提供，避免 argv 泄露)")
    sp.add_argument("--agent-name", default="",
                    help="optional local label; server derives identity from the key")
    sp.add_argument("--no-verify", action="store_true",
                    help="skip the post-bind /v1/me/quota check")
    sp.set_defaults(func=cmd_bind_api)

    sp = sub.add_parser("pair",
                        help="用门户生成的一次性配对码（expair_...）换取 Bearer API Key 并完成绑定（推荐用法）")
    sp.add_argument("--code", required=False, default=None,
                    help="one-time code minted by the portal, e.g. expair_... "
                         "(可改为环境变量 EXP_PAIR_CODE 提供，避免 argv 泄露)")
    sp.add_argument("--agent-name", default="",
                    help="optional local label override")
    sp.add_argument("--no-verify", action="store_true",
                    help="skip the post-pair /v1/me/quota check")
    sp.set_defaults(func=cmd_pair)

    sp = sub.add_parser("list-sessions", help="列出本机指定 runtime 的近期 session 文件（只扫描不上传）")
    sp.add_argument("--source", default="auto", choices=["auto"] + list(ADAPTERS))
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--with-model", action="store_true",
                    help="parse listed sessions enough to include model metadata")
    sp.set_defaults(func=cmd_list_sessions)

    push_args = argparse.ArgumentParser(add_help=False)
    push_args.add_argument("--task", default="misc")
    push_args.add_argument("--sensitivity", default="medium", choices=["low", "medium", "high"])
    push_args.add_argument("--acl", default="private")
    push_args.add_argument("--tag", action="append", default=[])
    push_args.add_argument("--no-trace", action="store_true",
                           help="upload only the LiteCard, drop the trajectory")
    push_args.add_argument("--full-trace", action="store_true",
                           help="also embed the raw source file (base64, capped 8MiB) "
                                "for byte-exact recovery")
    push_args.add_argument("--annotate", action="store_true",
                           help="run synergy-style 5-dim per-turn reward annotation "
                                "before upload (calls claude/anthropic/openai)")
    push_args.add_argument("--annotate-backend", default="auto",
                           choices=["auto", "claude", "anthropic", "openai"])
    push_args.add_argument("--annotate-model", default=None,
                           help="model id for annotation (default haiku)")
    push_args.add_argument("--annotate-subsequent-k", type=int, default=4,
                           help="subsequent turns fed as delayed feedback per evaluation")
    push_args.add_argument("--annotate-max-turns", type=int, default=8,
                           help="cap evaluated turns per session (cost control)")
    push_args.add_argument("--annotate-pick", default="even",
                           choices=["first", "even", "important"])
    push_args.add_argument("--verbose", "-v", action="store_true")
    push_args.add_argument("--yes", "-y", action="store_true",
                           help="bypass the consent prompt; treat decision as 'always'")
    push_args.add_argument("--dry-run", action="store_true",
                           help="run sanitize + structure but do NOT transmit; "
                                "save preview to ~/.experience-pool/pending/")

    sp = sub.add_parser("push", parents=[push_args])
    sp.add_argument("--session", required=True, help="session id, prefix, or path")
    sp.add_argument("--source", default="auto", choices=["auto"] + list(ADAPTERS))
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("push-latest", parents=[push_args])
    sp.add_argument("--source", default="auto", choices=["auto"] + list(ADAPTERS))
    sp.set_defaults(func=cmd_push_latest)

    sp = sub.add_parser("push-all", parents=[push_args],
                        help="批量上传指定 runtime 的全部近期 session（可用 --since 限制日期）")
    sp.add_argument("--source", default="auto", choices=["auto"] + list(ADAPTERS))
    sp.add_argument("--since", default="",
                    help="ISO date prefix; only sessions with mtime >= since are uploaded "
                         "(e.g. 2026-04-01)")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_push_all)

    sp = sub.add_parser("push-file", parents=[push_args])
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_push_file)

    sp = sub.add_parser("annotate-existing", parents=[push_args],
                        help="对一条已上传的经验重跑 5 维 reward 标注并回写服务端（不重新上传 trace）")
    sp.add_argument("--experience-id", required=True,
                    help="server-side experience id returned by an earlier push")
    sp.add_argument("--session", required=True,
                    help="local session id/path that the experience was created from")
    sp.add_argument("--source", default="auto", choices=["auto"] + list(ADAPTERS))
    sp.set_defaults(func=cmd_annotate_existing)

    sp = sub.add_parser("get-rewards", help="拉取一条经验的服务端已存 reward 列表（按 --experience-id 查询）")
    sp.add_argument("--experience-id", required=True)
    sp.add_argument("--judge-model", default=None)
    sp.set_defaults(func=cmd_get_rewards)

    sp = sub.add_parser("daemon-tick",
                        help="增量扫描并上传所有 source 的新 session（一次性，调度器周期性调用）")
    sp.add_argument("--sources", default="",
                    help="comma-sep adapter names; empty = $EXP_AUTO_SOURCES or default set")
    sp.add_argument("--max-per-source", type=int, default=10,
                    help="cap uploads per source per tick (default 10)")
    sp.add_argument("--max-session-kb", type=int, default=4096,
                    help="maximum compacted task/segment payload in KB; "
                         "large Claude files are split (default 4MB)")
    sp.add_argument("--acl", default="", help="default ACL (default $EXP_AUTO_ACL or private)")
    sp.add_argument("--task", default="", help="default task type (default auto-sync)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--verbose", "-v", action="store_true")
    sp.set_defaults(func=cmd_daemon_tick)

    sp = sub.add_parser("daemon-state", help="显示守护进程对每个 source 的最后处理状态（上传/跳过计数等）")
    sp.set_defaults(func=cmd_daemon_state)

    sp = sub.add_parser("daemon-reset",
                        help="清除本地 \"已上传\" 指纹记忆，下次 tick 将重新全量扫描所有 session")
    sp.add_argument("--source", default="", help="reset only this source (default: all)")
    sp.set_defaults(func=cmd_daemon_reset)

    # ------------------------------------------------------------------
    # Consent subcommands — local opt-in/opt-out (consent.json).
    # ------------------------------------------------------------------
    consent = sub.add_parser("consent", help="管理本机上传同意状态（consent.json：global / agent / cwd / session 四种粒度）")
    csub = consent.add_subparsers(dest="consent_cmd", required=True)

    sp = csub.add_parser("show", help="打印 consent.json 内容（或加 --simulate 模拟 decide() 结果）")
    sp.add_argument("--simulate", action="store_true",
                    help="instead of dumping consent.json, run decide() for the args")
    sp.add_argument("--agent", default="")
    sp.add_argument("--cwd", default="")
    sp.add_argument("--session", default="")
    sp.set_defaults(func=cmd_consent_show)

    sp = csub.add_parser("set", help="新增/更新一条 consent 规则（按 global / agent / cwd / session 四种粒度任选）")
    sp.add_argument("--mode", required=True, choices=["always", "never", "ask",
                                                       "prompt-on-start", "dry-run"])
    sp.add_argument("--agent", default="", help="apply rule to this agent only")
    sp.add_argument("--cwd", default="", help="apply rule to this cwd glob (e.g. ~/work/**)")
    sp.add_argument("--session", default="", help="apply rule to this session id")
    sp.add_argument("--reason", default="", help="audit comment")
    sp.add_argument("--acl", default="", help="default ACL for the rule (agent only)")
    sp.set_defaults(func=cmd_consent_set)

    sp = csub.add_parser("reset", help="把 consent.json 重置为默认（清空所有自定义规则）")
    sp.set_defaults(func=cmd_consent_reset)

    sp = csub.add_parser("decide",
                         help="返回 (agent, cwd, session) 三元组的当前 consent 决策（供 hook 脚本调用）")
    sp.add_argument("--agent", default="claude-code")
    sp.add_argument("--cwd", default="")
    sp.add_argument("--session", default="")
    sp.add_argument("--interactive", action="store_true",
                    help="if mode=='ask', drive the prompt and emit the answer")
    sp.set_defaults(func=cmd_consent_decide)

    sp = csub.add_parser("pending", help="列出（或加 --prune 清理）被 skip 后落盘的 pending session")
    sp.add_argument("--prune", action="store_true",
                    help="apply the cap+TTL pruning rules now and exit")
    sp.set_defaults(func=cmd_consent_pending)

    sp = csub.add_parser("revoke", help="请求服务端撤回一条已上传经验（即 /me 上的撤回操作；与顶层 revoke 等价）")
    sp.add_argument("--eid", required=True, help="experience_id to revoke")
    sp.add_argument("--reason", default="user_request")
    sp.set_defaults(func=cmd_consent_revoke)

    # ------------------------------------------------------------------
    # Personal vs. community pool — publish / unpublish / quota
    # ------------------------------------------------------------------
    sp = sub.add_parser("quota",
                        help="显示当前账号的社区池发布配额（publish_count / threshold / community_unlocked）")
    sp.set_defaults(func=cmd_quota)

    sp = sub.add_parser("publish",
                        help="把一条 private 经验发布到社区池（发布前自动跑严格脱敏；需 --eid）")
    sp.add_argument("--eid", required=True, help="experience_id to publish")
    sp.set_defaults(func=cmd_publish)

    sp = sub.add_parser("unpublish",
                        help="把一条已发布的经验下架回 private（注意：publish_count 不会被减）")
    sp.add_argument("--eid", required=True, help="experience_id to unpublish")
    sp.set_defaults(func=cmd_unpublish)

    # ------------------------------------------------------------------
    # 插件 / 下游开发友好的查询命令(都支持 --json 给脚本解析)
    # ------------------------------------------------------------------
    sp = sub.add_parser("search", help="在经验池里浏览匹配经验卡（支持 personal / community / auto / project:<slug>）")
    sp.add_argument("--q", required=True, help="查询文本")
    sp.add_argument("--top-k", type=int, default=5)
    sp.add_argument("--scope", default="auto",
                    help="auto | personal | community | project:<slug>")
    sp.add_argument("--task-type", default=None,
                    help="只搜某 task_type 下的(可选)")
    sp.add_argument("--json", action="store_true", help="JSON 输出便于脚本解析")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("rag-context",
                        help="平台侧 RAG 召回：chunk 检索 + ACL + 上下文压缩（推荐给自动召回）")
    sp.add_argument("--q", required=True, help="查询文本")
    sp.add_argument("--top-k", type=int, default=3)
    sp.add_argument("--scope", default="auto",
                    help="auto | personal | community | project:<slug>")
    sp.add_argument("--project", default="",
                    help="项目 slug/id；也可用 --scope project:<slug>")
    sp.add_argument("--task-type", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_rag_context)

    sp = sub.add_parser(
        "reuse-feedback",
        help="给一次 RAG 召回事件打反馈奖励，并回写经验 Q 值",
    )
    sp.add_argument("--event-id", default="", help="event_id returned by rag-context")
    sp.add_argument("--last", action="store_true", help="use the last automatic recall event")
    sp.add_argument("--experience-id", action="append", default=[], help="experience id to rate; repeat or comma-separate")
    sp.add_argument("--chunk-id", action="append", default=[], help="chunk id to rate; repeat or comma-separate")
    sp.add_argument("--reward", type=float, required=True, help="-1 harmful, 0 neutral, +1 helpful")
    sp.add_argument("--confidence", type=float, default=0.35, help="confidence in [0,1], default 0.35")
    sp.add_argument("--reason", default="", help="brief feedback reason")
    sp.add_argument("--source", default="agent", help="feedback source label, default agent")
    sp.add_argument("--final-status", default="unknown", help="task outcome label, e.g. success/partial/failed")
    sp.add_argument("--not-used", action="store_true", help="mark the recalled item as not actually used")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reuse_feedback)

    sp = sub.add_parser("projects", help="列出当前凭据可用的项目池")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_projects)

    sp = sub.add_parser("get",
                        help="按 experience_id 拉取一条经验的完整卡片（LiteCard + 可选 trajectory）")
    sp.add_argument("--eid", required=True, help="experience_id")
    sp.add_argument("--include-trajectory", action="store_true",
                    help="同时返回完整 trajectory(气泡渲染所需)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("list",
                        help="列出当前账号 private 库里的全部经验（默认 50 条；服务端 /me 视图）")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("ls",
                        help="`list` 的别名（习惯 Unix 风格者可用）")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("revoke",
                        help="撤回一条已上传的经验（顶层快捷别名，与 consent revoke 等价）")
    sp.add_argument("--eid", required=True)
    sp.add_argument("--reason", default="user_request")
    sp.set_defaults(func=cmd_revoke)

    sp = sub.add_parser("skills-search",
                        help="在 skills 库（蒸馏后的可复用片段）中做语义检索")
    sp.add_argument("--q", required=True)
    sp.add_argument("--top-k", type=int, default=5)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_skills_search)

    sp = sub.add_parser("skills-install",
                        help="按名字把一个 skill 拉到本地（--target 指定安装目录，缺省为 ~/.claude/skills/）")
    sp.add_argument("--name", required=True)
    sp.add_argument("--target", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_skills_install)

    sp = sub.add_parser("opf-status",
                        help="查询 OPF backfill worker 的当前处理状态（运维诊断用，普通用户用不到）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_opf_status)

    sp = sub.add_parser("dashboard",
                        help="查看全局经验池指标看板（推送量、用户数、sanitize 状态分布、近 7 天趋势）")
    sp.set_defaults(func=cmd_admin_dashboard)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

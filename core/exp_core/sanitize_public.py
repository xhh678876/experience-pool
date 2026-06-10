"""Strict-public sanitize — runs before an experience is published to the
community pool.

This is intentionally a *separate* layer from the regular sanitize
pipeline (sanitize.py). The regular pipeline runs on every push and tries
to be lenient: replace secrets with placeholders, but keep the trace
useful. Strict-public is the opposite: when a user clicks "publish to
community", we run a much stricter pass that REJECTS anything that could:

  * Identify the originating machine (file:// URIs, local resources)
  * Phone home to localhost / private IPs / vscode-resource://
  * Leak absolute filesystem layout
  * Carry stable session UUIDs that let an attacker map back to private rows

The semantics are intentionally different from layer1_text:
  - layer1_text  REPLACES the offending span with a placeholder
  - strict_public DETECTS the offending span and REPORTS — caller decides

If anything fires here, publish is BLOCKED; we tell the user exactly what
hit so they can clean it up and republish.

The user's example that motivated this layer:
    file:///Users/someuser/Library/Application%20Support/LarkShell/...
That URL by itself is harmless, but it leaks (a) the OS user, (b) the
fact that the user has Lark installed, (c) a stable resource UUID that
could be used to fingerprint the user across sessions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


# --------------------------------------------------------------------------
# Public-mode block patterns. Each rule is (name, regex, severity).
# All are 'blocking' for publish — there is no soft severity here.
# --------------------------------------------------------------------------

_PUBLIC_BLOCK_RULES: list[tuple[str, re.Pattern[str], str]] = [
    # file:// URIs in any form, percent-encoded or not.
    ("file_uri",
     re.compile(r"file://[^\s'\"]*", re.IGNORECASE),
     "leaks local filesystem path"),

    # Lark / DingTalk / WeCom local SDK resource paths.
    # Pattern: <something>Shell/sdk_storage/...
    ("local_app_resource",
     re.compile(r"\b[A-Za-z]+Shell/sdk_storage/[^\s'\"]+", re.IGNORECASE),
     "leaks local IM-app resource path"),

    # vscode-resource:// vscode-webview:// vscode-file://
    ("vscode_resource",
     re.compile(r"\bvscode-(?:resource|webview|file)://[^\s'\"]*", re.IGNORECASE),
     "leaks IDE-internal resource"),

    # chrome-extension:// moz-extension://
    ("browser_extension_url",
     re.compile(r"\b(?:chrome|moz|edge|safari-web)-extension://[^\s'\"]*", re.IGNORECASE),
     "leaks installed browser extension ID"),

    # localhost / 127.0.0.1 / 0.0.0.0 / [::1] inside URLs.
    ("localhost_url",
     re.compile(
         r"\bhttps?://(?:localhost|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|0\.0\.0\.0|\[::1\])(?::\d+)?(?:/[^\s'\"]*)?",
         re.IGNORECASE,
     ),
     "leaks localhost endpoint"),

    # RFC1918 private IPs in URLs.
    ("private_ip_url",
     re.compile(
         r"\bhttps?://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})(?::\d+)?(?:/[^\s'\"]*)?",
         re.IGNORECASE,
     ),
     "leaks private-network endpoint"),

    # Absolute Unix paths to system directories that suggest an absolute
    # filesystem layout. We accept paths under /tmp /var /etc /opt /usr
    # /private /System (macOS sandbox roots) / Library when they appear
    # as standalone tokens (not inside a generic regex).
    # Note: home_path (regular sanitize) already replaces /Users/x/ and
    # /home/x/ with <HOMEDIR>/, so by the time strict_public runs the
    # only absolute paths left should be system roots.
    ("absolute_system_path",
     re.compile(
         r"(?<![\w/])(?:/(?:etc|var|opt|tmp|private|System|Library|Applications)/[^\s'\":]+)",
     ),
     "leaks absolute system path"),

    # Windows absolute paths.
    ("windows_path",
     re.compile(r"\b[A-Za-z]:\\(?:Users|Windows|Program Files|Documents and Settings)\\[^\s'\"]+",
                re.IGNORECASE),
     "leaks Windows absolute path"),

    # Stable UUID-like session identifiers (8-4-4-4-12 hex).
    # Lenient sanitize keeps these because they're often legitimately
    # user-facing tokens. Strict-public blocks them because the same
    # UUID can be used to map a public experience back to a private
    # session record on the server.
    ("session_uuid",
     re.compile(
         r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
         re.IGNORECASE,
     ),
     "leaks session/experience UUID"),
]


@dataclass(frozen=True)
class StrictHit:
    """One offending span found in one string."""
    rule: str
    reason: str
    snippet: str
    location: str   # e.g. "trajectory[2].content" or "card.outcome"


@dataclass
class StrictPublicResult:
    """What strict_public_check returned for a whole experience."""
    ok: bool
    hits: list[StrictHit] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def reject_payload(self) -> dict[str, Any]:
        """Format suitable for HTTP 422 response body."""
        return {
            "ok": False,
            "status": "blocked",
            "reason": "strict_public_sanitize blocked publication",
            "hit_count": len(self.hits),
            "summary": dict(self.summary),
            "hits": [
                {
                    "rule": h.rule,
                    "reason": h.reason,
                    "location": h.location,
                    "preview": h.snippet[:120],
                }
                for h in self.hits[:50]  # cap response size
            ],
        }


# --------------------------------------------------------------------------
# Walker — recursively scan strings inside a card / trajectory tree.
# --------------------------------------------------------------------------

_SKIP_KEYS = frozenset({
    "id", "type", "role", "tool_use_id", "tool_call_id",
    "name", "subtype", "model", "stop_reason", "stop_sequence",
    "usage", "index", "ts", "tool_result_for",
})


def _scan_string(text: str, location: str, hits: list[StrictHit]) -> None:
    if not text:
        return
    for rule_name, pattern, reason in _PUBLIC_BLOCK_RULES:
        for m in pattern.finditer(text):
            hits.append(StrictHit(
                rule=rule_name,
                reason=reason,
                snippet=m.group(0),
                location=location,
            ))


def _walk(node: Any, path: str, hits: list[StrictHit]) -> None:
    if isinstance(node, str):
        _scan_string(node, path, hits)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(item, f"{path}[{i}]", hits)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in _SKIP_KEYS or not isinstance(v, (str, list, dict)):
                continue
            _walk(v, f"{path}.{k}" if path else k, hits)


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------


def strict_public_check(
    *,
    card: dict[str, Any] | None = None,
    trajectory: list[dict[str, Any]] | None = None,
    system: Any = None,
    tools: Any = None,
    meta: Any = None,
) -> StrictPublicResult:
    """Scan an experience for content that must NOT enter the community pool.

    Pass any combination of {card, trajectory, system, tools, meta} —
    each section is walked recursively. Returns a StrictPublicResult; on
    `ok=False` the caller (the publish endpoint) should reject with HTTP
    422 and surface the hits to the user.
    """
    hits: list[StrictHit] = []
    if card is not None:
        for k in ("query", "intent", "outcome", "summary"):
            v = card.get(k)
            if isinstance(v, str):
                _scan_string(v, f"card.{k}", hits)
        steps = card.get("steps")
        if isinstance(steps, list):
            for i, s in enumerate(steps):
                if isinstance(s, str):
                    _scan_string(s, f"card.steps[{i}]", hits)
    if trajectory is not None:
        _walk(trajectory, "trajectory", hits)
    if system is not None:
        _walk(system, "system", hits)
    if tools is not None:
        _walk(tools, "tools", hits)
    if meta is not None:
        _walk(meta, "meta", hits)

    summary: dict[str, int] = {}
    for h in hits:
        summary[h.rule] = summary.get(h.rule, 0) + 1
    return StrictPublicResult(
        ok=not hits,
        hits=hits,
        summary=summary,
    )


def list_rules() -> list[dict[str, str]]:
    """Operator-facing listing for /v1/admin endpoints + UI."""
    return [
        {"rule": name, "reason": reason, "pattern": pat.pattern}
        for name, pat, reason in _PUBLIC_BLOCK_RULES
    ]

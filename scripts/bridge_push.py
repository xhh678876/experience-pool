#!/usr/bin/env python3
"""Bridge: claude_sft_delivery / cursor_sft_delivery output → experience-pool.

Both SFT delivery pipelines produce JSONL where each line is one already-
extracted "single-turn" record. This script maps each line to one POST to
/v1/lite/push, preserving:

  - messages    → trajectory  (recursively sanitized server-side)
  - system      → system      (only present in the claude pipeline)
  - tools       → tools       (only present in the claude pipeline)
  - meta        → meta        (carries version/model/source_file/is_subagent…)

Plus it derives the LiteCard's 4 visible fields:

  - query    = last user-turn text in messages
  - intent   = first 120 chars of query
  - steps    = each assistant text block, truncated
  - outcome  = last assistant text block

Usage:

    python3 bridge_push.py \\
        --jsonl path/to/extracted.jsonl \\
        --base http://127.0.0.1:8080 \\
        --agent claude-sft-import \\
        --secret <hex secret from `exp register` or POST /v1/agents/register> \\
        [--source claude|cursor]   (default: auto-detect from record shape)
        [--task <type>] [--acl private|team:<X>] [--sensitivity low|medium|high]
        [--dry-run]                (print would-send JSON, don't POST)
        [--max <N>]                (only push first N records)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Iterable


def sign(secret: str, method: str, path: str, body: bytes) -> str:
    canonical = method.upper().encode() + b"\n" + path.encode() + b"\n" + body
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def post_lite(base: str, agent: str, secret: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = sign(secret, "POST", "/v1/lite/push", body)
    req = urllib.request.Request(
        url=base.rstrip("/") + "/v1/lite/push",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Name": agent,
            "X-Signature": sig,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace")}


def detect_source(record: dict[str, Any]) -> str:
    if "system" in record or (isinstance(record.get("meta"), dict) and "entrypoint" in record["meta"]):
        return "claude"
    if "segment_index" in record:
        return "cursor"
    return "unknown"


def text_of_message(msg: dict[str, Any]) -> str:
    """Flatten a message's content into a plain string for query/outcome derivation."""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out: list[str] = []
        for block in c:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    out.append(str(block.get("text", "")))
                elif t == "thinking":
                    pass  # don't surface thinking in user/outcome
                elif t == "tool_use":
                    out.append(f"[tool_use:{block.get('name','?')}]")
                elif t == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, str):
                        out.append(inner[:200])
                    else:
                        out.append("[tool_result]")
        return "\n".join(s for s in out if s)
    if c is None:
        return ""
    return str(c)


def derive_card(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract query/intent/steps/outcome from the messages timeline."""
    user_texts = [text_of_message(m) for m in messages if m.get("role") == "user"]
    asst_texts = [text_of_message(m) for m in messages if m.get("role") in ("assistant", "model")]

    last_user = ""
    for t in user_texts:
        if t.strip():
            last_user = t.strip()
            break
    # last_user is actually first non-empty; pick the LAST instead — that's
    # closer to "what the user finally asked", since extracted single-turn
    # records typically end at the target user_line.
    for t in reversed(user_texts):
        if t.strip():
            last_user = t.strip()
            break

    last_asst = ""
    for t in reversed(asst_texts):
        if t.strip():
            last_asst = t.strip()
            break

    steps = [t.strip()[:280] for t in asst_texts if t.strip()][:8]

    return {
        "query": last_user or "(no user turn)",
        "intent": (last_user[:120] or "unspecified task").strip(),
        "steps": steps if steps else ["(no assistant content)"],
        "outcome": last_asst[:500] or "(no outcome)",
    }


def map_record(
    record: dict[str, Any],
    *,
    source: str,
    default_task: str,
    default_acl: str,
    default_sensitivity: str,
) -> dict[str, Any] | None:
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        return None

    card = derive_card(messages)

    meta = dict(record.get("meta") or {})
    # Carry IDs that the extractor produced so we can trace lineage later.
    if "session_id" in record:
        meta.setdefault("session_id", record["session_id"])
    if "turn_index" in record:
        meta.setdefault("turn_index", record["turn_index"])
    if "segment_index" in record:
        meta.setdefault("segment_index", record["segment_index"])
    if "is_subagent" in record:
        meta.setdefault("is_subagent", record["is_subagent"])
    if "parent_session_id" in record:
        meta.setdefault("parent_session_id", record["parent_session_id"])
    if "subagent_type" in record:
        meta.setdefault("subagent_type", record["subagent_type"])
    meta.setdefault("ingest_source", source)

    # Source model from extractor meta if available.
    source_model = meta.get("model") or meta.get("source_turn_model") or "unknown"

    payload: dict[str, Any] = {
        "query": card["query"],
        "intent": card["intent"],
        "steps": card["steps"],
        "outcome": card["outcome"],
        "task_type": default_task,
        "source_model": str(source_model),
        "sensitivity": default_sensitivity,
        "acl": default_acl,
        "tags": [source],
        "trajectory": messages,
        "meta": meta,
    }

    sysblock = record.get("system")
    if sysblock is not None:
        payload["system"] = sysblock
    tools = record.get("tools")
    if tools is not None:
        payload["tools"] = tools
    return payload


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fp:
        for n, line in enumerate(fp, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"line {n}: invalid JSON ({e})\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True, type=Path,
                    help="Path to claude_sft_delivery/cursor_sft_delivery output JSONL")
    ap.add_argument("--base", required=True, help="experience-pool API base, e.g. http://127.0.0.1:8080")
    ap.add_argument("--agent", required=True, help="HMAC agent name registered on the server")
    ap.add_argument("--secret", required=True, help="HMAC secret returned at register time")
    ap.add_argument("--source", choices=["auto", "claude", "cursor"], default="auto")
    ap.add_argument("--task", default="misc", help="task_type tag for all rows")
    ap.add_argument("--acl", default="team:imported", help="ACL: private | team:<X>; publish separately for community")
    ap.add_argument("--sensitivity", choices=["low", "medium", "high"], default="medium")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=None, help="cap number of pushes")
    ap.add_argument("--sleep", type=float, default=0.05, help="sec between pushes (rate-limit friendly)")
    args = ap.parse_args()

    if not args.jsonl.exists():
        sys.exit(f"file not found: {args.jsonl}")

    pushed = skipped = failed = 0
    for record in iter_jsonl(args.jsonl):
        source = args.source if args.source != "auto" else detect_source(record)
        payload = map_record(
            record,
            source=source,
            default_task=args.task,
            default_acl=args.acl,
            default_sensitivity=args.sensitivity,
        )
        if payload is None:
            skipped += 1
            continue

        if args.dry_run:
            print(json.dumps({"would_push": payload}, ensure_ascii=False, indent=2))
        else:
            resp = post_lite(args.base, args.agent, args.secret, payload)
            if "error" in resp:
                failed += 1
                sys.stderr.write(f"FAILED: {resp}\n")
            else:
                pushed += 1
                eid = resp.get("experience_id", "?")
                print(f"  {eid}  ({source}, {len(payload['trajectory'])} turns)")

        if args.max and pushed >= args.max:
            break
        if args.sleep > 0 and not args.dry_run:
            time.sleep(args.sleep)

    print(f"\nresult: pushed={pushed} skipped_no_messages={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

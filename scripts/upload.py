#!/usr/bin/env python3
"""上传一条经验到内网经验池。

零依赖（只用 Python 标准库），适合任何能跑 python3 的内网终端。

用法
─────

# 1) 第一次先注册一个 agent，拿 secret
python3 upload.py register \\
    --base http://127.0.0.1:8080 \\
    --name my-agent --team my-team

# 输出 ~/.experience-pool/credentials/my-agent.json，secret 自动保存

# 2) 上传一条经验（trajectory.json 是你的对话记录）
python3 upload.py push \\
    --base http://127.0.0.1:8080 \\
    --agent my-agent \\
    --file trajectory.json \\
    --task ml_debug --acl private --sensitivity low

# 3) 上传完直接打开 UI 自动 5 秒内刷新看到

trajectory.json 接受三种格式
────────────────────────────

  A. 嵌套 + Anthropic block（推荐，能保留工具调用）：
     {"trajectory": [
        {"role":"user","content":"问题"},
        {"role":"assistant","content":[
            {"type":"text","text":"思路"},
            {"type":"tool_use","id":"toolu_1","name":"Read",
             "input":{"file_path":"./foo"}}]},
        {"role":"user","content":[
            {"type":"tool_result","tool_use_id":"toolu_1","content":"文件内容..."}]},
        {"role":"assistant","content":[{"type":"text","text":"结论"}]}
     ]}

  B. 扁平字符串（最简单，丢失工具结构）：
     {"trajectory": [
        {"role":"user","content":"..."},
        {"role":"assistant","content":"..."}
     ]}

  C. OpenAI 风格（自动转换）：
     {"trajectory": [
        {"role":"user","content":"..."},
        {"role":"assistant","content":"...","tool_calls":[
            {"id":"call_1","function":{"name":"foo","arguments":"{...}"}}]},
        {"role":"tool","tool_call_id":"call_1","content":"..."}
     ]}

裸数组也接受（即直接 [{role,content}, ...]，没有外层 trajectory key）。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any


CRED_DIR = Path(os.environ.get("EXP_CREDENTIALS_DIR")
                or Path.home() / ".experience-pool" / "credentials")


# ─── HMAC ──────────────────────────────────────────────────────────────────

def sign(secret: str, method: str, path: str, body: bytes) -> str:
    canonical = method.upper().encode() + b"\n" + path.encode() + b"\n" + body
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def post_json(base: str, path: str, body: dict[str, Any], *,
              agent: str | None = None, secret: str | None = None) -> dict[str, Any]:
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if agent and secret:
        headers["X-Agent-Name"] = agent
        headers["X-Signature"] = sign(secret, "POST", path, body_bytes)
    req = urllib.request.Request(base.rstrip("/") + path,
                                 data=body_bytes, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} from {path}: {msg}")
    except urllib.error.URLError as e:
        raise SystemExit(f"connect failed to {base}: {e}")


# ─── credential ────────────────────────────────────────────────────────────

def load_secret(name: str) -> str:
    if os.environ.get("EXP_AGENT_SECRET") and os.environ.get("EXP_AGENT_NAME") == name:
        return os.environ["EXP_AGENT_SECRET"]
    f = CRED_DIR / f"{name}.json"
    if not f.exists():
        raise SystemExit(
            f"找不到凭据 {f}\n"
            f"先跑：python3 {sys.argv[0]} register --name {name} --team <your-team> --base <BASE>"
        )
    with f.open() as fp:
        return json.load(fp)["secret"]


def save_credential(cred: dict[str, Any]) -> Path:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    f = CRED_DIR / f"{cred['agent_name']}.json"
    f.write_text(json.dumps(cred, indent=2, ensure_ascii=False))
    f.chmod(0o600)
    return f


# ─── derive query/intent/steps/outcome from messages ──────────────────────

def text_of(msg: dict[str, Any]) -> str:
    """把 message.content 拍扁成纯字符串。保留工具调用的可读标记。"""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for block in c:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    out.append(str(block.get("text", "")))
                elif t == "tool_use":
                    out.append(f"[tool_use:{block.get('name','?')}]")
                elif t == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, str):
                        out.append(inner[:200])
                    else:
                        out.append("[tool_result]")
        return "\n".join(s for s in out if s)
    return "" if c is None else str(c)


def derive_card(messages: list[dict[str, Any]]) -> dict[str, Any]:
    user_texts = [text_of(m) for m in messages if m.get("role") == "user"]
    asst_texts = [text_of(m) for m in messages if m.get("role") in ("assistant", "model")]

    last_user = next((t.strip() for t in reversed(user_texts) if t.strip()), "")
    last_asst = next((t.strip() for t in reversed(asst_texts) if t.strip()), "")
    steps = [t.strip()[:280] for t in asst_texts if t.strip()][:8]

    return {
        "query":   last_user or "(no user turn)",
        "intent":  (last_user[:120] or "unspecified task").strip(),
        "steps":   steps if steps else ["(no assistant content)"],
        "outcome": last_asst[:500] or "(no outcome)",
    }


def load_messages(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("trajectory"), list):
            return payload["trajectory"]
        if isinstance(payload.get("messages"), list):
            return payload["messages"]
    raise SystemExit(f"{path} 不是合法 trajectory：要么裸数组，要么 {{trajectory:[...]}} 或 {{messages:[...]}}")


# ─── commands ──────────────────────────────────────────────────────────────

def cmd_register(args: argparse.Namespace) -> int:
    cred = post_json(args.base, "/v1/agents/register",
                     {"name": args.name, "team": args.team})
    if "secret" not in cred:
        raise SystemExit(f"register 返回意外: {cred}")
    f = save_credential(cred)
    print(f"凭据已保存: {f}")
    print(f"agent_id: {cred.get('agent_id', '?')}")
    print(f"secret:   {cred['secret'][:12]}... (完整 64 字符见文件)")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    secret = args.secret or load_secret(args.agent)
    messages = load_messages(Path(args.file))
    card = derive_card(messages)

    payload: dict[str, Any] = {
        **card,
        "task_type":    args.task,
        "source_model": args.model,
        "sensitivity":  args.sensitivity,
        "acl":          args.acl,
        "tags":         args.tag or [],
        "trajectory":   messages,
    }
    if args.system_file:
        payload["system"] = json.loads(Path(args.system_file).read_text())
    if args.tools_file:
        payload["tools"] = json.loads(Path(args.tools_file).read_text())
    if args.meta:
        payload["meta"] = json.loads(args.meta)

    resp = post_json(args.base, "/v1/lite/push", payload,
                     agent=args.agent, secret=secret)
    print(json.dumps(resp, indent=2, ensure_ascii=False))
    eid = resp.get("experience_id")
    if eid:
        # quick hint: how the user can look at it
        print(f"\n查看：{args.base.replace(':8080', ':3000')}/experiences/{eid}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    req = urllib.request.Request(args.base.rstrip("/") + "/healthz")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(resp.read().decode())
        return 0
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description="upload one experience to the intranet experience-pool")
    p.add_argument("--base", default=os.environ.get("EXP_BASE_URL", "http://127.0.0.1:8080"),
                   help="API base URL (env EXP_BASE_URL)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("health", help="quick reachability check")
    sp.set_defaults(func=cmd_health)

    sr = sub.add_parser("register", help="申请 HMAC 凭据并保存到 ~/.experience-pool/credentials/")
    sr.add_argument("--name", required=True)
    sr.add_argument("--team", required=True)
    sr.set_defaults(func=cmd_register)

    sp = sub.add_parser("push", help="上传一条 trajectory")
    sp.add_argument("--agent", required=True, help="已注册的 agent name")
    sp.add_argument("--secret", default=None, help="HMAC secret（默认读 ~/.experience-pool/credentials/）")
    sp.add_argument("--file", required=True, help="trajectory JSON 文件")
    sp.add_argument("--task", default="misc")
    sp.add_argument("--model", default="unknown")
    sp.add_argument("--sensitivity", choices=["low", "medium", "high"], default="low")
    sp.add_argument("--acl", default="private", help="private | team:<X>; publish separately for community")
    sp.add_argument("--tag", action="append", default=[])
    sp.add_argument("--system-file", help="可选：system prompt JSON 文件")
    sp.add_argument("--tools-file", help="可选：工具 schema JSON 文件")
    sp.add_argument("--meta", help="可选：meta JSON 字符串")
    sp.set_defaults(func=cmd_push)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

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
from contextlib import asynccontextmanager
import datetime as _dt
import hashlib
import hmac
import json
import os
import shlex
import shutil
import sqlite3
import tarfile
import io
import tempfile
import time
from pathlib import Path
from typing import Any
import re

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import api_keys as api_keys_mod
from . import lite as lite_mod
from . import skills as skills_mod
from . import auto_label as auto_label_mod
from . import title_refine as title_refine_mod
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


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Loudly remind operators when registration is unauthenticated. The
    bootstrap install flow keeps registration open by default; on a publicly
    reachable deployment that lets anyone create accounts. Setting
    EXP_REGISTER_TOKEN / EXP_USER_REGISTER_TOKEN gates it."""
    if not os.getenv("EXP_REGISTER_TOKEN"):
        LOG.warning(
            "agent_registration_open",
            hint="anyone can call POST /v1/agents/register; set EXP_REGISTER_TOKEN to gate it",
        )
    if not os.getenv("EXP_USER_REGISTER_TOKEN"):
        LOG.warning(
            "user_registration_open",
            hint="anyone can call POST /v1/users/register; set EXP_USER_REGISTER_TOKEN to gate it",
        )
    yield


app = FastAPI(title="Experience Pool", version="0.1.0", lifespan=_lifespan)


# ----- middleware -----------------------------------------------------------

PUBLIC_PATHS = {
    "/healthz", "/v1/agents/register",
    "/docs", "/openapi.json", "/redoc",
    "/install", "/install.sh",
    "/exp_uploader.py", "/exp_annotator.py", "/session_start.sh",
    "/exp_consent.py", "/agent-contract.md",
    "/opf_service.py", "/opf_filter.py",
    "/session-extractor/run.sh",
    "/session-extractor/extract_and_upload.py",
    "/session-extractor/README.md",
    "/plugins/expool.tgz",
    "/plugins/install.sh",
    "/v1/plugin/package",
    "/v1/plugin/pair",
    # Email/password auth: register & login bootstrap a session before any
    # HMAC credential exists, so they must be HMAC-public. Subsequent
    # /v1/users/* calls validate via the session cookie middleware below.
    "/v1/users/register", "/v1/users/login",
}

# Routes whose auth is "session cookie OR HMAC". Listed here so the HMAC
# middleware passes them through to a per-route check rather than 401-ing
# missing X-Agent-Name. Session validation happens inside the route.
SESSION_AUTH_PATHS = {
    "/v1/users/me", "/v1/users/me/bind-script", "/v1/users/logout",
    "/v1/users/me/api-keys", "/v1/users/me/pairing-codes",
}


def _is_session_auth_path(path: str) -> bool:
    if path in SESSION_AUTH_PATHS:
        return True
    # Subpaths under /v1/users/me/api-keys (e.g. .../api-keys/<key_id>)
    if path.startswith("/v1/users/me/api-keys/"):
        return True
    if path.startswith("/v1/users/me/pairing-codes/"):
        return True
    return False


# ----- bootstrap files (so `curl <base>/install | bash` works) --------------
# Serves install.sh + Python clients out of dist-public/.  These paths are
# PUBLIC (no HMAC) — they only contain installer code, not data.
#
# /install (and /install.sh) is special: we rewrite the hardcoded
# `BASE="${EXP_BASE_URL:-...}"` line so the default points at this server's
# externally reachable URL, not a stale public deployment. Operators should set
# EXP_BIND_BASE_URL to the same gateway shown on /plugins.

_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2] / "dist-public"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_STATIC: dict[str, tuple[str, str]] = {
    "/exp_uploader.py":     ("exp_uploader.py",     "text/x-python; charset=utf-8"),
    "/exp_annotator.py":    ("exp_annotator.py",    "text/x-python; charset=utf-8"),
    "/exp_consent.py":      ("exp_consent.py",      "text/x-python; charset=utf-8"),
    "/session_start.sh":    ("session_start.sh",    "text/x-shellscript; charset=utf-8"),
    "/agent-contract.md":   ("agent-contract.md",   "text/markdown; charset=utf-8"),
    # OPF host bootstrap files — let the GPU machine `curl ... | bash`
    # without needing repo access.
    "/opf_service.py":      ("opf_service.py",      "text/x-python; charset=utf-8"),
    "/opf_filter.py":       ("opf_filter.py",       "text/x-python; charset=utf-8"),
    # session-extractor — standalone tool the user copies from /me page
    # to bulk-upload local Claude/Codex sessions to their private repo.
    "/session-extractor/run.sh":               ("session-extractor/run.sh",               "text/x-shellscript; charset=utf-8"),
    "/session-extractor/extract_and_upload.py": ("session-extractor/extract_and_upload.py", "text/x-python; charset=utf-8"),
    "/session-extractor/README.md":            ("session-extractor/README.md",            "text/markdown; charset=utf-8"),
}


def _patched_install_script(default_base: str) -> bytes:
    """Read install.sh and rewrite the hardcoded BASE default to default_base."""
    src = (_BOOTSTRAP_ROOT / "install.sh").read_bytes()
    # Match: BASE="${EXP_BASE_URL:-...}"
    import re as _re
    new = _re.sub(
        rb'BASE="\$\{EXP_BASE_URL:-[^"}]+\}"',
        f'BASE="${{EXP_BASE_URL:-{default_base}}}"'.encode(),
        src,
        count=1,
    )
    return new


def _register_bootstrap_routes() -> None:
    from fastapi import Response

    # Static files first.
    for url_path, (rel, ct) in _BOOTSTRAP_STATIC.items():
        full = _BOOTSTRAP_ROOT / rel

        def _make_static(p: Path, content_type: str):
            async def _h() -> Response:
                if not p.is_file():
                    raise HTTPException(status_code=404, detail=f"{p.name} not found")
                return Response(content=p.read_bytes(), media_type=content_type)
            return _h

        app.add_api_route(url_path, _make_static(full, ct),
                          methods=["GET"], include_in_schema=False)

    # /install and /install.sh: dynamic — rewrites BASE to point at THIS server.
    async def _install_handler(request: Request) -> "Response":
        default_base = _request_public_base_url(request)
        try:
            patched = _patched_install_script(default_base)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="install.sh not found on server")
        return Response(content=patched,
                        media_type="text/x-shellscript; charset=utf-8")

    app.add_api_route("/install",    _install_handler, methods=["GET"], include_in_schema=False)
    app.add_api_route("/install.sh", _install_handler, methods=["GET"], include_in_schema=False)


_register_bootstrap_routes()


def _latest_plugin_tarball() -> Path | None:
    explicit = os.getenv("EXP_PLUGIN_TARBALL")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p

    candidates: set[Path] = set()
    for root in (
        _REPO_ROOT / "dist-public" / "plugins",
        _REPO_ROOT.parent / "expool-mcp-plugin" / "dist",
    ):
        if root.is_dir():
            candidates.update(root.glob("expool.tgz"))
            candidates.update(root.glob("chuangzhi-expool-plugin-*.tgz"))
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (_plugin_version_key(p), p.name), reverse=True)[0]


def _plugin_version_key(tarball: Path) -> tuple[int, int, int, str]:
    version = ""
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            member = tf.extractfile("package/package.json")
            if member is not None:
                pkg = json.loads(member.read().decode("utf-8"))
                version = str(pkg.get("version") or "")
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError):
        version = ""
    if not version:
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", tarball.name)
        if m:
            version = ".".join(m.groups())
    parts = re.findall(r"\d+", version)[:3]
    nums = [int(part) for part in parts]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2], version)


def _request_public_base_url(request: Request) -> str:
    explicit = (os.getenv("EXP_PUBLIC_BASE_URL")
                or os.getenv("EXP_PUBLIC_API_BASE")
                or os.getenv("EXP_BIND_BASE_URL"))
    if explicit:
        return explicit.rstrip("/")
    ui_public = os.getenv("EXP_UI_PUBLIC_URL")
    if ui_public:
        m = re.match(r"^(.*)/proxy/\d+/?$", ui_public.rstrip("/"))
        if m:
            return f"{m.group(1)}/proxy/3080"
    proto = (request.headers.get("x-forwarded-proto")
             or ("https" if request.url.scheme == "https" else "http"))
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or f"{request.url.hostname}:{request.url.port}")
    return f"{proto}://{host}".rstrip("/")


def _plugin_tarball_metadata(tarball: Path) -> dict[str, Any]:
    raw = tarball.read_bytes()
    stat = tarball.stat()
    package_name = "@chuangzhi/expool-plugin"
    version = None
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            member = tf.extractfile("package/package.json")
            if member is not None:
                pkg = json.loads(member.read().decode("utf-8"))
                package_name = str(pkg.get("name") or package_name)
                version = str(pkg.get("version") or "")
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError):
        version = None
    if not version:
        version = tarball.stem.replace("chuangzhi-expool-plugin-", "")
    return {
        "name": package_name,
        "version": version,
        "filename": tarball.name,
        "size_bytes": stat.st_size,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mtime": _dt.datetime.fromtimestamp(stat.st_mtime, tz=_dt.timezone.utc).isoformat(),
    }


def _plugin_install_command(download_url: str, base: str) -> str:
    return (
        'tmp="${TMPDIR:-/tmp}/expool-plugin.tgz" && '
        f"curl --noproxy '*' -fsSL {shlex.quote(download_url)} -o \"$tmp\" && "
        'npm install -g "$tmp" && '
        f"expool-plugin install --agents claude,codex,openclaw,hermes --base {shlex.quote(base)} --force"
    )


def _plugin_install_script(download_url: str, base: str, sha256: str) -> str:
    return f"""#!/usr/bin/env bash
# expool plugin one-shot installer.
# Auto-detects which agents are on this machine (claude / codex / openclaw /
# hermes) and only registers the MCP server into the ones that exist. Pass
# extra flags (e.g. --agents claude only) after `bash -s --`.
set -euo pipefail

BASE={shlex.quote(base)}
URL={shlex.quote(download_url)}
SHA256={shlex.quote(sha256)}
TMP="${{TMPDIR:-/tmp}}/expool-plugin-${{SHA256:0:12}}.tgz"

echo "[expool] downloading $URL"
curl --noproxy '*' -fsSL "$URL" -o "$TMP"

if command -v sha256sum >/dev/null 2>&1; then
  got="$(sha256sum "$TMP" | awk '{{print $1}}')"
  if [ "$got" != "$SHA256" ]; then
    echo "[expool] sha256 mismatch: expected $SHA256, got $got" >&2
    exit 1
  fi
  echo "[expool] sha256 ok: $SHA256"
else
  echo "[expool] sha256sum not found; skipping checksum verification" >&2
fi

npm install -g "$TMP"
echo "[expool] auto-detecting installed agents and registering MCP..."
expool-plugin install --agents claude,codex,openclaw,hermes --base "$BASE" --force "$@"
echo "[expool] done — next step: /expool:pair expair_... (generate code at $BASE/me/api-keys)"
"""


@app.get("/plugins/expool.tgz", include_in_schema=False)
async def expool_plugin_tarball():
    """Serve the current expool npm tarball for intranet installs."""
    from fastapi.responses import FileResponse

    tarball = _latest_plugin_tarball()
    if tarball is None:
        raise HTTPException(status_code=404, detail="expool plugin tarball not found")
    return FileResponse(
        tarball,
        media_type="application/octet-stream",
        filename=tarball.name,
    )


@app.get("/plugins/install.sh", include_in_schema=False)
async def expool_plugin_install_script(request: Request):
    """Serve a self-contained intranet installer for the current plugin."""
    from fastapi import Response

    tarball = _latest_plugin_tarball()
    if tarball is None:
        raise HTTPException(status_code=404, detail="expool plugin tarball not found")
    base = _request_public_base_url(request)
    download_url = f"{base}/plugins/expool.tgz"
    meta = _plugin_tarball_metadata(tarball)
    return Response(
        content=_plugin_install_script(download_url, base, str(meta["sha256"])),
        media_type="text/x-shellscript; charset=utf-8",
    )


@app.get("/v1/plugin/package", include_in_schema=False)
async def expool_plugin_package(request: Request) -> dict[str, Any]:
    """Return current intranet plugin package metadata and install command."""
    tarball = _latest_plugin_tarball()
    if tarball is None:
        raise HTTPException(status_code=404, detail="expool plugin tarball not found")
    base = _request_public_base_url(request)
    download_url = f"{base}/plugins/expool.tgz"
    install_script_url = f"{base}/plugins/install.sh"
    meta = _plugin_tarball_metadata(tarball)
    meta.update({
        "download_url": download_url,
        "install_script_url": install_script_url,
        "install_script_command": f"curl --noproxy '*' -fsSL {shlex.quote(install_script_url)} | bash",
        "install_command": _plugin_install_command(download_url, base),
    })
    return meta


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
    if path == "/v1/users/register" and method == "POST":
        return ("user_register", _env_int("EXP_RATE_USER_REGISTER_PER_MIN", 10))
    if path == "/v1/users/login" and method == "POST":
        return ("user_login", _env_int("EXP_RATE_LOGIN_PER_MIN", 20))
    if path == "/v1/plugin/pair" and method == "POST":
        return ("plugin_pair", _env_int("EXP_RATE_PAIR_PER_MIN", 20))
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


def _extract_bearer(request: Request) -> str | None:
    raw = request.headers.get("authorization")
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token.startswith(api_keys_mod.KEY_PREFIX):
        return None
    return token


def _optional_token_response(
    request: Request,
    *,
    env_name: str,
    header_name: str,
    error: str,
) -> JSONResponse | None:
    expected = os.getenv(env_name)
    if not expected:
        return None
    got = request.headers.get(header_name, "")
    if hmac.compare_digest(got, expected):
        return None
    return _json_and_log(request, {"error": error}, 403)


def _valid_register_token(request: Request) -> bool:
    """True iff EXP_REGISTER_TOKEN is set AND the request carries a matching
    X-Register-Token. Used to authorize privileged re-registration (rotating
    an existing agent's credential), which is otherwise refused."""
    expected = os.getenv("EXP_REGISTER_TOKEN")
    if not expected:
        return False
    got = request.headers.get("x-register-token", "")
    return hmac.compare_digest(got, expected)


def _register_token_response(request: Request) -> JSONResponse | None:
    if request.url.path == "/v1/agents/register" and request.method.upper() == "POST":
        return _optional_token_response(
            request,
            env_name="EXP_REGISTER_TOKEN",
            header_name="x-register-token",
            error="registration disabled: missing or invalid X-Register-Token",
        )
    if request.url.path == "/v1/users/register" and request.method.upper() == "POST":
        return _optional_token_response(
            request,
            env_name="EXP_USER_REGISTER_TOKEN",
            header_name="x-user-register-token",
            error="user registration disabled: missing or invalid X-User-Register-Token",
        )
    return None


def _admin_token_response(request: Request) -> JSONResponse | None:
    if not request.url.path.startswith("/v1/admin/"):
        return None
    expected = os.getenv("EXP_ADMIN_TOKEN")
    if not expected:
        return _json_and_log(
            request,
            {"error": "admin endpoint disabled: set EXP_ADMIN_TOKEN"},
            403,
        )
    got = request.headers.get("x-admin-token", "")
    if hmac.compare_digest(got, expected):
        return None
    return _json_and_log(
        request,
        {"error": "admin endpoint disabled: missing or invalid X-Admin-Token"},
        403,
    )


@app.middleware("http")
async def hmac_auth(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs"):
        protected = _register_token_response(request)
        if protected is not None:
            return protected
        limited = _rate_limit_response(request)
        if limited is not None:
            _log_request(request, limited.status_code, 0)
            return limited
        return await _call_and_log(request, call_next)
    # Session-cookie auth: skip HMAC, let route validate the cookie itself.
    if _is_session_auth_path(request.url.path):
        limited = _rate_limit_response(request)
        if limited is not None:
            _log_request(request, limited.status_code, 0)
            return limited
        return await _call_and_log(request, call_next)

    # ---- Auth path A: API key (Bearer token) ----
    bearer = _extract_bearer(request)
    if bearer is not None:
        pool = _pool()
        key_row = api_keys_mod.lookup(pool.conn, bearer)
        if key_row is None:
            return _json_and_log(request, {"error": "invalid or revoked api key"}, 401)
        cred = load_credential(key_row.agent_name)
        if cred is None:
            return _json_and_log(
                request,
                {"error": f"api key references unknown agent: {key_row.agent_name}"},
                500,
            )
        request.state.agent_name = key_row.agent_name
        request.state.agent_team = cred.team
        request.state.auth_method = "api_key"
        request.state.api_key_id = key_row.key_id
        protected = _admin_token_response(request)
        if protected is not None:
            return protected
        limited = _rate_limit_response(request)
        if limited is not None:
            _log_request(request, limited.status_code, 0)
            return limited
        return await _call_and_log(request, call_next)

    # ---- Auth path B: HMAC signature ----
    name = request.headers.get("x-agent-name")
    sig = request.headers.get("x-signature")
    if not name:
        return _json_and_log(
            request,
            {"error": "missing credentials: send X-Agent-Name + X-Signature, "
                      "or Authorization: Bearer <api_key>"},
            401,
        )
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
    request.state.auth_method = "hmac"
    protected = _admin_token_response(request)
    if protected is not None:
        return protected
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
async def register(req: RegisterReq, request: Request) -> dict[str, Any]:
    pool = _pool()
    # Account-takeover guard: registering an existing agent name would
    # otherwise mint a fresh credential and let the caller reassign owner/team,
    # hijacking another agent's private/team data. Refuse re-registration
    # unless the caller presents a valid ops token (EXP_REGISTER_TOKEN), which
    # is the supported way to rotate a credential. New names register freely.
    if pool.agent_exists(req.name) and not _valid_register_token(request):
        raise HTTPException(
            status_code=409,
            detail=(
                "agent name already registered; obtain a key via web login / "
                "pairing code, or present X-Register-Token to rotate it"
            ),
        )
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
    # Reject card-only uploads by default. The pool is much less useful
    # without the raw trajectory (no tool-call replay, no judge re-run,
    # no SFT material). Operators who genuinely want lite-only ingest
    # can set EXP_REQUIRE_TRAJECTORY=0.
    if os.getenv("EXP_REQUIRE_TRAJECTORY", "1").lower() not in {"0", "false", "no"}:
        if not req.trajectory:
            raise HTTPException(
                status_code=400,
                detail="trajectory is required (set EXP_REQUIRE_TRAJECTORY=0 "
                       "on the server to allow card-only pushes)",
            )
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
    db_path = str(pool.config.db_path)
    if eid and auto_label_mod._enabled():
        background_tasks.add_task(_safe_auto_label, db_path, eid)
        result["auto_label_queued"] = True
    # Server-side title refinement: shells out to local `claude -p` to
    # generate a one-line summary of the WHOLE conversation, then
    # overwrites `intent_text` if the result looks usable. Runs as a
    # BackgroundTask so push response time isn't affected.
    if eid and title_refine_mod._enabled():
        background_tasks.add_task(_safe_refine_title, db_path, eid)
        result["title_refine_queued"] = True
    return result


def _safe_auto_label(db_path: str, eid: str) -> None:
    try:
        auto_label_mod.auto_label_experience(db_path, eid)
    except Exception as e:
        LOG.warning("auto_label failed", experience_id=eid, error=str(e)[:200])


def _safe_refine_title(db_path: str, eid: str) -> None:
    try:
        title_refine_mod.refine_title_for_experience(db_path, eid)
    except Exception as e:
        LOG.warning("title_refine failed", experience_id=eid, error=str(e)[:200])


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


# ----- email/password user auth ---------------------------------------------

from . import users as _users_mod  # noqa: E402


SESSION_COOKIE = "exp_session"


class UserRegisterReq(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class UserLoginReq(BaseModel):
    email: str
    password: str


def _public_base_url(request: Request) -> str:
    """Compute the URL the bind script should embed.

    Priority:
      1. EXP_BIND_BASE_URL env (operator-set; intranet IP / hostname)
      2. X-Forwarded-Host + X-Forwarded-Proto (set by reverse proxies)
      3. Host header on the request

    For intranet deployments where agents on other machines must curl
    a fixed IP, set EXP_BIND_BASE_URL=http://10.x.y.z:8080 — without it
    we fall back to the request Host, which is usually 127.0.0.1 when
    the UI calls us server-side (useless to other machines).
    """
    return _request_public_base_url(request)


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_falsey(value: str | None) -> bool:
    return (value or "").strip().lower() in {"0", "false", "no", "off"}


def _secure_session_cookie(request: Request) -> bool:
    configured = os.getenv("EXP_SESSION_COOKIE_SECURE")
    if _env_truthy(configured):
        return True
    if _env_falsey(configured):
        return False
    forwarded = (request.headers.get("x-forwarded-proto") or "").lower()
    if forwarded == "https":
        return True
    try:
        return _public_base_url(request).startswith("https://")
    except Exception:
        return request.url.scheme == "https"


def _set_session_cookie(resp: JSONResponse, token: str, expires: int, request: Request) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=expires - int(time.time()),
        httponly=True,
        samesite="lax",
        secure=_secure_session_cookie(request),
        path="/",
    )


@app.post("/v1/admin/refine-titles")
async def admin_refine_titles(req: dict[str, Any], request: Request) -> dict[str, Any]:
    """Backfill `intent_text` on already-uploaded experiences using local
    `claude -p`. Body:
      owner: optional email (e.g. alice.com) to scope the pass
      limit: max rows to refine in one call (default 100)
    """
    pool = _pool()
    owner = req.get("owner")
    limit = int(req.get("limit", 100))
    db_path = str(pool.config.db_path)
    return title_refine_mod.refine_titles_batch(db_path, owner=owner, limit=limit)


def _current_user_or_401(request: Request) -> _users_mod.User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not logged in")
    pool = _pool()
    user = _users_mod.lookup_session(pool.conn, token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return user


@app.post("/v1/users/register")
async def user_register(req: UserRegisterReq, request: Request) -> JSONResponse:
    pool = _pool()
    try:
        user, cred = _users_mod.register_user(
            pool.conn,
            email=req.email,
            password=req.password,
            display_name=req.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Auto-login: issue a session so the client can hit /me/bind-script
    # immediately without a separate login step.
    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    token, expires = _users_mod.create_session(
        pool.conn, user_id=user.user_id, user_agent=ua, ip=ip,
    )
    base = _public_base_url(request)
    bind_cmd = _users_mod.render_bind_script(
        base_url=base, agent_name=cred["agent_name"], secret=cred["secret"],
        team=cred.get("team", "default"), agent_id=cred.get("agent_id"),
    )
    body = {
        "user_id": user.user_id,
        "email": user.email,
        "default_agent_name": user.default_agent_name,
        "agent_id": cred["agent_id"],
        "secret": cred["secret"],
        "bind_command": bind_cmd,
        "session_expires_at": expires,
    }
    resp = JSONResponse(body, status_code=201)
    _set_session_cookie(resp, token, expires, request)
    return resp


@app.post("/v1/users/login")
async def user_login(req: UserLoginReq, request: Request) -> JSONResponse:
    pool = _pool()
    try:
        email = _users_mod.validate_email(req.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    user = _users_mod.authenticate(pool.conn, email=email, password=req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    token, expires = _users_mod.create_session(
        pool.conn, user_id=user.user_id, user_agent=ua, ip=ip,
    )
    body = {
        "user_id": user.user_id,
        "email": user.email,
        "default_agent_name": user.default_agent_name,
        "session_expires_at": expires,
    }
    resp = JSONResponse(body)
    _set_session_cookie(resp, token, expires, request)
    return resp


@app.post("/v1/users/logout")
async def user_logout(request: Request) -> JSONResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        pool = _pool()
        _users_mod.revoke_session(pool.conn, token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/v1/users/me")
async def user_me(request: Request) -> dict[str, Any]:
    user = _current_user_or_401(request)
    return {
        "user_id": user.user_id,
        "email": user.email,
        "default_agent_name": user.default_agent_name,
        "display_name": user.display_name,
    }


@app.get("/v1/users/me/bind-script")
async def user_bind_script(request: Request) -> dict[str, Any]:
    user = _current_user_or_401(request)
    cred = _users_mod.get_credential_for_user(user)
    if cred is None:
        raise HTTPException(status_code=500,
                            detail="credential file missing — re-register or contact admin")
    base = _public_base_url(request)
    cmd = _users_mod.render_bind_script(
        base_url=base, agent_name=cred["agent_name"], secret=cred["secret"],
        team=cred.get("team", "default"),
    )
    # Backfill extractor command — same identity/secret, but goes to a
    # different one-shot tool that scrapes ~/.claude / ~/.codex etc. and
    # uploads each session privately. acl=private is hardcoded in the
    # Python script; users physically cannot make these uploads public
    # via this command.
    extract_cmd = (
        f"curl -fsSL {base.rstrip('/')}/session-extractor/run.sh | "
        f"EXP_AGENT_NAME='{cred['agent_name']}' "
        f"EXP_AGENT_SECRET='{cred['secret']}' "
        f"EXP_BASE_URL='{base.rstrip('/')}' "
        f"bash"
    )
    return {
        "agent_name": cred["agent_name"],
        "agent_id": cred["agent_id"],
        "team": cred.get("team", "default"),
        "secret_hint": cred["secret"][:6] + "..." + cred["secret"][-4:],
        "bind_command": cmd,
        "extract_command": extract_cmd,
        "base_url": base,
    }


# ----- API keys (Bearer-token auth) -----------------------------------------


class ApiKeyCreateReq(BaseModel):
    name: str = Field(..., description="Human label, max 64 chars")


class PairingCodeCreateReq(BaseModel):
    name: str = Field("plugin-pairing", description="Human label, max 64 chars")
    ttl_seconds: int = Field(
        api_keys_mod.PAIRING_CODE_DEFAULT_TTL_SECONDS,
        ge=60,
        le=api_keys_mod.PAIRING_CODE_MAX_TTL_SECONDS,
        description="Pairing code lifetime in seconds",
    )


class PairingCodeClaimReq(BaseModel):
    code: str = Field(..., description="One-time code from the web portal")
    agent_name: str = Field("", description="Optional local agent label override")


@app.get("/v1/users/me/api-keys", tags=["api-keys"])
async def api_keys_list(request: Request) -> dict[str, Any]:
    """List all API keys belonging to the current user (cookie-authenticated).

    Raw key material is **not** returned — only the prefix, name, and
    timestamps. Use POST to mint a new key.
    """
    user = _current_user_or_401(request)
    pool = _pool()
    rows = api_keys_mod.list_for_user(pool.conn, user.user_id)
    return {"keys": [r.to_dict() for r in rows]}


@app.post("/v1/users/me/api-keys", status_code=201, tags=["api-keys"])
async def api_keys_create(req: ApiKeyCreateReq, request: Request) -> dict[str, Any]:
    """Mint a new API key for the current user.

    The raw key (``expk_...``) is returned **once** in the response body.
    Save it now — it cannot be retrieved later. Future calls only see the
    prefix.
    """
    user = _current_user_or_401(request)
    pool = _pool()
    try:
        raw, row = api_keys_mod.create(
            pool.conn,
            user_id=user.user_id,
            agent_name=user.default_agent_name,
            name=req.name,
        )
    except api_keys_mod.ApiKeyError as exc:
        status = 409 if exc.code == "quota_exceeded" else 400
        raise HTTPException(status_code=status, detail=exc.message)
    payload = row.to_dict()
    payload["api_key"] = raw
    payload["warning"] = "save this key now — it will not be shown again"
    return payload


@app.delete("/v1/users/me/api-keys/{key_id}", tags=["api-keys"])
async def api_keys_revoke(key_id: str, request: Request) -> dict[str, Any]:
    """Soft-revoke an API key. Subsequent requests using it will return 401."""
    user = _current_user_or_401(request)
    pool = _pool()
    ok = api_keys_mod.revoke(pool.conn, key_id=key_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="api key not found or already revoked")
    return {"ok": True, "key_id": key_id}


@app.post("/v1/users/me/pairing-codes", status_code=201, tags=["api-keys"])
async def pairing_codes_create(req: PairingCodeCreateReq, request: Request) -> dict[str, Any]:
    """Create a short-lived one-time code for plugin binding.

    The browser receives only the pairing code. The plugin exchanges that code
    for a newly minted API key through /v1/plugin/pair, and stores the key
    locally without printing it into the chat transcript.
    """
    user = _current_user_or_401(request)
    pool = _pool()
    try:
        raw_code, row = api_keys_mod.create_pairing_code(
            pool.conn,
            user_id=user.user_id,
            agent_name=user.default_agent_name,
            name=req.name,
            ttl_seconds=req.ttl_seconds,
        )
    except api_keys_mod.ApiKeyError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    payload = row.to_dict()
    payload["code"] = raw_code
    payload["warning"] = "pairing code is one-time and expires soon"
    return payload


@app.post("/v1/plugin/pair", tags=["api-keys"])
async def plugin_pair(req: PairingCodeClaimReq) -> dict[str, Any]:
    """Exchange a one-time pairing code for a local plugin API key."""
    pool = _pool()
    try:
        raw, row = api_keys_mod.claim_pairing_code(
            pool.conn,
            code=req.code,
            agent_name=req.agent_name,
        )
    except api_keys_mod.ApiKeyError as exc:
        status = 409 if exc.code in {"already_claimed", "quota_exceeded"} else 400
        raise HTTPException(status_code=status, detail=exc.message)
    payload = row.to_dict()
    payload["api_key"] = raw
    payload["warning"] = "save locally only — this key will not be shown again"
    return payload


# ----- API protocol doc + OpenAPI security schemes --------------------------


# core/exp_core/server.py → parents[2] = experience-pool repo root.
_API_PROTOCOL_PATH = Path(__file__).resolve().parents[2] / "API_PROTOCOL.md"


@app.get("/api-protocol", include_in_schema=False)
async def api_protocol_doc() -> "JSONResponse":
    """Serve the human-readable API protocol doc. Public, no auth."""
    from fastapi.responses import PlainTextResponse
    if not _API_PROTOCOL_PATH.is_file():
        return PlainTextResponse(
            "API protocol doc not bundled with this build.\n"
            "See /docs for the auto-generated OpenAPI reference.\n",
            media_type="text/plain; charset=utf-8",
        )
    return PlainTextResponse(
        _API_PROTOCOL_PATH.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


# Mark /api-protocol and the user-facing OpenAPI page as public so the auth
# middleware lets them through without credentials.
PUBLIC_PATHS.add("/api-protocol")


def _customize_openapi() -> dict[str, Any]:
    """Inject Bearer + HMAC security schemes so Swagger UI renders auth boxes."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=(
            "Experience-pool HTTP API.\n\n"
            "**Three auth modes** — pick whichever fits your client:\n\n"
            "1. **Bearer API key** (recommended for bots / LLM agents). "
            "Send `Authorization: Bearer expk_...`. Mint a key at "
            "`POST /v1/users/me/api-keys` after logging in.\n"
            "2. **HMAC** (legacy / installer-generated). Send "
            "`X-Agent-Name` + `X-Signature` (HMAC-SHA256 over "
            "`METHOD\\nPATH[?QUERY]\\nBODY`).\n"
            "3. **Session cookie** (web UI only). `exp_session` cookie "
            "set by `/v1/users/login`.\n\n"
            "See `/api-protocol` for the full markdown protocol."
        ),
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update({
        "BearerApiKey": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "expk_<32 random chars>",
            "description": (
                "User-scoped API key. Mint via POST /v1/users/me/api-keys."
            ),
        },
        "HMACSignature": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Signature",
            "description": (
                "HMAC-SHA256 of METHOD\\nPATH[?QUERY]\\nBODY using the "
                "agent secret. Must be paired with X-Agent-Name. Issued "
                "via /v1/agents/register or the installer."
            ),
        },
        "SessionCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": "exp_session",
            "description": "Set by /v1/users/login. Web UI use only.",
        },
    })
    # Default: every non-public route accepts Bearer OR HMAC.
    public_set = PUBLIC_PATHS | {"/docs", "/redoc", "/openapi.json", "/api-protocol"}
    session_only = {"/v1/users/me", "/v1/users/me/bind-script",
                    "/v1/users/logout", "/v1/users/me/api-keys",
                    "/v1/users/me/pairing-codes"}
    for path, ops in (schema.get("paths") or {}).items():
        if path in public_set or any(path.startswith(p + "/") for p in public_set):
            continue
        for method, op in ops.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if path in session_only or path.startswith("/v1/users/me/api-keys") \
               or path.startswith("/v1/users/me/pairing-codes"):
                op["security"] = [{"SessionCookie": []}]
            else:
                op["security"] = [
                    {"BearerApiKey": []},
                    {"HMACSignature": []},
                ]
    app.openapi_schema = schema
    return schema


app.openapi = _customize_openapi  # type: ignore[assignment]

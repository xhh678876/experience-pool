#!/usr/bin/env bash
# Babysit the experience-pool services. Runs an infinite probe loop;
# any 500 / 000 / dead process triggers a restart of the offending
# component. Designed to run under nohup; logs to /tmp/babysit.log.
#
# Components watched are configured by config/env.sh.
#
# Usage:
#   nohup bash scripts/babysit.sh >/tmp/babysit.log 2>&1 &
#   disown
#
# Stop:
#   pkill -f scripts/babysit.sh

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXP_ENV="${EXP_ENV:-production}"
# shellcheck disable=SC1091
. "$ROOT/config/env.sh"

FLEET_DIR="${CLAUDE_FLEET_DIR:-$EXP_FLEET_REPO}"
FLEET_ENABLED="$EXP_FLEET_ENABLED"
INTERVAL="${BABYSIT_INTERVAL:-15}"
PROBE_TIMEOUT="${BABYSIT_PROBE_TIMEOUT:-6}"
UI_PROBE_TIMEOUT="${BABYSIT_UI_PROBE_TIMEOUT:-15}"
THRESHOLD="${BABYSIT_FAIL_THRESHOLD:-3}"
UI_THRESHOLD="${BABYSIT_UI_FAIL_THRESHOLD:-6}"
RAG_MAINTENANCE_INTERVAL="${EXP_RAG_MAINTENANCE_INTERVAL:-300}"
RAG_MAINTENANCE_MARKER="$EXP_RAG_MARKER"
LOG="$EXP_BABYSIT_LOG"
LOCAL_UI_ORIGIN="${EXP_LOCAL_UI_ORIGIN:-$EXP_UI_ORIGIN}"

mkdir -p "$EXP_ROOT" "$(dirname "$EXP_DB_PATH")" "$EXP_TRAJECTORIES_DIR" \
    "$EXP_RUNTIME_DIR" "$(dirname "$LOG")" "$(dirname "$EXP_SERVER_LOG")" \
    "$(dirname "$EXP_RAG_LOG")" "$(dirname "$EXP_GATEWAY_LOG")" \
    "$(dirname "$EXP_UI_LOG")"

built_ui_url() {
    python3 - "$ROOT/ui/.next/required-server-files.json" <<'PY' 2>/dev/null || true
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        value = json.load(fh).get("config", {}).get("assetPrefix", "")
except Exception:
    value = ""
if isinstance(value, str) and value.startswith(("http://", "https://")):
    print(value.rstrip("/"))
PY
}

gateway_from_ui_url() {
    python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import re
import sys

ui = (sys.argv[1] or "").rstrip("/")
port = sys.argv[2]
m = re.match(r"^(.*)/proxy/\d+/?$", ui)
if m:
    print(f"{m.group(1)}/proxy/{port}")
PY
}

UI_PUBLIC_URL="${EXP_UI_PUBLIC_URL:-$(built_ui_url)}"
UI_PUBLIC_URL="${UI_PUBLIC_URL:-$EXP_UI_ORIGIN}"
# The notebook's public proxy does not reliably support hairpin requests from
# this host. Probe the local process so a healthy UI is not rebuilt in a loop.
UI_HEALTH_URL="$EXP_UI_HEALTH_URL"
DERIVED_API_PUBLIC_URL="$(gateway_from_ui_url "$UI_PUBLIC_URL" "$EXP_GATEWAY_PORT")"
API_PUBLIC_URL="${EXP_PUBLIC_BASE_URL:-${EXP_PUBLIC_API_BASE:-${DERIVED_API_PUBLIC_URL:-$EXP_GATEWAY_ORIGIN}}}"
if [ -n "${EXP_BIND_BASE_URL:-}" ]; then
    case "$EXP_BIND_BASE_URL" in
        http://127.0.0.1*|http://localhost*|http://0.0.0.0*)
            API_PUBLIC_URL="${DERIVED_API_PUBLIC_URL:-$EXP_BIND_BASE_URL}"
            ;;
        *)
            API_PUBLIC_URL="$EXP_BIND_BASE_URL"
            ;;
    esac
fi

RUNTIME_ENV="$EXP_RUNTIME_ENV"
generate_runtime_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        python3 -c 'import secrets; print(secrets.token_hex(32))'
    fi
}

load_runtime_secrets() {
    local env_register="${EXP_REGISTER_TOKEN:-}"
    local env_user_register="${EXP_USER_REGISTER_TOKEN:-}"
    local env_admin="${EXP_ADMIN_TOKEN:-}"
    if [ -f "$RUNTIME_ENV" ]; then
        # This file is generated below with hex-only values and mode 0600.
        # shellcheck disable=SC1090
        . "$RUNTIME_ENV"
    fi
    EXP_REGISTER_TOKEN="${env_register:-${EXP_REGISTER_TOKEN:-$(generate_runtime_secret)}}"
    EXP_USER_REGISTER_TOKEN="${env_user_register:-${EXP_USER_REGISTER_TOKEN:-$(generate_runtime_secret)}}"
    EXP_ADMIN_TOKEN="${env_admin:-${EXP_ADMIN_TOKEN:-$(generate_runtime_secret)}}"
    mkdir -p "$(dirname "$RUNTIME_ENV")"
    umask 077
    {
        printf 'EXP_REGISTER_TOKEN=%s\n' "$EXP_REGISTER_TOKEN"
        printf 'EXP_USER_REGISTER_TOKEN=%s\n' "$EXP_USER_REGISTER_TOKEN"
        printf 'EXP_ADMIN_TOKEN=%s\n' "$EXP_ADMIN_TOKEN"
    } >"$RUNTIME_ENV"
    chmod 600 "$RUNTIME_ENV"
    export EXP_REGISTER_TOKEN EXP_USER_REGISTER_TOKEN EXP_ADMIN_TOKEN
}

load_runtime_secrets

log() { printf '[%s] %s\n' "$(date +%FT%T)" "$*" >>"$LOG"; }

maybe_start_rag_maintenance() {
    local now last=0
    now=$(date +%s)
    if [ -f "$RAG_MAINTENANCE_MARKER" ]; then
        last=$(stat -c %Y "$RAG_MAINTENANCE_MARKER" 2>/dev/null || echo 0)
    fi
    if [ $((now - last)) -lt "$RAG_MAINTENANCE_INTERVAL" ]; then
        return
    fi
    if pgrep -f "scripts/rag_maintenance.py.*--db $EXP_DB_PATH" >/dev/null 2>&1; then
        return
    fi
    log "starting incremental RAG maintenance"
    (
        cd "$ROOT"
        if PYTHONPATH="$ROOT/core" core/.venv/bin/python scripts/rag_maintenance.py \
            --db "$EXP_DB_PATH" --batch-size 500 \
            >>"$EXP_RAG_LOG" 2>&1; then
            touch "$RAG_MAINTENANCE_MARKER"
        fi
    ) &
}

ui_source_fingerprint() {
    (
        cd "$ROOT/ui" || exit 1
        printf 'EXP_UI_PUBLIC_URL=%s\n' "$UI_PUBLIC_URL"
        printf 'EXP_API_BASE=%s\n' "$EXP_API_ORIGIN"
        find app components lib public -type f -print 2>/dev/null | sort | while IFS= read -r file; do
            sha256sum "$file"
        done
        for file in package.json package-lock.json tsconfig.json next.config.* postcss.config.* tailwind.config.*; do
            [ -f "$file" ] && sha256sum "$file"
        done
    ) | sha256sum | awk '{print $1}'
}

ui_build_is_stale() {
    local source_fingerprint built_fingerprint=""
    source_fingerprint="$(ui_source_fingerprint)"
    if [ -f "$ROOT/ui/.next/.source-fingerprint" ]; then
        built_fingerprint="$(cat "$ROOT/ui/.next/.source-fingerprint")"
    fi
    [ ! -f "$ROOT/ui/.next/BUILD_ID" ] || [ "$source_fingerprint" != "$built_fingerprint" ]
}

# ---------- start commands ---------------------------------------------------

start_api() {
    log "starting $EXP_API_PORT (FastAPI, --workers $EXP_API_WORKERS)"
    cd "$ROOT"
    # EXP_BIND_BASE_URL is embedded into portal bind commands. Set it to
    # a browser-reachable API/gateway URL in production; local default is 3080.
    EXP_AUTO_UPLOAD="$EXP_AUTO_UPLOAD" EXP_LLM="$EXP_LLM" \
    EXP_RATE_LIMIT_ENABLED="$EXP_RATE_LIMIT_ENABLED" \
    EXP_ROOT="$EXP_ROOT" EXP_DB_PATH="$EXP_DB_PATH" \
    EXP_TRAJECTORIES_DIR="$EXP_TRAJECTORIES_DIR" \
    EXP_CREDENTIALS_DIR="$EXP_CREDENTIALS_DIR" \
    EXP_BIND_BASE_URL="$API_PUBLIC_URL" \
    EXP_DEFER_OPF="$EXP_DEFER_OPF" \
    nohup core/.venv/bin/uvicorn exp_core.server:app \
        --host "$EXP_SERVICE_HOST" --port "$EXP_API_PORT" --app-dir core \
        --workers "$EXP_API_WORKERS" \
        >"$EXP_SERVER_LOG" 2>&1 &
    disown
    sleep 3
}

start_gateway() {
    log "starting $EXP_GATEWAY_PORT (gateway)"
    cd "$ROOT"
    EXP_GATEWAY_HOST="$EXP_GATEWAY_HOST" EXP_GATEWAY_PORT="$EXP_GATEWAY_PORT" \
    EXP_API_ORIGIN="$EXP_API_ORIGIN" EXP_UI_ORIGIN="$LOCAL_UI_ORIGIN" \
    nohup node scripts/local-gateway.mjs >"$EXP_GATEWAY_LOG" 2>&1 &
    disown
    sleep 2
}

start_ui() {
    log "starting $EXP_UI_PORT (next start)"
    cd "$ROOT/ui"
    local source_fingerprint built_fingerprint=""
    source_fingerprint="$(ui_source_fingerprint)"
    if [ -f "$ROOT/ui/.next/.source-fingerprint" ]; then
        built_fingerprint="$(cat "$ROOT/ui/.next/.source-fingerprint")"
    fi
    if [ ! -f "$ROOT/ui/.next/BUILD_ID" ] || [ "$source_fingerprint" != "$built_fingerprint" ]; then
        log "$EXP_UI_PORT source or public URL changed — running next build"
        EXP_ROOT="$EXP_ROOT" EXP_DB_PATH="$EXP_DB_PATH" \
        EXP_TRAJECTORIES_DIR="$EXP_TRAJECTORIES_DIR" \
        EXP_UI_PUBLIC_URL="$UI_PUBLIC_URL" \
        EXP_API_BASE="$EXP_API_ORIGIN" \
        npm run build >>"$EXP_UI_LOG" 2>&1 || {
            log "$EXP_UI_PORT build failed; see $EXP_UI_LOG"
            return
        }
        printf '%s\n' "$source_fingerprint" >"$ROOT/ui/.next/.source-fingerprint"
    else
        log "$EXP_UI_PORT build matches current source — skipping next build"
    fi
    EXP_ROOT="$EXP_ROOT" EXP_DB_PATH="$EXP_DB_PATH" \
    EXP_TRAJECTORIES_DIR="$EXP_TRAJECTORIES_DIR" \
    EXP_UI_PUBLIC_URL="$UI_PUBLIC_URL" \
    EXP_API_BASE="$EXP_API_ORIGIN" \
    nohup node node_modules/.bin/next start -p "$EXP_UI_PORT" -H "$EXP_SERVICE_HOST" \
        >"$EXP_UI_LOG" 2>&1 &
    disown
    sleep 4
}

start_fleet() {
    log "starting $EXP_FLEET_PORT (claude-fleet)"
    if [ ! -d "$FLEET_DIR" ]; then
        log "claude-fleet dir missing: $FLEET_DIR — skip"
        return
    fi
    cd "$FLEET_DIR"
    # 单进程、无 --reload：被看护时进程树干净，pkill 能彻底杀掉再重起。
    # venv 缺失时先用 run.sh bootstrap 一次（它会建 .venv + pip install）。
    if [ ! -x ".venv/bin/uvicorn" ]; then
        log "claude-fleet venv missing — bootstrapping via run.sh once"
        nohup bash run.sh >/tmp/fleet.log 2>&1 &
        disown
        sleep 5
        return
    fi
    nohup .venv/bin/uvicorn app:app --host "$EXP_FLEET_HOST" --port "$EXP_FLEET_PORT" \
        >/tmp/fleet.log 2>&1 &
    disown
    sleep 3
}

# ---------- probes -----------------------------------------------------------

probe() {
    # $1 = url, $2 = expected codes (space-separated, ok-list)
    # Optional: probe --timeout 15 <url> <ok-code>...
    local timeout="$PROBE_TIMEOUT"
    if [ "${1:-}" = "--timeout" ]; then
        timeout="$2"
        shift 2
    fi
    local url="$1"; shift
    local code
    if ! code=$(curl -s -m "$timeout" -o /dev/null -w "%{http_code}" "$url" 2>/dev/null); then
        code=000
    fi
    for ok in "$@"; do
        if [ "$code" = "$ok" ]; then echo "ok"; return; fi
    done
    echo "fail($code)"
}

listener_pids() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null \
            | awk -v port=":$port" '$4 ~ port "$" { print $0 }' \
            | grep -oP 'pid=\K[0-9]+' \
            | sort -u
        return
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u
    fi
}

kill_port_listeners() {
    local port="$1" pid
    while IFS= read -r pid; do
        [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null
    done < <(listener_pids "$port")
}

# ---------- main loop --------------------------------------------------------

log "babysit started, profile=$EXP_ENV interval=${INTERVAL}s root=$EXP_ROOT"

# Consecutive-failure thresholds — only restart after N failures in a
# row. Prevents killing a worker mid-slow-push (single push can take
# 30s on this hw, longer than one probe interval).
declare -A FAILS

needs_restart() {
    local key="$1" status="$2" threshold="${3:-$THRESHOLD}"
    if [ "$status" = "ok" ]; then
        FAILS[$key]=0
        return 1
    fi
    FAILS[$key]=$((${FAILS[$key]:-0} + 1))
    [ "${FAILS[$key]}" -ge "$threshold" ]
}

start_missing_services() {
    if [ "$(probe "$EXP_API_ORIGIN/healthz" 200)" != "ok" ]; then
        log "$EXP_API_PORT absent at startup — starting immediately"
        start_api
    fi
    if [ "$(probe "$EXP_GATEWAY_ORIGIN/healthz" 200)" != "ok" ]; then
        log "$EXP_GATEWAY_PORT absent at startup — starting immediately"
        start_gateway
    fi
    if [ "$(probe --timeout "$UI_PROBE_TIMEOUT" "$UI_HEALTH_URL" 200 307 308)" != "ok" ]; then
        log "$EXP_UI_PORT absent at startup — starting immediately"
        start_ui
    elif ui_build_is_stale; then
        log "$EXP_UI_PORT build stale at startup — rebuilding immediately"
        kill_port_listeners "$EXP_UI_PORT"
        start_ui
    fi
}

start_missing_services

while true; do
    api_status=$(probe "$EXP_API_ORIGIN/healthz" 200)
    if needs_restart api "$api_status"; then
        log "$EXP_API_PORT unhealthy ${FAILS[api]} consecutive — restarting"
        pkill -KILL -f "uvicorn exp_core.server:app.*--port $EXP_API_PORT" 2>/dev/null
        sleep 1
        start_api
        FAILS[api]=0
    fi

    gateway_status=$(probe "$EXP_GATEWAY_ORIGIN/healthz" 200)
    if needs_restart gateway "$gateway_status"; then
        log "$EXP_GATEWAY_PORT unhealthy ${FAILS[gateway]} consecutive — restarting gateway"
        pkill -KILL -f "node scripts/local-gateway.mjs" 2>/dev/null
        sleep 1
        start_gateway
        FAILS[gateway]=0
    fi

    if ui_build_is_stale; then
        ui_status="fail(stale-build)"
        FAILS[ui]="$UI_THRESHOLD"
    else
        ui_status=$(probe --timeout "$UI_PROBE_TIMEOUT" "$UI_HEALTH_URL" 200 307 308)
    fi
    if needs_restart ui "$ui_status" "$UI_THRESHOLD"; then
        log "$EXP_UI_PORT unhealthy or stale ${FAILS[ui]} consecutive — restarting next start"
        kill_port_listeners "$EXP_UI_PORT"
        sleep 1
        start_ui
        FAILS[ui]=0
    fi

    if [ "$FLEET_ENABLED" = "1" ] || [ "$FLEET_ENABLED" = "true" ]; then
        # claude-fleet monitor is optional and local-only by default.
        fleet_status=$(probe "http://$EXP_FLEET_HOST:$EXP_FLEET_PORT/api/windows" 200)
        if needs_restart fleet "$fleet_status"; then
            log "$EXP_FLEET_PORT unhealthy ${FAILS[fleet]} consecutive — restarting claude-fleet"
            fleet_pids=$( { ss -ltnp 2>/dev/null | grep ":$EXP_FLEET_PORT" | grep -oP 'pid=\K[0-9]+'; pgrep -f 'uvicorn app:app'; } | sort -u)
            for fp in $fleet_pids; do kill -KILL "$fp" 2>/dev/null; done
            pkill -KILL -f 'watchfiles' 2>/dev/null
            sleep 1
            start_fleet
            FAILS[fleet]=0
        fi
    fi

    maybe_start_rag_maintenance

    sleep "$INTERVAL"
done

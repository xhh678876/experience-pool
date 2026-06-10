#!/usr/bin/env bash
# Babysit the experience-pool services. Runs an infinite probe loop;
# any 500 / 000 / dead process triggers a restart of the offending
# component. Designed to run under nohup; logs to /tmp/babysit.log.
#
# Components watched:
#   8081  FastAPI (exp_core.server:app)
#   3080  local-gateway.mjs (uniform UI+API entry)
#   3002  Next.js production server (UI)
#
# Usage:
#   nohup bash scripts/babysit.sh >/tmp/babysit.log 2>&1 &
#   disown
#
# Stop:
#   pkill -f scripts/babysit.sh

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# claude-fleet 仓在 experience-pool 的同级目录（可用 CLAUDE_FLEET_DIR 覆盖）。
FLEET_DIR="${CLAUDE_FLEET_DIR:-$(cd "$ROOT/.." && pwd)/claude-fleet}"
FLEET_ENABLED="${EXP_FLEET_ENABLED:-0}"
INTERVAL="${BABYSIT_INTERVAL:-15}"
LOG=/tmp/babysit.log
UI_PUBLIC_URL="${EXP_UI_PUBLIC_URL:-http://127.0.0.1:3002}"
API_PUBLIC_URL="${EXP_BIND_BASE_URL:-${EXP_PUBLIC_BASE_URL:-${EXP_PUBLIC_API_BASE:-http://127.0.0.1:3080}}}"

log() { printf '[%s] %s\n' "$(date +%FT%T)" "$*" >>"$LOG"; }

# ---------- start commands ---------------------------------------------------

start_8081() {
    log "starting 8081 (FastAPI, --workers 4)"
    cd "$ROOT"
    # EXP_BIND_BASE_URL is embedded into portal bind commands. Set it to
    # a browser-reachable API/gateway URL in production; local default is 3080.
    EXP_AUTO_UPLOAD=1 EXP_LLM=mock EXP_RATE_LIMIT_ENABLED=0 \
    EXP_ROOT="$ROOT/.experience-pool" \
    EXP_BIND_BASE_URL="$API_PUBLIC_URL" \
    EXP_DEFER_OPF=1 \
    nohup core/.venv/bin/uvicorn exp_core.server:app \
        --host 0.0.0.0 --port 8081 --app-dir core \
        --workers 4 \
        >"$ROOT/.experience-pool/server.log" 2>&1 &
    disown
    sleep 3
}

start_3080() {
    log "starting 3080 (gateway)"
    cd "$ROOT"
    EXP_GATEWAY_HOST=0.0.0.0 EXP_GATEWAY_PORT=3080 \
    EXP_API_ORIGIN=http://127.0.0.1:8081 EXP_UI_ORIGIN=http://127.0.0.1:3002 \
    nohup node scripts/local-gateway.mjs >/tmp/gateway.log 2>&1 &
    disown
    sleep 2
}

start_3002() {
    log "starting 3002 (next start)"
    cd "$ROOT/ui"
    if [ ! -f "$ROOT/ui/.next/BUILD_ID" ]; then
        log "3002 build missing — running next build"
        EXP_DB_PATH="$ROOT/.experience-pool/pool.db" \
        EXP_UI_PUBLIC_URL="$UI_PUBLIC_URL" \
        EXP_API_BASE=http://127.0.0.1:8081 \
        npm run build >>/tmp/next-3002-stable.log 2>&1 || {
            log "3002 build failed; see /tmp/next-3002-stable.log"
            return
        }
    fi
    EXP_DB_PATH="$ROOT/.experience-pool/pool.db" \
    EXP_UI_PUBLIC_URL="$UI_PUBLIC_URL" \
    EXP_API_BASE=http://127.0.0.1:8081 \
    nohup node node_modules/.bin/next start -p 3002 -H 0.0.0.0 \
        >/tmp/next-3002-stable.log 2>&1 &
    disown
    sleep 4
}

start_7878() {
    log "starting 7878 (claude-fleet)"
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
    nohup .venv/bin/uvicorn app:app --host 127.0.0.1 --port 7878 \
        >/tmp/fleet.log 2>&1 &
    disown
    sleep 3
}

# ---------- probes -----------------------------------------------------------

probe() {
    # $1 = url, $2 = expected codes (space-separated, ok-list)
    local url="$1"; shift
    local code
    code=$(curl -s -m 6 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo 000)
    for ok in "$@"; do
        if [ "$code" = "$ok" ]; then echo "ok"; return; fi
    done
    echo "fail($code)"
}

# ---------- main loop --------------------------------------------------------

log "babysit started, interval=${INTERVAL}s"

# Consecutive-failure thresholds — only restart after N failures in a
# row. Prevents killing a worker mid-slow-push (single push can take
# 30s on this hw, longer than one probe interval).
declare -A FAILS
THRESHOLD=3

needs_restart() {
    local key="$1" status="$2"
    if [ "$status" = "ok" ]; then
        FAILS[$key]=0
        return 1
    fi
    FAILS[$key]=$((${FAILS[$key]:-0} + 1))
    [ "${FAILS[$key]}" -ge "$THRESHOLD" ]
}

while true; do
    p8081=$(probe http://127.0.0.1:8081/healthz 200)
    if needs_restart 8081 "$p8081"; then
        log "8081 unhealthy ${FAILS[8081]} consecutive — restarting"
        pkill -KILL -f "uvicorn exp_core.server:app.*--port 8081" 2>/dev/null
        sleep 1
        start_8081
        FAILS[8081]=0
    fi

    p3080=$(probe http://127.0.0.1:3080/healthz 200)
    if needs_restart 3080 "$p3080"; then
        log "3080 unhealthy ${FAILS[3080]} consecutive — restarting gateway"
        pkill -KILL -f "node scripts/local-gateway.mjs" 2>/dev/null
        sleep 1
        start_3080
        FAILS[3080]=0
    fi

    p3002=$(probe http://127.0.0.1:3002/login 200 307)
    if needs_restart 3002 "$p3002"; then
        log "3002 unhealthy ${FAILS[3002]} consecutive — clearing .next + restarting"
        pkill -KILL -f "next-server" 2>/dev/null
        pkill -KILL -f "node node_modules/.bin/next dev" 2>/dev/null
        pkill -KILL -f "node node_modules/.bin/next start" 2>/dev/null
        sleep 1
        start_3002
        FAILS[3002]=0
    fi

    if [ "$FLEET_ENABLED" = "1" ] || [ "$FLEET_ENABLED" = "true" ]; then
        # claude-fleet monitor is optional and local-only by default.
        p7878=$(probe http://127.0.0.1:7878/api/windows 200)
        if needs_restart 7878 "$p7878"; then
            log "7878 unhealthy ${FAILS[7878]} consecutive — restarting claude-fleet"
            fleet_pids=$( { ss -ltnp 2>/dev/null | grep ':7878' | grep -oP 'pid=\K[0-9]+'; pgrep -f 'uvicorn app:app'; } | sort -u)
            for fp in $fleet_pids; do kill -KILL "$fp" 2>/dev/null; done
            pkill -KILL -f 'watchfiles' 2>/dev/null
            sleep 1
            start_7878
            FAILS[7878]=0
        fi
    fi

    sleep "$INTERVAL"
done

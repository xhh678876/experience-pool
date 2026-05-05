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
INTERVAL="${BABYSIT_INTERVAL:-15}"
LOG=/tmp/babysit.log
UI_PUBLIC_URL="${EXP_UI_PUBLIC_URL:-https://nat2-notebook-inspire.sii.edu.cn/ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b/project-969649d6-31b8-45af-b6ff-ffb85bbfb3c9/user-ef4936dd-0231-4485-ba30-34e92bf3ea53/vscode/6bf937f8-4826-43cd-b0f6-54f30c688f96/a5119654-19ab-4e0d-9527-bb73a246b9a8/proxy/3002}"

log() { printf '[%s] %s\n' "$(date +%FT%T)" "$*" >>"$LOG"; }

# ---------- start commands ---------------------------------------------------

start_8081() {
    log "starting 8081 (FastAPI, --workers 4)"
    cd "$ROOT"
    EXP_AUTO_UPLOAD=1 EXP_LLM=mock EXP_RATE_LIMIT_ENABLED=0 \
    EXP_ROOT=/tmp/exp-mvp \
    EXP_BIND_BASE_URL=http://10.244.66.195:3080 \
    EXP_DEFER_OPF=1 \
    nohup core/.venv/bin/uvicorn exp_core.server:app \
        --host 0.0.0.0 --port 8081 --app-dir core \
        --workers 4 \
        >/tmp/exp-mvp/server.log 2>&1 &
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
        EXP_DB_PATH=/tmp/exp-mvp/pool.db \
        EXP_DEFAULT_REVIEWER=alice \
        EXP_UI_PUBLIC_URL="$UI_PUBLIC_URL" \
        EXP_API_BASE=http://127.0.0.1:8081 \
        npm run build >>/tmp/next-3002-stable.log 2>&1 || {
            log "3002 build failed; see /tmp/next-3002-stable.log"
            return
        }
    fi
    EXP_DB_PATH=/tmp/exp-mvp/pool.db \
    EXP_DEFAULT_REVIEWER=alice \
    EXP_UI_PUBLIC_URL="$UI_PUBLIC_URL" \
    EXP_API_BASE=http://127.0.0.1:8081 \
    nohup node node_modules/.bin/next start -p 3002 -H 0.0.0.0 \
        >/tmp/next-3002-stable.log 2>&1 &
    disown
    sleep 4
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

    sleep "$INTERVAL"
done

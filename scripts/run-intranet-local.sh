#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXP_ROOT="${EXP_ROOT:-/tmp/chuangzhi-expool}"
EXP_DB_PATH="${EXP_DB_PATH:-$EXP_ROOT/pool.db}"
EXP_SERVICE_HOST="${EXP_SERVICE_HOST:-127.0.0.1}"
EXP_GATEWAY_HOST="${EXP_GATEWAY_HOST:-0.0.0.0}"
EXP_API_PORT="${EXP_API_PORT:-8080}"
EXP_UI_PORT="${EXP_UI_PORT:-3000}"
EXP_GATEWAY_PORT="${EXP_GATEWAY_PORT:-3080}"
EXP_GATEWAY_IMPL="${EXP_GATEWAY_IMPL:-node}"
EXP_UI_MODE="${EXP_UI_MODE:-dev}"

PIDS=()
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-127.0.0.1,localhost}"

info() {
  printf '%s\n' "$*"
}

kill_tree() {
  local pid="$1"
  local children=""

  if command -v pgrep >/dev/null 2>&1; then
    children="$(pgrep -P "$pid" 2>/dev/null || true)"
  fi

  for child in $children; do
    kill_tree "$child"
  done

  kill "$pid" >/dev/null 2>&1 || true
}

cleanup() {
  trap - EXIT INT TERM
  if [ "${#PIDS[@]}" -gt 0 ]; then
    for pid in "${PIDS[@]}"; do
      kill_tree "$pid"
    done
    wait "${PIDS[@]}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

port_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$port$"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  return 1
}

wait_http() {
  local url="$1"
  local label="$2"
  local tries="${3:-60}"

  for _ in $(seq 1 "$tries"); do
    if curl --noproxy '*' -fsS "$url" >/dev/null 2>&1; then
      info "$label ready: $url"
      return 0
    fi
    sleep 1
  done

  info "$label did not answer in time: $url"
  return 1
}

start_api() {
  if port_listening "$EXP_API_PORT"; then
    info "API port $EXP_API_PORT is already listening, checking health."
    wait_http "http://$EXP_SERVICE_HOST:$EXP_API_PORT/healthz" "API" 5
    return $?
  fi

  mkdir -p "$EXP_ROOT"
  info "Starting FastAPI API on $EXP_SERVICE_HOST:$EXP_API_PORT"
  (
    cd "$ROOT/core"
    EXP_ROOT="$EXP_ROOT" \
    EXP_RATE_LIMIT_ENABLED="${EXP_RATE_LIMIT_ENABLED:-1}" \
      uv run --extra server uvicorn exp_core.server:app \
        --host "$EXP_SERVICE_HOST" \
        --port "$EXP_API_PORT"
  ) &
  PIDS+=("$!")
  wait_http "http://$EXP_SERVICE_HOST:$EXP_API_PORT/healthz" "API"
}

start_ui() {
  if port_listening "$EXP_UI_PORT"; then
    info "UI port $EXP_UI_PORT is already listening, checking health."
    wait_http "http://$EXP_SERVICE_HOST:$EXP_UI_PORT/" "UI" 5
    return $?
  fi

  info "Starting Next.js UI on $EXP_SERVICE_HOST:$EXP_UI_PORT"
  (
    cd "$ROOT/ui"
    if [ ! -d node_modules ]; then
      npm install
    fi
    if [ "$EXP_UI_MODE" = "start" ]; then
      EXP_DB_PATH="$EXP_DB_PATH" npm run start -- --hostname "$EXP_SERVICE_HOST" --port "$EXP_UI_PORT"
    else
      EXP_DB_PATH="$EXP_DB_PATH" npm run dev -- --hostname "$EXP_SERVICE_HOST" --port "$EXP_UI_PORT"
    fi
  ) &
  PIDS+=("$!")
  wait_http "http://$EXP_SERVICE_HOST:$EXP_UI_PORT/" "UI"
}

start_gateway() {
  if port_listening "$EXP_GATEWAY_PORT"; then
    info "Gateway port $EXP_GATEWAY_PORT is already listening, checking health."
    wait_http "http://127.0.0.1:$EXP_GATEWAY_PORT/__gateway/health" "Gateway" 5
    return $?
  fi

  info "Starting intranet gateway on $EXP_GATEWAY_HOST:$EXP_GATEWAY_PORT"
  if [ "$EXP_GATEWAY_IMPL" = "caddy" ] && command -v caddy >/dev/null 2>&1; then
    (
      cd "$ROOT"
      EXP_GATEWAY_PORT="$EXP_GATEWAY_PORT" \
      EXP_API_UPSTREAM="$EXP_SERVICE_HOST:$EXP_API_PORT" \
      EXP_UI_UPSTREAM="$EXP_SERVICE_HOST:$EXP_UI_PORT" \
        caddy run --config deploy/Caddyfile.local --adapter caddyfile
    ) &
  else
    (
      cd "$ROOT"
      EXP_GATEWAY_HOST="$EXP_GATEWAY_HOST" \
      EXP_GATEWAY_PORT="$EXP_GATEWAY_PORT" \
      EXP_API_ORIGIN="http://$EXP_SERVICE_HOST:$EXP_API_PORT" \
      EXP_UI_ORIGIN="http://$EXP_SERVICE_HOST:$EXP_UI_PORT" \
        node scripts/local-gateway.mjs
    ) &
  fi
  PIDS+=("$!")
  wait_http "http://127.0.0.1:$EXP_GATEWAY_PORT/__gateway/health" "Gateway"
}

start_api
start_ui
start_gateway

cat <<EOF

Experience Pool intranet preview is running.

Unified URL:  http://127.0.0.1:$EXP_GATEWAY_PORT
Gateway:      http://127.0.0.1:$EXP_GATEWAY_PORT/__gateway/health
API health:   http://127.0.0.1:$EXP_GATEWAY_PORT/healthz
UI upstream:  http://$EXP_SERVICE_HOST:$EXP_UI_PORT
API upstream: http://$EXP_SERVICE_HOST:$EXP_API_PORT
Data root:    $EXP_ROOT

Press Ctrl+C to stop the processes started by this script.
EOF

wait

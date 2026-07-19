#!/usr/bin/env bash
# Shared workspace configuration for the portal, services, and sibling repos.
# Source this file; do not execute profile files directly.

EXP_CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

exp_default() {
  local name="$1"
  local value="$2"
  if [ -z "${!name+x}" ]; then
    printf -v "$name" '%s' "$value"
  fi
  export "$name"
}

exp_default EXP_ENV "development"
case "$EXP_ENV" in
  development|production) ;;
  *)
    printf 'experience-pool: unsupported EXP_ENV=%s (expected development or production)\n' "$EXP_ENV" >&2
    return 2 2>/dev/null || exit 2
    ;;
esac

exp_default EXP_REPO_ROOT "$(cd "$EXP_CONFIG_DIR/.." && pwd -P)"
exp_default EXP_WORKSPACE_ROOT "$(cd "$EXP_REPO_ROOT/.." && pwd -P)"
exp_default EXP_PLUGIN_REPO "$EXP_WORKSPACE_ROOT/expool-mcp-plugin"
exp_default EXP_FLEET_REPO "$EXP_WORKSPACE_ROOT/claude-fleet"
exp_default EXPOOL_PORTAL_ROOT "$EXP_REPO_ROOT"
exp_default EXPOOL_CONFIG_FILE "$EXP_CONFIG_DIR/env.sh"

# Load machine-specific values before tracked defaults. Both files use
# exp_default, so values already exported by the caller remain authoritative.
LOCAL_PROFILE="$EXP_CONFIG_DIR/environments/$EXP_ENV.local.sh"
TRACKED_PROFILE="$EXP_CONFIG_DIR/environments/$EXP_ENV.sh"
if [ -f "$LOCAL_PROFILE" ]; then
  # shellcheck disable=SC1090
  . "$LOCAL_PROFILE"
fi
if [ ! -f "$TRACKED_PROFILE" ]; then
  printf 'experience-pool: profile not found: %s\n' "$TRACKED_PROFILE" >&2
  return 2 2>/dev/null || exit 2
fi
# shellcheck disable=SC1090
. "$TRACKED_PROFILE"

# Paths derived from EXP_ROOT stay consistent unless explicitly overridden.
exp_default EXP_DB_PATH "$EXP_ROOT/pool.db"
exp_default EXP_TRAJECTORIES_DIR "$EXP_ROOT/trajectories"
exp_default EXP_CREDENTIALS_DIR "$EXP_ROOT/credentials"
exp_default EXP_RUNTIME_DIR "$EXP_ROOT/runtime"
exp_default EXP_RUNTIME_ENV "$EXP_ROOT/runtime.env"
exp_default EXP_SERVER_LOG "$EXP_ROOT/server.log"
exp_default EXP_RAG_LOG "$EXP_ROOT/rag-maintenance.log"
exp_default EXP_RAG_MARKER "$EXP_RUNTIME_DIR/rag-maintenance.last"
exp_default EXP_BABYSIT_LOG "/tmp/babysit.log"
exp_default EXP_GATEWAY_LOG "/tmp/gateway.log"
exp_default EXP_UI_LOG "/tmp/next-${EXP_UI_PORT}-stable.log"

exp_default EXP_API_ORIGIN "http://127.0.0.1:$EXP_API_PORT"
exp_default EXP_UI_ORIGIN "http://127.0.0.1:$EXP_UI_PORT"
exp_default EXP_GATEWAY_ORIGIN "http://127.0.0.1:$EXP_GATEWAY_PORT"
exp_default EXP_API_BASE "$EXP_API_ORIGIN"
exp_default EXP_PUBLIC_BASE_URL "$EXP_GATEWAY_ORIGIN"
exp_default EXP_BIND_BASE_URL "$EXP_PUBLIC_BASE_URL"
exp_default EXP_UI_HEALTH_URL "$EXP_UI_ORIGIN/login"

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  for name in \
    EXP_ENV EXP_REPO_ROOT EXP_WORKSPACE_ROOT EXP_PLUGIN_REPO EXP_FLEET_REPO \
    EXP_ROOT EXP_DB_PATH EXP_TRAJECTORIES_DIR EXP_CREDENTIALS_DIR \
    EXP_API_PORT EXP_UI_PORT EXP_GATEWAY_PORT EXP_PUBLIC_BASE_URL \
    EXP_UI_PUBLIC_URL EXPOOL_PORTAL_ROOT; do
    printf '%s=%q\n' "$name" "${!name-}"
  done
fi

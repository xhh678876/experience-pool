#!/usr/bin/env bash
# Idempotent installer for the experience-pool skill.
#
# 1. Installs (or updates) the npm CLI globally so `exp` is on PATH.
# 2. If the agent isn't yet registered, runs `exp register`.
# 3. Verifies connectivity to the gateway.
#
# Override:
#   EXP_BASE_URL    gateway URL (default http://localhost:8080)
#   EXP_AGENT_NAME  agent identifier (default $USER-$(hostname))
#   EXP_TEAM        team for the agent (default "default")
#   EXP_PACKAGE     npm package spec (default @experience-pool/cli@latest)

set -euo pipefail

BASE="${EXP_BASE_URL:-http://localhost:8080}"
NAME="${EXP_AGENT_NAME:-${USER}-$(hostname -s)}"
TEAM="${EXP_TEAM:-default}"
PKG="${EXP_PACKAGE:-@experience-pool/cli@latest}"

echo "[1/3] installing $PKG"
if command -v npm >/dev/null 2>&1; then
    npm install -g "$PKG"
else
    echo "npm not found. install Node.js >=18 first." >&2
    exit 1
fi

echo "[2/3] registering agent $NAME on team $TEAM at $BASE"
if ! exp whoami 2>/dev/null | grep -q '"agent_name"'; then
    exp --base "$BASE" register --name "$NAME" --team "$TEAM"
else
    echo "(already registered)"
    exp whoami
fi

echo "[3/3] connectivity check"
if curl -fsS "${BASE}/healthz" >/dev/null; then
    echo "gateway healthy"
else
    echo "gateway not reachable at $BASE" >&2
    exit 1
fi

echo "done. try: exp search --q 'something you might have solved before'"

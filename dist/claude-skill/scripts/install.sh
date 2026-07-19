#!/usr/bin/env bash
# Idempotent installer for the experience-pool skill.
#
# 1. Installs (or updates) the npm CLI globally so `exp` is on PATH.
# 2. Installs this Claude Skill under ~/.claude/skills/experience-pool.
# 3. If the agent isn't yet registered, runs `exp register`.
# 4. Verifies connectivity to the gateway.
#
# Override:
#   EXP_BASE_URL    gateway URL (default https://expool.clawsii.com)
#   EXP_AGENT_NAME  agent identifier (default $USER-$(hostname))
#   EXP_TEAM        team for the agent (default "default")
#   EXP_PACKAGE     npm package spec (default local repo cli when available,
#                   otherwise @experience-pool/cli@latest)

set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
REPO_ROOT="$(cd "$SKILL_ROOT/../.." && pwd -P)"
TARGET="${EXP_CLAUDE_SKILL_DIR:-$HOME/.claude/skills/experience-pool}"
BASE="${EXP_BASE_URL:-https://expool.clawsii.com}"
NAME="${EXP_AGENT_NAME:-${USER}-$(hostname -s)}"
TEAM="${EXP_TEAM:-default}"
if [ -n "${EXP_PACKAGE:-}" ]; then
    PKG="$EXP_PACKAGE"
elif [ -f "$REPO_ROOT/cli/package.json" ]; then
    PKG="$REPO_ROOT/cli"
else
    PKG="@experience-pool/cli@latest"
fi

echo "[1/4] installing $PKG"
if command -v npm >/dev/null 2>&1; then
    if [ -d "$PKG" ]; then
        (cd "$PKG" && npm install --package-lock=false && npm run build)
    fi
    npm install -g "$PKG"
else
    echo "npm not found. install Node.js >=18 first." >&2
    exit 1
fi

echo "[2/4] installing Claude skill to $TARGET"
mkdir -p "$TARGET"
if [ "$SKILL_ROOT" != "$(cd "$TARGET" && pwd -P)" ]; then
    cp -a "$SKILL_ROOT/." "$TARGET/"
fi

echo "[3/4] registering agent $NAME on team $TEAM at $BASE"
if ! exp whoami 2>/dev/null | grep -q '"agent_name"'; then
    TMP="$(mktemp -t exp_register).json"
    exp --base "$BASE" register --name "$NAME" --team "$TEAM" >"$TMP"
    node - "$TMP" <<'NODE'
const fs = require("fs");
const file = process.argv[2];
const cred = JSON.parse(fs.readFileSync(file, "utf8"));
console.log(JSON.stringify({
  agent_id: cred.agent_id,
  agent_name: cred.agent_name,
  team: cred.team,
  secret: "***",
  credentials_path: cred.credentials_path,
}, null, 2));
NODE
    rm -f "$TMP"
else
    echo "(already registered)"
    exp whoami
fi

echo "[4/4] connectivity check"
if curl --noproxy '*' -fsS "${BASE}/healthz" >/dev/null; then
    echo "gateway healthy"
else
    echo "gateway not reachable at $BASE" >&2
    exit 1
fi

echo "done. try: exp search-lite --q 'something you might have solved before'"

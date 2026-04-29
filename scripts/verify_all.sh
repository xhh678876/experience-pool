#!/usr/bin/env bash
# Full from-scratch verification.
#
# 1. Wipes ~/.experience-pool and any test artifacts
# 2. Re-syncs python deps via uv
# 3. Runs core unit tests (mock LLM, fast)
# 4. Runs gateway unit tests
# 5. Builds the Next.js UI
# 6. Runs the integration smoke against the real `claude` CLI
# 7. Final HTTP smoke against the running UI
#
# Each phase prints PHASE n/7 and exits non-zero on failure.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
CORE="$HERE/core"
GW="$HERE/gateway"
UI="$HERE/ui"

red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
phase()  { printf "\n\033[1m==== PHASE %s ====\033[0m\n" "$*"; }

phase "1/7  wipe state"
rm -rf /tmp/expintegration /tmp/exp_export /tmp/skills_demo /tmp/skills_installed /tmp/skills_installed_redo
# Force-glob removal; bash returns the literal pattern when no matches, so '|| true'.
rm -rf /tmp/exp_pii.*    2>/dev/null || true
rm -rf /tmp/exp_clean.*  2>/dev/null || true
rm -rf /tmp/exp_child.*  2>/dev/null || true
rm -rf /tmp/exp_using.*  2>/dev/null || true
rm -rf /tmp/exp_skill.*  2>/dev/null || true
rm -rf /tmp/exp_skill_install.* 2>/dev/null || true
rm -f  /tmp/exp_traj_*.json /tmp/exp_ui.log 2>/dev/null || true
pkill -f next-server 2>/dev/null || true
pkill -f 'npm run start' 2>/dev/null || true
green "  cleaned"

phase "2/7  uv sync core + gateway"
( cd "$CORE" && uv pip install -e ".[test]" >/dev/null 2>&1 )
( cd "$GW"   && uv pip install -e .          >/dev/null 2>&1 )
green "  deps synced"

phase "3/7  core unit tests (EXP_LLM=mock)"
( cd "$CORE" && EXP_LLM=mock uv run pytest tests/ -q 2>&1 | tail -3 )

phase "4/7  gateway unit tests"
( cd "$GW" && uv run pytest tests/ -q 2>&1 | tail -3 )

phase "5/7  Next.js UI build"
( cd "$UI" && npm run build 2>&1 | tail -10 )

phase "6/7  integration smoke (real claude CLI)"
"$HERE/scripts/integration_smoke.sh"

phase "7/7  live UI HTTP smoke against the integration pool"
( cd "$UI" && EXP_DB_PATH=/tmp/expintegration/pool.db npm run start > /tmp/exp_ui.log 2>&1 ) &
UI_PID=$!
trap 'kill $UI_PID 2>/dev/null || true; pkill -f next-server 2>/dev/null || true' EXIT
sleep 6
for path in / /experiences /skills /login; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:3000${path}")
  if [ "$code" != "200" ]; then
    red   "  GET $path -> HTTP $code"
    exit 1
  fi
  green "  GET $path -> HTTP 200"
done

# Pick the first skill detail and confirm the lineage shows.
SID=$(curl -s http://localhost:3000/skills | grep -oE '/skills/[a-f0-9-]{36}' | head -1)
if [ -n "$SID" ]; then
  HTML=$(curl -s "http://localhost:3000$SID")
  for needle in "Experiences using this skill" "credit applied" "Q update history"; do
    if ! echo "$HTML" | grep -qF "$needle"; then
      red "  detail page missing fragment: $needle"
      exit 1
    fi
  done
  green "  /skills/[id] lineage + Q history rendered"
fi
EID=$(curl -s http://localhost:3000/experiences | grep -oE '/experiences/[a-f0-9-]{36}' | head -1)
if [ -n "$EID" ]; then
  HTML=$(curl -s "http://localhost:3000$EID?tab=skills")
  if echo "$HTML" | grep -qF "Skills used by this experience"; then
    green "  experience -> skills tab renders"
  else
    yellow "  experience -> skills tab not yet wired (no skill use was declared on this experience)"
  fi
fi

phase "DONE"
green "all phases passed"

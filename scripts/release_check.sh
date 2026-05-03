#!/usr/bin/env bash
# Release gate for the 创智内网 MVP.
#
# It avoids the real-Claude integration path and verifies the publishable
# surface: lite MVP, core unit tests, optional gateway tests, CLI tests, UI
# production build, and high-severity npm audit gate.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "[1/5] MVP smoke"
"$HERE/scripts/mvp_smoke.sh"

echo "[2/5] core tests"
(
  cd "$HERE/core"
  EXP_LLM=mock UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-/tmp/exp-core-release-venv}" \
    uv run --extra test --extra server --with httpx pytest tests -q
)

echo "[3/5] gateway tests"
(
  cd "$HERE/gateway"
  UV_PROJECT_ENVIRONMENT="${UV_GATEWAY_ENVIRONMENT:-/tmp/exp-gateway-release-venv}" \
    uv run --with pytest pytest tests -q
)

echo "[4/5] CLI tests"
(
  cd "$HERE/cli"
  npx --yes tsx@4.19.0 --test test/*.test.ts
)

echo "[5/5] UI build + high-severity audit gate"
(
  cd "$HERE/ui"
  if [ ! -d node_modules ]; then
    npm install
  fi
  npm run build
  npm audit --audit-level=high
)

echo "RELEASE CHECK PASSED"

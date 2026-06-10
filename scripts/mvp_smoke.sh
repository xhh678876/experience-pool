#!/usr/bin/env bash
# Fast MVP verification:
#   - lite local structure/sanitize tests
#   - FastAPI + HMAC + SQL/vector + ACL tests
#   - npm CLI lite helper tests
#
# This intentionally does not run the heavy judge/credit/skills integration.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
CORE="$HERE/core"
CLI="$HERE/cli"

export EXP_LLM="${EXP_LLM:-mock}"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-/tmp/exp-core-mvp-venv}"

echo "[1/2] core lite MVP tests"
(
  cd "$CORE"
  uv run --extra test --extra server --with httpx \
    pytest tests/test_lite.py tests/test_lite_http_mvp.py -q
)

echo "[2/2] CLI lite helper tests"
(
  cd "$CLI"
  npx --yes tsx@4.19.0 --test test/sign.test.ts test/lite.test.ts
)

echo "MVP SMOKE PASSED"

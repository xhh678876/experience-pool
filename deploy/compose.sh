#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
EXP_ENV="${EXP_ENV:-production}"
# shellcheck disable=SC1091
. "$ROOT/config/env.sh"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-expool-$EXP_ENV}"
exec docker compose -f "$ROOT/deploy/docker-compose.yml" "$@"

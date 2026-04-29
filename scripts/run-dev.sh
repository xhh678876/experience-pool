#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"
docker compose -f docker-compose.dev.yml up -d

echo "waiting for postgres..."
until docker exec $(docker ps -q -f ancestor=postgres:16-alpine) pg_isready -U exp >/dev/null 2>&1; do
  sleep 1
done

echo "ready. start gateway:"
echo "  cd $ROOT/gateway && uv sync && uv run uvicorn app.main:app --reload --port 8080"
echo "and pipeline:"
echo "  cd $ROOT/workers && uv sync && uv run python -m workers.pipeline"
echo "and smoke:"
echo "  cd $ROOT && uv run python scripts/smoke.py"

#!/usr/bin/env bash
# Bootstrap the OPF redaction service on the GPU host.
#
# Run this on the OPF machine (NOT the main API host). It expects:
#   - opf package installed (~3GB model weights download on first run)
#   - this script's dir contains opf_filter.py + opf_service.py
#     (or they're on PYTHONPATH)
#   - a CUDA-capable GPU visible to the process
#
# Override anything via env vars:
#   OPF_BIND_HOST         default 0.0.0.0
#   OPF_BIND_PORT         default 8085
#   OPF_DEVICE            default cuda (cpu / cuda / cuda:0 / mps)
#   OPF_CHECKPOINT        default ~/.opf/privacy_filter
#   OPF_OPERATING_POINT   default balanced
#   OPF_AUTH_TOKEN        default empty (no auth — intranet trust mode)
#   CUDA_VISIBLE_DEVICES  pick a specific GPU id

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Ensure opf_filter.py + opf_service.py are importable. Two layouts:
#  (a) repo checkout: experience-pool/dist-public/{opf_service,opf_filter}.py
#      and experience-pool/core/exp_core/opf_filter.py
#  (b) standalone: both .py files in $HERE
PY_DIRS=()
[ -f "$ROOT/dist-public/opf_service.py" ] && PY_DIRS+=("$ROOT/dist-public")
[ -f "$ROOT/core/exp_core/opf_filter.py" ] && PY_DIRS+=("$ROOT/core/exp_core")
[ -f "$HERE/opf_service.py" ] && PY_DIRS+=("$HERE")

if [ "${#PY_DIRS[@]}" -eq 0 ]; then
    echo "ERROR: cannot find opf_service.py / opf_filter.py" >&2
    exit 1
fi

# de-dup PY_DIRS and join with :
PYTHONPATH="$(printf '%s\n' "${PY_DIRS[@]}" | awk '!seen[$0]++' | paste -sd:)${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONPATH

: "${OPF_BIND_HOST:=0.0.0.0}"
: "${OPF_BIND_PORT:=8085}"
: "${OPF_DEVICE:=cuda}"
: "${OPF_CHECKPOINT:=$HOME/.opf/privacy_filter}"
: "${OPF_OPERATING_POINT:=balanced}"

export OPF_BIND_HOST OPF_BIND_PORT OPF_DEVICE OPF_CHECKPOINT OPF_OPERATING_POINT

echo "[opf] bind:    $OPF_BIND_HOST:$OPF_BIND_PORT"
echo "[opf] device:  $OPF_DEVICE  (CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset})"
echo "[opf] ckpt:    $OPF_CHECKPOINT"
echo "[opf] auth:    $([ -n "${OPF_AUTH_TOKEN:-}" ] && echo on || echo off)"
echo "[opf] PYTHONPATH=$PYTHONPATH"
echo

# Prefer running via uvicorn directly if installed; fall back to python -m
if command -v uvicorn >/dev/null 2>&1; then
    exec uvicorn opf_service:app \
        --host "$OPF_BIND_HOST" --port "$OPF_BIND_PORT" \
        --workers 1 --log-level info
else
    exec python3 -m uvicorn opf_service:app \
        --host "$OPF_BIND_HOST" --port "$OPF_BIND_PORT" \
        --workers 1 --log-level info
fi

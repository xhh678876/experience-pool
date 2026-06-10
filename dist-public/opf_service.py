#!/usr/bin/env python3
"""Standalone OPF (OpenAI Privacy Filter) HTTP service.

This is the GPU-bound layer of the experience pool, runnable on its own
machine so the main FastAPI gateway stays light. Main server posts text
or full trajectory dicts here; we run the OPF model and return cleaned
output + per-label hit counts.

Run:
    OPF_DEVICE=cuda OPF_CHECKPOINT=~/.opf/privacy_filter \\
    uvicorn opf_service:app --host 0.0.0.0 --port 8085

Or via the helper script:
    scripts/run_opf_service.sh

Environment:
    OPF_DEVICE          "cpu" | "cuda" | "cuda:1" | "mps" (default cpu)
    OPF_CHECKPOINT      path to model weights (default ~/.opf/privacy_filter)
    OPF_OPERATING_POINT "high_recall" | "high_precision" | "balanced"
    OPF_AUTH_TOKEN      optional shared secret; clients send X-OPF-Token header
    OPF_BIND_HOST       (uvicorn) default 0.0.0.0
    OPF_BIND_PORT       (uvicorn) default 8085

Endpoints:
    GET  /healthz          quick liveness
    GET  /status           model load info, device, version
    POST /redact-text      {text}                 -> {text, hits, triggered_high}
    POST /redact-batch     {texts: [..]}          -> {results: [..]}
    POST /redact-trajectory {trajectory: [...]}   -> {trajectory, hits, triggered_high}

Auth: if OPF_AUTH_TOKEN is set, every endpoint except /healthz requires
the X-OPF-Token request header to match (constant-time compare). Empty
env var = no auth (intranet trust mode).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# We reuse the same opf_filter shipped with the main repo. Drop this file
# next to opf_filter.py (or pip-install both into the OPF host's venv).
import opf_filter

LOG = logging.getLogger("opf_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="experience-pool OPF service", version="1.0.0")

_AUTH_TOKEN = os.environ.get("OPF_AUTH_TOKEN", "").strip()


# ---------- auth ---------------------------------------------------------

@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)
    if not _AUTH_TOKEN:
        return await call_next(request)
    sent = request.headers.get("x-opf-token", "")
    if not hmac.compare_digest(sent, _AUTH_TOKEN):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


# ---------- request / response models -----------------------------------

class RedactTextReq(BaseModel):
    text: str


class RedactTextResp(BaseModel):
    text: str
    hits: dict[str, int]
    triggered_high: bool
    used: bool


class RedactBatchReq(BaseModel):
    texts: list[str] = Field(default_factory=list)


class RedactBatchResp(BaseModel):
    results: list[RedactTextResp]


class RedactTrajectoryReq(BaseModel):
    trajectory: list[dict[str, Any]]


class RedactTrajectoryResp(BaseModel):
    trajectory: list[dict[str, Any]]
    hits: dict[str, int]
    triggered_high: bool


# ---------- routes ------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/status")
async def status_endpoint() -> dict[str, Any]:
    s = opf_filter.status()
    return {
        **s,
        "device": os.environ.get("OPF_DEVICE", "cpu"),
        "checkpoint": os.environ.get("OPF_CHECKPOINT", "~/.opf/privacy_filter"),
        "operating_point": os.environ.get("OPF_OPERATING_POINT", "balanced"),
        "auth_required": bool(_AUTH_TOKEN),
    }


@app.post("/redact-text", response_model=RedactTextResp)
async def redact_text(req: RedactTextReq) -> RedactTextResp:
    res = opf_filter.redact_text(req.text)
    return RedactTextResp(
        text=res.text, hits=res.hits,
        triggered_high=res.triggered_high, used=res.used,
    )


@app.post("/redact-batch", response_model=RedactBatchResp)
async def redact_batch(req: RedactBatchReq) -> RedactBatchResp:
    results: list[RedactTextResp] = []
    for t in req.texts:
        r = opf_filter.redact_text(t)
        results.append(RedactTextResp(
            text=r.text, hits=r.hits,
            triggered_high=r.triggered_high, used=r.used,
        ))
    return RedactBatchResp(results=results)


@app.post("/redact-trajectory", response_model=RedactTrajectoryResp)
async def redact_trajectory(req: RedactTrajectoryReq) -> RedactTrajectoryResp:
    """Walk the trajectory and apply OPF to every text-bearing field.

    Reuses opf_filter.redact_node so the recursion + SKIP_KEYS logic is
    identical to the in-process path. Aggregate hits + high flag returned.
    """
    accumulator: dict[str, int] = {}
    cleaned, triggered = opf_filter.redact_node(req.trajectory, accumulator)
    return RedactTrajectoryResp(
        trajectory=cleaned, hits=accumulator, triggered_high=triggered,
    )


# Surface auth errors as JSON
@app.exception_handler(HTTPException)
async def _http_exc_handler(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# ---------- if run directly -----------------------------------------

def main() -> None:
    import uvicorn
    host = os.environ.get("OPF_BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("OPF_BIND_PORT", "8085"))
    LOG.info("starting opf_service on %s:%d (device=%s, auth=%s)",
             host, port,
             os.environ.get("OPF_DEVICE", "cpu"),
             "on" if _AUTH_TOKEN else "off")
    # Single worker — model is GPU-bound, no benefit from multi-worker
    # (each worker would load a copy of the model).
    uvicorn.run(app, host=host, port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()

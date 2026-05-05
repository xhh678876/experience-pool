# OPF Host Setup — single-file deployment guide

> Drag this file to the GPU machine, follow the 3 steps, done.
> No repo clone needed — the code is fetched from the main API at
> `http://10.244.66.195:3080`.

## What you're setting up

A separate **OPF redaction service** running on a GPU host, callable
over HTTP from the main experience-pool API. Architecture:

```
   agent ─HTTP→  main API (10.244.66.195:8081)
                    │  Layer 1 regex (fast)
                    │  DB write — sanitization_status='layer1_only'
                    │  return 202 in ~1s
                    │
                    ▼ async (via worker)
              ┌──────────────────────────────┐
              │  exp_opf_worker (any CPU box)│
              │  poll layer1_only rows       │
              │  ──HTTP──▶ THIS MACHINE      │
              │           opf_service:8085   │
              │           (cuda, 3GB model)  │
              │  update DB → 'done'          │
              └──────────────────────────────┘
```

This document is for **THIS MACHINE** — the GPU host. The main API is
already running.

---

## Step 1 — install OPF model + Python deps

```bash
# Python ≥ 3.9 + a CUDA-capable GPU + ~4GB free disk for weights
pip install --user "git+https://github.com/openai/privacy-filter.git" fastapi uvicorn pydantic

# First model load downloads ~3GB weights to ~/.opf/privacy_filter
python3 -c "from opf._api import OPF; OPF(model='~/.opf/privacy_filter', device='cuda')"
```

If the model download fails (no internet egress on this box), you'll
need to manually copy `~/.opf/privacy_filter` from a host that has it.

---

## Step 2 — fetch the service code

Two files are needed: `opf_service.py` (the FastAPI app) and
`opf_filter.py` (the OPF wrapper). Both are served by the main API:

```bash
mkdir -p /opt/experience-pool && cd /opt/experience-pool

curl -fsSL http://10.244.66.195:3080/opf_service.py -o opf_service.py
curl -fsSL http://10.244.66.195:3080/opf_filter.py  -o opf_filter.py
chmod +x opf_service.py
```

Sanity:

```bash
ls -la opf_service.py opf_filter.py
# Both should be ~6-9KB.
head -1 opf_service.py
# #!/usr/bin/env python3
```

---

## Step 3 — run the service

### 3a — manual / foreground (verify first)

```bash
cd /opt/experience-pool

OPF_BIND_HOST=0.0.0.0 \
OPF_BIND_PORT=8085 \
OPF_DEVICE=cuda \
OPF_CHECKPOINT=$HOME/.opf/privacy_filter \
OPF_OPERATING_POINT=balanced \
PYTHONPATH=/opt/experience-pool \
python3 -m uvicorn opf_service:app --host 0.0.0.0 --port 8085 --workers 1
```

It will:
1. Start uvicorn on `0.0.0.0:8085`
2. Lazy-load the OPF model on the first redact call (~10s)
3. Serve `/healthz`, `/status`, `/redact-text`, `/redact-batch`,
   `/redact-trajectory`

Verify from another machine on the intranet:

```bash
# Replace <opf-ip> with this machine's actual eth0 IP.
curl http://<opf-ip>:8085/healthz
# {"status":"ok"}

curl http://<opf-ip>:8085/status
# {"enabled":true, "loaded":false, "device":"cuda", ...}

curl -X POST http://<opf-ip>:8085/redact-text \
     -H "content-type: application/json" \
     -d '{"text":"my email is alice@sii.edu.cn"}'
# {"text":"my email is <PRIVATE_FILTER:private_email>", ...}
```

After the first /redact call, hit /status again — `loaded` should be
`true`.

### 3b — pin a specific GPU (multi-GPU box)

```bash
CUDA_VISIBLE_DEVICES=1 \
OPF_DEVICE=cuda \
... (rest of the env vars from 3a) ...
python3 -m uvicorn opf_service:app --host 0.0.0.0 --port 8085
```

`CUDA_VISIBLE_DEVICES=1` makes only the second GPU visible; the
process treats it as `cuda:0` internally.

### 3c — systemd service (long-running)

```bash
sudo tee /etc/systemd/system/opf-service.service <<'EOF'
[Unit]
Description=experience-pool OPF redaction service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/experience-pool
Environment=OPF_BIND_HOST=0.0.0.0
Environment=OPF_BIND_PORT=8085
Environment=OPF_DEVICE=cuda
Environment=OPF_CHECKPOINT=/root/.opf/privacy_filter
Environment=OPF_OPERATING_POINT=balanced
Environment=PYTHONPATH=/opt/experience-pool
# Pin a GPU if needed:
# Environment=CUDA_VISIBLE_DEVICES=1
# Optional shared-secret auth (must match EXP_OPF_AUTH_TOKEN on main API):
# Environment=OPF_AUTH_TOKEN=<long-random>
ExecStart=/usr/bin/env python3 -m uvicorn opf_service:app \
    --host 0.0.0.0 --port 8085 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now opf-service
sudo systemctl status opf-service
```

Logs:

```bash
sudo journalctl -u opf-service -f
```

---

## After this machine is up — wire the main API

(Do this on the **main API machine**, not here.)

```bash
# Add to the main uvicorn launch / settings:
export EXP_DEFER_OPF=1                          # push returns in ~1s
export EXP_OPF_REMOTE_URL=http://<opf-ip>:8085  # async backfill calls here
# Optional, only if you set OPF_AUTH_TOKEN above:
# export EXP_OPF_AUTH_TOKEN=<same long-random>
```

Then restart the main API.

Bring up the **OPF backfill worker** (anywhere with DB read/write +
network to this OPF service):

```bash
EXP_DB_PATH=/tmp/exp-mvp/pool.db \
EXP_TRAJECTORIES_DIR=/tmp/exp-mvp/trajectories \
EXP_OPF_REMOTE_URL=http://<opf-ip>:8085 \
nohup python3 scripts/exp_opf_worker.py > /tmp/opf-worker.log 2>&1 &
```

Worker logs every tick where it does work. To check progress:

```bash
sqlite3 /tmp/exp-mvp/pool.db \
  "SELECT sanitization_status, COUNT(*) FROM experiences GROUP BY 1"
# layer1_only | <count of pending>
# done        | <count of finished>
```

---

## Endpoints summary (this machine, port 8085)

| method | path | body | returns |
|---|---|---|---|
| GET | `/healthz` | — | `{"status":"ok"}` |
| GET | `/status` | — | model load, device, auth |
| POST | `/redact-text` | `{"text": "..."}` | `{text, hits, triggered_high, used}` |
| POST | `/redact-batch` | `{"texts": [..]}` | `{results: [...]}` |
| POST | `/redact-trajectory` | `{"trajectory": [...]}` | `{trajectory, hits, triggered_high}` |

If `OPF_AUTH_TOKEN` is set, every endpoint except `/healthz` requires
`X-OPF-Token: <token>` header.

---

## Tuning notes

| concern | knob |
|---|---|
| GPU memory | ~3-4GB; pin to a less-loaded card via `CUDA_VISIBLE_DEVICES` |
| Model device | `OPF_DEVICE=cuda:1` etc. (relative to visible devices) |
| Single-thread bottleneck | run >1 instance on different GPUs + put a load balancer in front |
| Recall vs precision | `OPF_OPERATING_POINT=high_recall` / `high_precision` / `balanced` |
| Per-call timeout (worker side) | `EXP_OPF_TIMEOUT_SECONDS=60` (default) |

---

## Troubleshooting

**`opf package not importable`** at startup — model not installed.
Re-run Step 1.

**`opf load failed`** — checkpoint dir missing. Try:
```bash
python3 -c "from opf._api import OPF; OPF(model='~/.opf/privacy_filter', device='cpu')"
```
to force a re-download.

**Out of GPU memory** — another process is using the GPU. Check
`nvidia-smi`. Use `CUDA_VISIBLE_DEVICES` to pick a different card, or
fall back to `OPF_DEVICE=cpu` (much slower but works).

**Main API timeout when calling here** — the main API uses
`EXP_OPF_TIMEOUT_SECONDS=8` by default for synchronous calls. With
`EXP_DEFER_OPF=1` it never makes synchronous calls, so the worker
(with 60s timeout) handles slow paths.

**`401 unauthorized` from clients** — auth token mismatch.
`OPF_AUTH_TOKEN` (here) must equal `EXP_OPF_AUTH_TOKEN` on main API
and worker.

---

## Quick health-check from anywhere

```bash
curl http://<opf-ip>:8085/healthz
curl http://<opf-ip>:8085/status | python3 -m json.tool
curl -X POST http://<opf-ip>:8085/redact-text \
     -H "content-type: application/json" \
     -d '{"text":"contact alice@sii.edu.cn at 555-0100"}'
```

Expected last response (after first call warms the model):

```json
{
  "text": "contact <PRIVATE_FILTER:private_email> at <PRIVATE_FILTER:private_phone>",
  "hits": {"private_email": 1, "private_phone": 1},
  "triggered_high": false,
  "used": true
}
```

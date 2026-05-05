# OPF service deployment guide

The OPF (OpenAI Privacy Filter) is the slow, GPU-bound part of the
sanitize pipeline (~tens of seconds per push when run in-process). For
production we host it on a **separate machine** so the main API stays
fast and stateless.

## Architecture

```
  agent ──HTTP──▶ main API (8081, CPU)
                     │
                     ├─ Layer 1 regex sanitize (fast)
                     ├─ DB insert (fast, mark sanitization_status='layer1_only')
                     └─ return 202 in ~1s
                            │
                            ▼ (async)
                  exp_opf_worker (CPU box, near DB)
                       │
                       └──HTTP──▶ opf_service (8085, GPU box)
                                    │
                                    └─ load OPF model on cuda:N
                                       redact trajectory
                                       return cleaned + hits
                       │
                       └─ writes back: sanitization_status='done',
                          updated trajectory file, audit_log entry
```

Three machines (or three roles on fewer machines):

| role | binary | network |
|------|--------|---------|
| main API | `uvicorn exp_core.server:app --port 8081` | accepts uploads |
| OPF service | `uvicorn opf_service:app --port 8085` | accepts redact calls |
| OPF worker | `python3 scripts/exp_opf_worker.py` | reads pool.db, calls OPF service |

OPF worker can run on the main API box (it just needs DB read/write +
network to the OPF service).

---

## Step 1 — bring up the OPF service host

On the GPU machine:

```bash
# Install OPF (~3GB model weights download)
pip install --user "git+https://github.com/openai/privacy-filter.git"
python3 -c "from opf._api import OPF; OPF(model='~/.opf/privacy_filter', device='cpu')"
# (first call downloads weights)

# Get the service code (only 2 files needed: opf_service.py + opf_filter.py)
git clone <repo>  /opt/experience-pool
cd /opt/experience-pool

# Run it (manual / foreground)
OPF_DEVICE=cuda CUDA_VISIBLE_DEVICES=0 \
  ./scripts/run_opf_service.sh

# Or as a systemd service
sudo cp deploy/opf-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opf-service
```

Verify:

```bash
curl http://<opf-host>:8085/healthz
# {"status":"ok"}

curl -X POST http://<opf-host>:8085/redact-text \
     -H "content-type: application/json" \
     -d '{"text":"my email is alice@sii.edu.cn"}'
# {"text":"my email is <PRIVATE_FILTER:private_email>", "hits":{"private_email":1}, ...}
```

### Optional: enable shared-secret auth

If the OPF service is reachable beyond your trusted intranet, set
`OPF_AUTH_TOKEN=<long-random>` on the service. Clients (main API +
worker) must send `X-OPF-Token: <token>` header — set
`EXP_OPF_AUTH_TOKEN=<token>` in their env.

---

## Step 2 — point the main API at the OPF service

On the main API host, restart with two new env vars:

```bash
export EXP_OPF_REMOTE_URL=http://<opf-host>:8085
# export EXP_OPF_AUTH_TOKEN=<token>           # if you set OPF_AUTH_TOKEN
# export EXP_OPF_TIMEOUT_SECONDS=8            # default 8s

# (also still supported: EXP_DEFER_OPF=1 to skip OPF entirely on hot path)
```

Mode resolution in `lite.py`:

| EXP_DEFER_OPF | EXP_OPF_REMOTE_URL | behavior |
|---|---|---|
| 1 | (any) | layer1_only; worker backfills |
| 0 | set | call remote service synchronously on every push |
| 0 | unset | load OPF in-process (legacy, slow) |

**Recommended for production**: `EXP_DEFER_OPF=1` + remote URL set.
Pushes return in ~1s, worker handles OPF asynchronously.

---

## Step 3 — bring up the worker

Anywhere with read/write access to pool.db:

```bash
EXP_DB_PATH=/var/lib/expool/pool.db \
EXP_TRAJECTORIES_DIR=/var/lib/expool/trajectories \
EXP_OPF_REMOTE_URL=http://<opf-host>:8085 \
python3 scripts/exp_opf_worker.py --limit 20
```

Or one-shot (CI / cron):

```bash
python3 scripts/exp_opf_worker.py --once --limit 100
```

It polls every 15s by default (`EXP_OPF_WORKER_INTERVAL`). Idempotent
— safe to run multiple instances against the same DB.

---

## Tuning

- **GPU memory**: ~3-4GB for the standard checkpoint. Pick `OPF_DEVICE=cuda:1` if cuda:0 is busy.
- **Throughput**: a single OPF service worker handles one redact call at
  a time (model is single-threaded). For higher throughput, run multiple
  service instances on different GPUs and put a load balancer in front
  (or assign each main API to a different OPF host).
- **Latency**: typical redact-trajectory call on a small claude-code
  session is 1-5s. The worker's `EXP_OPF_TIMEOUT_SECONDS` should be
  comfortably above the slowest expected call (default 60s).
- **Failures**: the worker retries on next tick. The main API's
  synchronous OPF call falls through to "no redaction" on timeout —
  better to ship a layer1_only row and backfill than block the user.

---

## Health checks

Main `/healthz` reports its OPF target via `EXP_OPF_REMOTE_URL`. To
verify the worker chain:

```bash
# How many rows are awaiting OPF backfill
sqlite3 $EXP_DB_PATH \
  "SELECT COUNT(*) FROM experiences WHERE sanitization_status='layer1_only'"

# After running the worker
sqlite3 $EXP_DB_PATH \
  "SELECT sanitization_status, COUNT(*) FROM experiences GROUP BY 1"
```

# Roadmap — what's next

Status legend: 🔥 must-do before internal-network production · ⚙️ nice-to-have ·
🧪 research / future · ✅ already done (cross-reference STATUS.md)

---

## Phase A — Internal-network production ops (~2 hours)

The codebase is application-layer ready. These are deployment-layer items.
Without them, "it runs" but isn't "it stays running".

### A1. systemd service unit 🔥

```ini
# /etc/systemd/system/expool.service
[Unit]
Description=Experience Pool server
After=network.target

[Service]
Type=simple
User=expool
WorkingDirectory=/var/lib/expool
Environment=EXP_ROOT=/var/lib/expool
ExecStart=/usr/local/bin/uvicorn exp_core.server:app --host 0.0.0.0 --port 8080 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Deliverable: `deploy/expool.service` + a one-page install README.

### A2. Backup 🔥

SQLite has a built-in hot-backup API. One cron job:

```bash
# /etc/cron.d/expool-backup — daily 03:00
0 3 * * * expool /usr/local/bin/sqlite3 /var/lib/expool/pool.db ".backup /var/backups/expool/pool-$(date +\%F).db" && find /var/backups/expool -name 'pool-*.db' -mtime +30 -delete
```

Plus an `rsync /var/lib/expool/skills /backup/host:/expool/skills/` for the
tar bundles.

Deliverable: `deploy/backup.sh` + `deploy/backup.cron`.

### A3. Rate limiting 🔥

The realistic internal threat is "someone wrote `while true; exp push`".
Use `slowapi`:

- 60 push/min per agent
- 1000 search/min per agent
- 10 push-skill/min per agent
- 429 with `Retry-After` header

Deliverable: middleware in `core/exp_core/server.py` + slowapi to
`pyproject.toml`.

### A4. Real `/healthz` 🔥

Right now `/healthz` returns `{"status":"ok"}` even if SQLite is corrupt.
Replace with a real check: `SELECT 1`, disk-free %, audit-log row count.
Add `/v1/admin/healthz` (auth-required, deeper).

Deliverable: ~30 lines in `server.py`.

### A5. Disk water-mark + auto-archive ⚙️

When `/var/lib/expool` > 80% full:
- Move trajectories older than 90 days to `archive/<year>/<month>/`.
- Drop `review_status='rejected'` skill bundles.
- Emit a structured warning to the audit log.

Deliverable: `core/exp_core/maintenance.py` + a cron entry.

### A6. Structured logging ⚙️

`structlog` is already a dependency. Wire it through the FastAPI middleware
so every request emits one JSON line with `agent_name / path / status /
duration_ms / experience_id`. Configure logrotate.

Deliverable: middleware + `deploy/logrotate.conf`.

### A7. Internal reverse proxy (Caddy or nginx) ⚙️

Even on internal network, reverse-proxying gives gzip + access-log + a
stable hostname.

```caddy
expool.example.com {
    reverse_proxy 127.0.0.1:8080
    encode gzip
    log {
        output file /var/log/caddy/expool.log
    }
}
```

Deliverable: `deploy/Caddyfile`.

### A8. Container packaging (alternative to systemd) ⚙️

`Dockerfile` + `docker-compose.yml`. Persistent volume on `/var/lib/expool`.
For teams that prefer container deployment over apt/yum.

Deliverable: `deploy/Dockerfile`, `deploy/docker-compose.yml`.

---

## Phase B — Quality of retrieval (~1 day)

### B1. Replace the hash embedding 🔥

The current `embeddings.py` is a deterministic hash trigram projection. Good
enough to demo but semantically weak. The FTS5 keyword pass partially saves
us; with real embeddings the pool gets a lot more useful.

Plan:
- Bring up `text-embedding-3-large` (or BGE-large-zh-v1.5 / jina-v3 for CN).
- New `embedder` worker that pulls from `pending_reembed` table.
- Migrate existing rows by re-embedding once, in the background.
- Bump `vectors.kind` payload with `embed_model_version` so we can re-migrate later.

Cost note: ~10k experiences × 1k tokens each × $0.13/M tokens ≈ $1.30 one-off.

### B2. Cross-encoder reranker ⚙️

After top-N retrieval, score (query, candidate) with a cross-encoder
(`bge-reranker-large` or similar). Improves precision@5 substantially. Run
only when the user-facing flow can tolerate +200 ms.

### B3. Hybrid retrieval tuning ⚙️

Currently 0.7 vector + 0.3 BM25. Run an A/B over recorded `search_log` to
find the right weights per task_type. Code already has the knobs.

### B4. Recall eval + card rewrite 🔥

The 2026-06-15 AVGen/NAVA pilot showed that ingestion can succeed while
automatic recall still fails. The current failure mode is not just embedding
quality: many cards index the first user message as `intent`, so the vector
store contains junk like paths, `hi`, or shell dumps instead of the real task.

Deliverables:
- Build a 10-query eval set: `query -> known relevant experience_id`.
- Report baseline `recall@5` and `MRR` for raw session search, RAG context,
  and skills search.
- Add a card-rewrite worker that distills each session into `task_intent`,
  key steps, outputs, and pitfalls.
- Re-embed distilled summaries or trajectory chunks, then compare against the
  baseline before changing plugin auto-recall thresholds.

---

## Phase C — Trust + governance (~1 day)

### C1. Replay-protection (only for non-trusted networks) 🧪

Right now signature = `HMAC(secret, METHOD\nPATH\nBODY)`. Add a
`X-Timestamp` header + nonce, reject `>5 min` skew, store nonces for 10 min
to defeat replay. Skip on fully-trusted internal network.

### C2. Per-team rate-limit + quota ⚙️

slowapi by-agent isn't enough if a team registers 100 agents. Add a
team-level cap: max N rows/team/day, max K bundles/team/total.

### C3. Reviewer roles ⚙️

Currently any reviewer can approve any row. Add `roles` table + UI
permission check: dept admins can approve their dept's rows; org admins
can approve anything.

### C4. Audit-log retention + immutability ⚙️

Configure SQLite trigger to forbid `UPDATE` and `DELETE` on `audit_log`.
Or move to `audit_log` as a periodically-archived JSONL file with a
content-addressed hash chain.

---

## Phase D — Scale-out path (only if needed) 🧪

The standalone SQLite server is good for ~50–100 concurrent agents and
single-digit GB of data. Beyond that:

### D1. Postgres migration

Code already exists in `gateway/`. Migration plan:
1. `pg_loader` script that reads the SQLite file and INSERTs into Postgres.
2. Switch `EXP_BACKEND=postgres` in `server.py`.
3. Bring up Qdrant for vectors, MinIO for bundles.
4. Bring up Redis Streams + the worker fleet (already coded in `workers/`).

### D2. Read replicas

Read traffic dominates (search ≫ push). Add a replica with WAL streaming;
route GET to replicas.

### D3. Sharding by team / task_type

Hash on `task_type` to route. Cross-shard search via federated query. Only
worth it past ~10M experiences.

---

## Phase E — Skill ecosystem (~1 week)

### E1. Skill versioning UX 🔥

Already have `(name, version)` unique constraint. Need:
- `exp skill versions <name>` to list all versions
- `exp install-skill --name foo` defaults to latest
- UI shows version badges on the skills list

### E2. Skill discovery in the agent loop ⚙️

Right now agents search skills only when explicitly told. Better:
- Auto-search on Claude Code session start with the user's first message
  as the query.
- If a skill scores above a threshold, install it locally and the agent
  picks it up automatically.

### E3. Skill testing harness ⚙️

Optional: a skill bundle can include `tests/` with executable `run.sh` that
takes inputs and asserts outputs. The pool runs them in a sandbox before
auto-approving.

### E4. Skill author leaderboard ⚙️

Surface "who wrote the skill that earned the most Q this month". Drives
contribution incentives.

---

## Phase F — Multi-org federation 🧪

If multiple companies want to share skills (without sharing experiences):

- Pool exports a signed `skills.bundle.tar.gz` of `acl=org` skills.
- A federation peer imports it, mapping skill ownership to a remote
  org_id.
- ACL gets a `federated:<org>` kind.
- No experience ever leaves an org boundary.

This is research-grade; flag for later.

---

## Calibration: what to tackle first

If the goal is "deploy to internal network this week":

1. **A1 (systemd)** — script the boot
2. **A2 (backup)** — never lose data
3. **A3 (rate limit)** — don't get DDoS'd by your own teammate
4. **A4 (healthz)** — know when it's broken
5. **B1 (real embeddings)** — make search worth using

Total: ~3 hours engineering + a one-off embed cost.

After that, the system is "internal-network production", and the rest is
incremental.

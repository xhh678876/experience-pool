# Experience Pool

Enterprise-internal experience pool for 100+ agents. **The product is three pieces working together**:

1. **A Claude Code Skill** at `dist/claude-skill/` (auto-installed at `~/.claude/skills/experience-pool`).
   When an agent needs to learn from past work or share what it did, it invokes this skill, which calls
   the npm CLI under the hood.
2. **An npm-distributed CLI** at `cli/` (`@experience-pool/cli`). HTTP client, HMAC-SHA256 signed.
   `npm install -g @experience-pool/cli` gets every agent on the latest version. Talks to:
3. **A FastAPI server** at `core/exp_core/server.py`. SQLite-backed (no Postgres required), HMAC-verified
   per request, single-binary deploy. `uvicorn exp_core.server:app --host 0.0.0.0 --port 8080`.

```
    Claude Code agent
         │
         ▼  (invokes skill)
    ~/.claude/skills/experience-pool/SKILL.md
         │
         ▼  (calls)
    npm-installed `exp` CLI ──HMAC-signed HTTPS──▶ FastAPI server ──▶ SQLite + filesystem
                                                       │
                                                       ▼
                                                 Sanitizer · Extractor · Judge · CreditAssigner · Embedder
```

 Implements the v2 design with the following adjustments from the original spec:

1. **Feedback is derived, not declared.** Agents declare `parent_experience_ids` on push. The CreditAssigner reads new experience rewards and automatically updates parents. No `feedback` API for agents.
2. **One-hop credit assignment.** Reward only flows to direct parents — prevents cycles when UI editing creates back-references.
3. **Mixed ranking from day one.** Search uses similarity z-score + Q z-score + UCB exploration from M2.
4. **Continuous reward internally.** Judge produces floats in `[-1, 1]`. `{-1, 0, 1}` discretization only for export and UI display.
5. **Extractor double-run.** Two prompts; structural diff above 0.5 threshold flags `unstable_extraction` and routes to review.
6. **Cost-aware judge routing.** Low-sensitivity / short trajectories → Haiku single-shot. Otherwise → Haiku/Sonnet 3-shot self-consistency. Ensemble only for high sensitivity / high reuse.
7. **Credit before dedup.** Even if a child push turns out to duplicate something already in the pool, its declared parents earn credit before the merge — the agent did genuinely use them.

## Quickstart for the actual product (Skill + npm CLI + server)

**1. Run the server**

```bash
cd core
uv pip install -e ".[server]"
EXP_ROOT=/var/lib/expool uvicorn exp_core.server:app --host 0.0.0.0 --port 8080
```

In production put it behind nginx / Cloudflare. The server is single-process,
SQLite-backed, no external dependencies. Storage scales to many GB before
needing Postgres; if you outgrow it, swap to the full FastAPI gateway under
`gateway/` which uses Postgres + Redis + Qdrant.

**2. Distribute the npm CLI**

```bash
cd cli
npm install
npm run build
npm publish --access restricted    # to a private GitHub Packages or internal registry
```

Agents install with `npm install -g @experience-pool/cli`. Updates flow
centrally — every agent picks up the new version on next install.

**3. Install the Claude Code Skill on every agent host**

```bash
EXP_BASE_URL=https://expool.your.corp \
EXP_AGENT_NAME=agent-$(hostname -s) \
EXP_TEAM=platform \
~/experience-pool/dist/claude-skill/scripts/install.sh
cp -r ~/experience-pool/dist/claude-skill ~/.claude/skills/experience-pool
```

The agent now has the `experience-pool` skill in its skill catalog. It will
invoke `exp search`, `exp push`, etc. automatically based on the SKILL.md
trigger keywords.

## Two ways to run the standalone backend

**Standalone** (no Docker, no infra). SQLite + filesystem + in-process vectors. Real LLM via the `claude` CLI.

```bash
cd core
uv venv --python 3.11 .venv && uv pip install -e .
uv run expctl register --name agent-a --team platform
uv run expctl push --agent agent-a --task csv_analysis --model claude-sonnet-4-6 \
    --file /tmp/traj.json --sensitivity low
uv run expctl search --agent agent-a --q "rank dimensions in tabular data"
```

**Distributed** (FastAPI + Postgres + Redis Streams + Qdrant + MinIO). Same logic, infra split out for scale.

```bash
cd infra && docker compose -f docker-compose.dev.yml up -d
cd ../gateway && uv sync && uv run uvicorn app.main:app --reload --port 8080 &
cd ../workers && uv sync && uv run python -m workers.pipeline &
uv run python scripts/smoke.py
```

## Layout

```
core/       Standalone implementation (SQLite + numpy-style vectors). expctl CLI.
gateway/    FastAPI gateway. Stateless. push / search / get + admin endpoints.
workers/    Pipeline workers (sanitizer, extractor, judge, embedder, dedup, credit).
ui/         Next.js review UI (server actions hit SQLite directly).
infra/      docker-compose, postgres schema, qdrant config, MinIO bootstrap.
scripts/    Smoke tests for the distributed stack.
```

## Review UI

A Next.js 15 reviewer lives in `ui/`. The UI *is* the backend in standalone
mode: server actions hit SQLite directly via `better-sqlite3`. No separate
API needs to be running.

```bash
cd ui
pnpm install   # or npm install
pnpm dev       # or npm run dev
# open http://localhost:3000
```

Override the database location with `EXP_DB_PATH=/path/to/pool.db`. Pages:
dashboard `/`, list `/experiences`, detail `/experiences/[id]` with Card /
Trajectory / Lineage / Audit tabs and an action bar (approve / reject / edit
/ re-judge / export / soft-delete). Editing and re-judging enqueue rows in
the helper tables `pending_reembed` and `pending_rejudge` for the Python
sidecar to consume — the Python schema file under `core/` is not modified.
See `ui/README.md` for the full server-action contract.

## Closed-loop verification

Run via `claude` CLI as the LLM backend:

```
parent push   → extractor 2-run div=0.15 (stable) → judge → q=(1.0,1.0,0.8,0.7,0.9)
child push    → declared parent_experience_ids=[parent]
              → extractor div=0.10 → judge → q=(0.9,0.95,0.7,0.75,0.8)
              → credit_applied: parents_updated=1
parent state  → q_update_count: 1 → 2
              → q_outcome: 1.0 → 0.987 (moved toward child's r=0.9 with α·c=0.13)
              → reuse_count: 1
search        → both surface; parent ranks higher (similarity z 0.55 vs -0.55, q z 0.35 vs -0.35)
audit log     → push, extract, judge, credit_applied stages all recorded
```

## Status

- [x] M1: skeleton (gateway + infra + workers wired)
- [x] M2: extractor double-run + 5-dim cost-aware judge + mixed ranking
- [x] Standalone mode (SQLite + claude CLI as LLM)
- [x] expctl CLI (register / push / search / get / dump-audit / stats / dashboard / leaderboard / drift-record / drift-check / issue-credential / acl-search / acl-get / export)
- [x] One-hop credit assignment with confidence-weighted updates
- [x] Dedup gated by intent + script + task_type triple match
- [x] M3: 3-layer sanitizer (rules → privacy filter → LLM business-sensitivity)
- [x] M4: Next.js review UI (`/ui`)
- [x] M5: Q-monitoring dashboard, judge drift detection (`exp_core.monitoring`)
- [x] M6: Parquet export
- [x] M7: ACL enforcement (private/team/org), HMAC credentials, denied-read auditing
- [x] M8: dashboard stats, reuse leaderboard, drift baseline + check
- [x] M9: skill bundle upload (`SKILL.md` + helpers), search, install, with same one-hop credit-assignment loop so skills earn Q from downstream experiences that succeed using them. UI at `/skills`.

## Skills (M9)

Agents upload reusable bundles (a `SKILL.md` with YAML frontmatter plus any
helper files) and other agents can search / install them. Skills are
**first-class** in credit assignment: when an experience push declares
`--uses-skill foo`, and that experience's judge reward arrives, foo's Q
moves via the same `α·c` one-hop update used for parent experiences. The
pool learns *which skills actually work* in practice instead of trusting
the author's self-assessment.

```bash
# Upload a skill (the directory must contain SKILL.md)
expctl push-skill --agent alice --bundle ./csv-helper --sensitivity low --acl team:platform

# Search uploaded skills with mixed ranking (similarity + Q + UCB)
expctl search-skills --q "rank top regions in tabular data"

# Declare skill use on a trajectory push
expctl push --agent alice --task csv_analysis --model claude-sonnet-4-6 \
    --file traj.json --uses-skill csv-helper

# Install a skill back to disk (e.g. into another agent's workspace)
expctl install-skill --name csv-helper --target ./vendor/skills/csv-helper

# Inspect Q updates on the skill
expctl get-skill --name csv-helper
```

Sanitizer runs on every text artifact in the bundle (SKILL.md and any
`.md/.py/.sh/.yaml/.json/.toml/...` file). High-severity findings hold the
skill at `review_status='pending'` until a human approves. Bundle SHA-256 is
recorded so installers can verify integrity. Bundles are capped at 5 MB and
200 files; tarbomb-resistant extraction guards against `..` paths.

## Closed-loop integration test

```bash
./scripts/integration_smoke.sh
```

Exercises sanitizer (PII redaction, raw-file preservation), ACL (cross-team
isolation + denied-read auditing), credit assignment (parent Q updates from
child reward), monitoring (dashboard + leaderboard), credential issuance,
and Parquet export — all driven by the real `claude` CLI as the LLM.

## Sanitizer (M3)

The push pipeline runs every trajectory through a three-layer sanitizer
between the trajectory write and the extractor. The extractor never sees
the raw text; the sanitized copy is what gets indexed and embedded.

**Layer 1 — deterministic regex rules.** Always runs. Driven by
`core/exp_core/sanitize_rules.yaml`. Categories:

| Category | Placeholder | Severity |
|----------|-------------|----------|
| email | `<EMAIL>` | medium |
| phone (intl + CN) | `<PHONE>` | medium |
| IPv4 / IPv6 | `<IP>` | low |
| credit card (Luhn-validated) | `<CARD>` | high |
| AWS access key (`AKIA…`) | `<KEY>` | high |
| SSH public/private keys, PEM blocks | `<KEY>` | high |
| Bearer tokens (`Authorization: Bearer …`) | `Bearer <TOKEN>` | high |
| Stripe `sk_live_…`, GitHub `ghp_…`, OpenAI `sk-…`, Anthropic `sk-ant-…` | `<SECRET>` | high |
| URLs with embedded credentials | `https://<USER>:<PASS>@…` | high |
| Internal hostnames (configurable) | `<INTERNAL_HOST>` | high |
| Internal employee IDs (configurable prefix) | `<EMP_ID>` | high |

Edit `sanitize_rules.yaml` to add domain-specific rules without code changes.

**Layer 2 — heuristic PII detector.** Skipped when `sensitivity=low` and
Layer 1 was clean. Flags (does NOT redact) names, addresses, dates of
birth, and credit-card-shaped digit runs. Reviewers see the flags in the
audit log.

**Layer 3 — LLM business sensitivity.** Runs when `sensitivity=high` or
when Layer 2 surfaced anything. Prompts the model to classify into
`internal_strategy / unreleased_product / financial_nonpublic /
legal_privileged / personnel / none`.

**Status routing.** The result drives `experiences.sanitization_status`:

- `done` — no findings
- `flagged` — Layer 1/2 found something but no high-severity hit
- `human_review` — Layer 1 hit a high-severity rule OR Layer 3 said sensitive

When status is `human_review`, `review_status` is forced to `pending`
even if the judge would otherwise auto-approve. The original raw
trajectory is preserved at `<id>.raw.json` next to the sanitized
`<id>.json` whenever any layer made a change, so reviewers can diff what
was redacted.

## Training export (M6)

The pool exports to a Hive-partitioned Parquet dataset suitable for
offline training pipelines. Partition columns are `task_type` and
`date` (UTC, derived from `created_at`). Each row joins the experience
with its latest reward, current Q values, parent edges, and a Q-update
count, plus a `{-1, 0, 1}` discretization of every reward dimension.

```bash
# Default: dump everything under <out>/task_type=<X>/date=<YYYY-MM-DD>/data.parquet
uv run expctl export --out ./data/

# Filter by date range and task_type:
uv run expctl export --out ./data/ \
    --since 2026-04-01 --until 2026-04-30 \
    --task csv_analysis
```

PyTorch usage (lazy torch import — Parquet export does not require torch):

```python
from exp_core.dataset import ExperienceDataset

ds = ExperienceDataset("./data", task_type="csv_analysis")
loader = ds.to_dataloader(batch_size=32)
for batch in loader:
    print(batch[0]["experience_id"], batch[0]["r_outcome_discrete"])
```

## Tests

```bash
cd core && EXP_LLM=mock uv run pytest tests/    # pool + sanitize + acl + monitoring + export
cd gateway && uv run pytest tests/               # 9 tests, pure logic
```

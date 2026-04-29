# Experience Pool — Project Status

Last updated: 2026-04-29

## What this project is

An internal-network "shared brain" for a fleet of Claude Code agents.
Three pieces work together:

```
    Claude Code agent
         │
         ▼  invokes skill (~/.claude/skills/experience-pool)
    ~/.claude/skills/experience-pool/SKILL.md
         │
         ▼  calls
    npm-installed `exp` CLI ──HMAC-signed HTTP──▶ FastAPI server
                                                        │
                                                        ▼
                                                  SQLite + filesystem
                                                  Sanitizer · Extractor · Judge ·
                                                  CreditAssigner · Embedder · FTS5
```

Every agent on the team uploads its completed trajectories and any reusable
skill bundles. The pool sanitizes, distills into a model-agnostic "experience
card", scores on five dimensions (outcome / intent / execution / orchestration
/ expression), embeds for retrieval, and propagates credit one hop back to
declared parents and skills. When another agent searches, results are mixed-
ranked by similarity (vector + BM25) + accumulated Q value + UCB exploration.

## Status snapshot

| Layer | Status | Tests | Lines |
|---|---|---|---|
| Python core (`core/`) | ✅ done | 54/55 (1 torch optional skipped) | ~3,300 |
| FastAPI server (`core/exp_core/server.py`) | ✅ done | manual e2e verified | ~210 |
| FastAPI gateway (`gateway/`, Postgres-backed) | ✅ optional | 9/9 | ~600 |
| Workers (`workers/`, Redis Streams) | ✅ optional | included in gateway tests | ~500 |
| Next.js UI (`ui/`) | ✅ done | build passes | ~1,400 |
| npm CLI (`cli/`) | ✅ done | 3/3 | ~400 |
| Claude Code skill (`dist/claude-skill/`) | ✅ done | available-skills check | ~70 |
| Integration scripts (`scripts/`) | ✅ done | end-to-end real-Claude smoke | ~250 |

**4 layer regression**: 54 + 9 + 3 + UI build = all green as of last run.

---

## What is done

### M1 — Skeleton & in-process pipeline ✅
- Monorepo layout: `core/` `gateway/` `workers/` `ui/` `cli/` `dist/` `scripts/`.
- `ExperiencePool` class wraps the full inline pipeline: write trajectory →
  sanitize → extract → judge → embed → dedup → credit-assign.
- SQLite schema with experiences, rewards, edges, q_updates, search_log,
  audit_log, vectors, skills, experience_skill_uses, skill_q_updates, plus
  FTS5 virtual tables maintained by triggers.

### M2 — Extractor double-run + 5-dim cost-aware Judge ✅
- Two parallel extractor prompts (A/B); structural fingerprint diff > 0.5
  flags `unstable`. Single-run fallback if exactly one of the two raises.
- Judge routing:
  - `sensitivity=low` AND `len(traj) <= 4` → Haiku, single shot
  - `sensitivity=high` OR `reuse_count >= 10` → Sonnet, 3-shot self-consistency + Haiku ensemble cross-check
  - default → Haiku, 3-shot self-consistency
- Reward stored as `[-1, 1]` floats internally; discretized to {-1, 0, 1} only at export.
- Mixed ranking (z-score similarity + z-score Q + UCB exploration) live from day one.

### M3 — Three-layer sanitizer ✅
- Layer 1: 25+ regex rules (email, phone, IPs, AWS/Stripe/GitHub/OpenAI/Anthropic keys, internal hostnames, employee IDs, URL credentials, Luhn-validated credit cards). YAML-driven (`core/exp_core/sanitize_rules.yaml`).
- Layer 2: heuristic PII detector (names, addresses, DOBs).
- Layer 3: optional LLM business-sensitivity classifier (only for `sensitivity=high` OR Layer 2 flagged).
- Status routing: `done` / `flagged` / `human_review`. `human_review` forces `review_status=pending`.
- Raw trajectory preserved at `<id>.raw.json` whenever any layer changed text.

### M4 — Next.js review UI ✅
- 7 routes: `/` `/experiences` `/experiences/[id]` `/skills` `/skills/[id]` `/login` `/api/export/[id]`.
- 5 tabs on experience detail: Card / Trajectory / Lineage / Skills / Audit.
- Skills detail page with lineage graph, Q update history, file count, sha256.
- Server actions for approve / reject / edit / re-judge / soft-delete; reviewer identity from cookie.
- `pending_reembed` and `pending_rejudge` helper tables (auto-created) so a Python sidecar can pick up edits.

### M5 / M8 — Monitoring ✅
- `dashboard_stats()`: counts by status / task / sensitivity, Q-scalar histogram + percentiles, top-10 reused, judge confidence p10/p50, 7-day ingestion.
- `reuse_leaderboard()` for both experiences and skills.
- `judge_drift(baseline_path)`: re-judges a frozen benchmark set with the current prompt and reports per-dim mean absolute deviation; triggers alert above 0.15.
- CLI: `expctl dashboard / leaderboard / drift-record / drift-check`.

### M6 — Parquet export for offline training ✅
- Hive-partitioned: `<root>/task_type=<X>/date=<YYYY-MM-DD>/data.parquet`.
- 36-column schema: experience_id, intent, script_steps (struct), reward (5 dims continuous + 5 dims discrete int8), Q values, parents, tags, sensitivity, ACL.
- `core/exp_core/dataset.py` provides a PyTorch `Dataset` with lazy torch import.
- CLI: `expctl export --out ./data/ --since YYYY-MM-DD --task <X>`.

### M7 — Identity + ACL ✅
- HMAC-SHA256 credentials at `<EXP_ROOT>/credentials/<agent>.json` (mode 0600).
- Three ACL kinds: `private` / `team:<X>` / `org`. Fail-closed on unknown.
- `search_with_acl` over-fetches and filters; `get_with_acl` returns 404 + audit `read_denied`.
- Server middleware verifies signature on every non-public path, rejects 401 on tamper.
- CLI sign uses Node `crypto.createHmac`; reference test cross-checks the canonical string format.

### M9 — Skill bundle upload ✅
- Bundle = directory containing `SKILL.md` (YAML frontmatter required: `name`, `description`).
- `build_bundle` walks the dir, sanitizes every text file, packs into deterministic `tar.gz`, hashes with sha256.
- Hard limits: 5 MB total, 1 MB per file, 200 files max, paths < 200 chars, ignores `.git/.venv/node_modules/...`.
- Path-traversal guard on extraction (resolve + prefix check, with macOS `/tmp → /private/tmp` symlink fix).
- Skills are first-class in credit assignment: declared via `--uses-skill foo`; credit fires from the child's reward via the same `(1 - α·c) · old + α·c · r` rule.
- `expctl push-skill / search-skills / install-skill / list-skills / get-skill / approve-skill / reject-skill`.

### Improvements added on top of the brief

- **A. SQLite FTS5 keyword search** blended into the ranking (0.7 vector + 0.3 BM25 rank-signal). Without this, the deterministic hash-based embedding fails on "kafka" since trigram cosine isn't semantic.
- **B. CLI `approve / reject / approve-skill / reject-skill`** for pure-CLI review workflows.
- **C. Bidirectional UI link** — experience detail has a Skills tab listing what was used.
- **D. Dependency validation on skill push** — declared deps that don't resolve get returned as `dependency_warnings` (warn, don't fail).
- **E. Per-file 1 MB cap** in addition to the 5 MB total.
- **Robust extractor** — single-run fallback if one of the two parallel runs raises (real `claude -p` occasionally returns non-JSON).
- **Real verification scripts** — `scripts/integration_smoke.sh` runs the full real-Claude pipeline; `scripts/verify_all.sh` wipes state, runs every test layer, builds the UI, runs integration, then smokes the live UI HTTP routes.

---

## What is the actual deliverable

Three artifacts an internal team can ship:

### 1. The Claude Code Skill (`dist/claude-skill/`)
Drop into `~/.claude/skills/experience-pool/`. The skill auto-fires on
trigger phrases like "share experience" / "lookup playbook" / "record what
worked". When fired it shells out to the npm CLI.

### 2. The npm CLI (`cli/`, `@experience-pool/cli`)
TypeScript, builds with `tsc`, distributed via `npm install -g`. Reads
HMAC credentials from `~/.experience-pool/credentials/<agent>.json` (or
`EXP_AGENT_NAME` + `EXP_AGENT_SECRET` env vars for CI).

Commands:
```
exp register --name <agent> --team <team>
exp whoami
exp push --task <type> --model <m> --file traj.json [--uses-skill foo --parents id1,id2]
exp search --q "..." [--task t] [--top-k 5]
exp push-skill --bundle ./my-skill
exp search-skills --q "..."
exp install-skill --name <skill> --target ./vendor/skills/<skill>
exp dashboard
exp leaderboard
```

### 3. The shared server (`core/exp_core/server.py`)
FastAPI app over the standalone `ExperiencePool`. SQLite-backed (no Postgres
required), HMAC-verified per request, CORS-aware, single-process.

```bash
EXP_ROOT=/var/lib/expool uvicorn exp_core.server:app --host 0.0.0.0 --port 8080
```

For the larger Postgres + Redis Streams + Qdrant + MinIO version, see
`gateway/` and `workers/`.

---

## Verified end-to-end

The gateway has been brought up and exercised against the real `claude` CLI
through the npm CLI:

- **Register two agents on different teams** → server issues HMAC creds,
  client signs every subsequent request.
- **Alice push-skill (`quick-grep`)** → server extracts tar, runs the three-
  layer sanitizer, writes the bundle to disk, returns `{skill_id, sha256}`.
- **Bob (different team) `search-skills`** with `acl=org` → finds it.
- **Bob `install-skill`** → bundle bytes round-trip identical (sha256 match).
- **Tampered secret** → 401 `bad signature`.

A separate real-Claude integration (`scripts/integration_smoke.sh`) exercises:

- PII trajectory upload → Layer 1 redacts AKIA + email + card + phone, status
  goes to `human_review`, `<id>.raw.json` preserved.
- Parent + child trajectory push → child reward propagates back to parent's
  Q via `α·c` update; `q_update_count` rises 1 → 2.
- Cross-team ACL: Bob (data) sees zero of Alice's `team:platform` rows;
  Carol (platform) sees all of them; direct fetch by Bob writes a
  `read_denied` audit row.

---

## What is NOT done (= roadmap)

See [ROADMAP.md](ROADMAP.md) for the full plan. Top items:

- Internal-network production ops (systemd, backups, log rotation, slowapi)
- Real embeddings (BGE-large-zh / jina-v3) replacing the hash stub
- TLS termination (when going beyond fully-trusted internal network)
- Replay protection (timestamp + nonce) — not needed on internal network
- Postgres migration path (if scaling past ~50 concurrent agents)

---

## Layout

```
experience-pool/
├── core/                     # Standalone Python implementation
│   ├── exp_core/
│   │   ├── pool.py           # ExperiencePool — the inline pipeline
│   │   ├── server.py         # FastAPI HTTP server (the production binary)
│   │   ├── skills.py         # Skill bundle upload, search, install, credit
│   │   ├── sanitize.py       # Three-layer sanitizer
│   │   ├── prompts.py        # Extractor + judge prompts
│   │   ├── llm.py            # `claude -p` subprocess + mock backend
│   │   ├── ranking.py        # Mixed (similarity z + Q z + UCB)
│   │   ├── fts.py            # SQLite FTS5 helpers
│   │   ├── identity.py       # HMAC creds + ACL predicates
│   │   ├── acl_search.py     # ACL-aware search wrapper
│   │   ├── monitoring.py     # Dashboard + drift + leaderboard
│   │   ├── export.py         # Parquet export
│   │   ├── dataset.py        # Optional PyTorch Dataset
│   │   ├── embeddings.py     # Hash-based stub (replaceable)
│   │   ├── schema.py         # Single-source-of-truth SQLite schema
│   │   ├── sanitize_rules.yaml
│   │   └── cli.py            # `expctl` (Python local CLI, 19 subcommands)
│   ├── tests/                # 54 tests covering every module
│   └── pyproject.toml
├── cli/                      # npm-distributed TypeScript CLI
│   ├── src/
│   │   ├── index.ts          # commander entry, 9 subcommands
│   │   ├── client.ts         # HMAC-signed HTTP client
│   │   ├── sign.ts           # HMAC-SHA256 of canonical request
│   │   └── config.ts         # Credential discovery (env or filesystem)
│   ├── test/sign.test.ts     # node:test
│   └── package.json
├── ui/                       # Next.js 15 reviewer UI
│   ├── app/                  # App Router: dashboard, experiences, skills, login
│   ├── lib/                  # better-sqlite3 queries
│   └── components/ui/        # hand-rolled shadcn-style primitives
├── gateway/                  # Optional Postgres-backed FastAPI (for scale)
├── workers/                  # Optional Redis Streams workers (sanitizer / extractor / judge / embedder / dedup / credit)
├── infra/                    # docker-compose.dev.yml + Postgres schema for the gateway path
├── dist/
│   └── claude-skill/         # The Claude Code skill bundle (drop into ~/.claude/skills/)
│       ├── SKILL.md
│       └── scripts/install.sh, scripts/auto-upload.sh
├── scripts/
│   ├── integration_smoke.sh  # Real-Claude end-to-end
│   ├── verify_all.sh         # Wipe + run every test + build + smoke
│   ├── run-dev.sh            # Bring up gateway+workers locally
│   └── smoke.py              # Original gateway smoke
├── README.md
├── STATUS.md                 # ← you are here
└── ROADMAP.md
```

---

## Running locally in 30 seconds

```bash
# Server
cd core
uv pip install -e ".[server]"
EXP_ROOT=/tmp/expool uvicorn exp_core.server:app --port 8080 &

# CLI (will hit localhost:8080 by default)
cd ../cli
npm install && npm run build
node dist/index.js register --name alice --team platform
node dist/index.js push-skill --bundle ../dist/claude-skill --sensitivity low --acl org
node dist/index.js search-skills --q "share experience"
```

Or run the everything-from-scratch verification:

```bash
./scripts/verify_all.sh
```

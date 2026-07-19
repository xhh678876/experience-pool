# Experience Pool — Product Requirements Document

| | |
|---|---|
| **Product** | Experience Pool |
| **Owner** | Platform team |
| **Status** | v1 implemented, internal-network deployment pending |
| **Last updated** | 2026-04-30 |
| **Repo** | Configure for your deployment |

---

## 1. Problem statement

A fleet of Claude Code agents — operating across many users on the same team
— repeatedly solves the **same problems with no shared learning**.

- Agent A debugs a Kafka offset reset on Tuesday. Agent B hits the same bug
  on Thursday and starts from zero.
- Agent C writes a CSV-aggregation playbook that genuinely works. Agent D
  three desks over rewrites it from scratch.
- A senior engineer's "how I actually do code review" lives in their head
  and is lost when the session ends.

The team has **no mechanism to**:
1. Capture what an agent did, *why* it worked or didn't, and surface it to
   the next agent that might need it.
2. Promote a piece of knowledge ("this script", "this checklist") from one
   agent's local skill to a team-wide reusable artifact.
3. Trust that the surfaced knowledge has been **redacted** (no leaked
   secrets / PII) and **scored** (this playbook actually delivered results
   downstream, not just looks good on paper).

## 2. Goals

| Goal | Measurable target |
|---|---|
| Every completed agent task uploads a structured trajectory | ≥ 80% of sessions on participating agents auto-upload via the Stop hook |
| Agents find prior work before starting from scratch | ≥ 1 search call per multi-step task, p95 < 200 ms cold |
| "Good" playbooks rise to the top automatically | Q-scalar of top-5 results vs random-sample correlation > 0.4 over 4 weeks |
| No leaked secrets in the pool | 0 known-secret patterns reach `review_status=approved`; Layer 1 sanitizer recall > 95% on the canary set |
| Team-shared skills work the same on every agent | `install-skill` round-trip sha256 must match the bundle the author pushed |
| Operators can audit / reverse anything | Every state-changing call has an `audit_log` row; `approved → rejected` reversible |

## 3. Non-goals

- **Replacing Claude Code's plan/execution loop.** This is a sidecar, not
  a runtime.
- **Real-time agent collaboration.** No chat / mailbox / cross-agent RPC.
- **Cross-organization sharing.** Internal-network only in v1.
- **Replacing existing knowledge bases (Notion, internal wikis).** Different
  shape — those are human-edited; this is agent-emitted + agent-consumed.
- **Production-grade observability.** No SLO dashboards, no Grafana, no
  PagerDuty. Internal tools, internal incident response.

## 4. Users

| Role | Job-to-be-done | Touch point |
|---|---|---|
| **Agent** (Claude Code instance) | After finishing a task, share what worked. Before starting one, check what's been tried. | `~/.claude/skills/experience-pool` skill; `exp` npm CLI under the hood |
| **Skill author** (engineer) | Promote a working `SKILL.md` to the team registry; trust that updates flow. | `exp push-skill --bundle ./mything` |
| **Reviewer** (team lead / data steward) | Audit pending uploads, redact further, approve / reject; spot-check sanitizer hits. | Next.js UI at `https://expool.example.com/`; CLI `exp approve / reject` |
| **Operator** (platform engineer) | Run the server, watch disk, do backups. | systemd unit, `/healthz`, `audit_log`, structured logs |
| **ML / data scientist** (offline) | Pull rewards + trajectories for SFT / RM training. | `exp export --out ./data/` (Hive-partitioned Parquet) |

## 5. Scope (v1)

### 5.1 In scope

**Agent side**
- Claude Code skill bundle (`SKILL.md` + install scripts) auto-discovered
  from `~/.claude/skills/`.
- npm-distributed TypeScript CLI (`@experience-pool/cli`).
- HMAC-SHA256 request signing tied to a per-agent credential file.

**Server side**
- Single FastAPI process, SQLite-backed, deployable as a systemd service.
- Three-layer sanitizer: deterministic regex → heuristic PII → optional LLM
  business-sensitivity classifier.
- Extractor double-run with structural-divergence detection.
- Cost-aware 5-dim judge (Haiku for low-stakes, Sonnet 3-shot + ensemble
  for high-stakes).
- One-hop credit assignment: parents and skills earn Q from their
  descendant's reward.
- Mixed retrieval: vector cosine (z-scored) + FTS5 BM25 + accumulated Q +
  UCB exploration.
- ACL: `private` / `team:<X>` / `org`.
- Skill upload + sha256-verified installation.
- Parquet export for offline training.

**Reviewer side**
- Next.js UI: dashboard, list, 5-tab detail page (card / trajectory /
  lineage / skills / audit), skill detail page with Q update history.
- Server actions for approve / reject / edit / re-judge / soft-delete.

### 5.2 Out of scope (v1)

- **TLS / replay protection** — internal network is trusted.
- **Postgres + Redis + Qdrant scale-out** — code exists in `gateway/`,
  not deployed.
- **Real (non-stub) embeddings** — current model is a hash-trigram
  projection; FTS5 keyword pass partially compensates.
- **K8s deployment** — single-process server is enough for v1.
- **Cross-org federation** — listed in roadmap Phase F as research.

## 6. Functional requirements

### F1. Trajectory upload
- **F1.1** Agent POSTs `{trajectory, task_type, source_model, parents, uses_skills, sensitivity, acl, tags}` to `/v1/experiences`.
- **F1.2** Server runs sanitizer before extractor; raw trajectory preserved at `<id>.raw.json` if any layer changed text.
- **F1.3** Extractor produces a structured card (intent, preconditions, script_steps with why/how, tool_capabilities, key_decisions, pitfalls, summary).
- **F1.4** Judge produces 5 reward dimensions in `[-1, 1]` with confidence.
- **F1.5** Declared parents and skills earn one-hop credit before dedup.
- **F1.6** Response within 60 s on `claude -p` Sonnet pipeline; status 202 + experience_id.

### F2. Search
- **F2.1** `GET /v1/experiences/search?q=&task_type=&top_k=&sort=&exploration=`.
- **F2.2** ACL filter applied at search time; private rows invisible to non-owners; team rows invisible to other teams.
- **F2.3** Score = `0.55·sim_z + 0.35·q_z + 0.10·UCB`. Sim itself is `0.7·vector_cosine + 0.3·fts_rank_signal`.
- **F2.4** Each result includes intent, summary, full script, q_breakdown, score components.
- **F2.5** Search increments `visit_count` for retrieved rows; visit_count feeds UCB.

### F3. Skill bundle upload + install
- **F3.1** `POST /v1/skills` with base64 tar.gz of a directory containing `SKILL.md`.
- **F3.2** YAML frontmatter must have `name` and `description`; name slug-validated `[a-z0-9][a-z0-9._\-:]{1,63}`.
- **F3.3** All text files inside the bundle pass through Layer 1 sanitizer.
- **F3.4** `(name, version)` unique. Re-pushing same version → 409. Bump version to update.
- **F3.5** Bundle size ≤ 5 MB total, ≤ 1 MB per file, ≤ 200 files.
- **F3.6** `GET /v1/skills/install?name=&version=` returns base64 bundle. `bundle_sha256` matches what was stored at push time.
- **F3.7** Skills earn Q via the same one-hop loop when an experience declares `uses_skills=[name]`.

### F4. Review
- **F4.1** UI lists pending rows with filters (review_status / task_type / sensitivity).
- **F4.2** Detail tabs: Card (rendered) / Trajectory (raw vs sanitized diff) / Lineage (parent/child SVG graph) / Skills (used by this experience) / Audit (timeline).
- **F4.3** Server actions: approve, reject (with reason), edit (which clears Q + queues `pending_reembed`), re-judge (queues `pending_rejudge`), soft-delete, JSON export.
- **F4.4** Every action logs to `audit_log` with `actor=reviewer:<default_agent_name>` from the authenticated session.
- **F4.5** CLI mirrors: `exp approve / reject / approve-skill / reject-skill`.

### F5. Authentication
- **F5.1** `POST /v1/agents/register {name, team}` issues an HMAC secret. File at `<EXP_ROOT>/credentials/<name>.json`, mode 0600.
- **F5.2** Every non-public request must include `X-Agent-Name` and `X-Signature`. Signature = `hex(hmac_sha256(secret, METHOD\nPATH\nBODY))`.
- **F5.3** Verification middleware uses constant-time compare; bad signature → 401 `{"error":"bad signature"}`.
- **F5.4** Public allowlist: `/healthz`, `/v1/agents/register`, `/docs`, `/openapi.json`.

### F6. Export (offline training)
- **F6.1** `expctl export --out ./data/ --since YYYY-MM-DD --until YYYY-MM-DD --task <X>`.
- **F6.2** Hive-partitioned Parquet: `<root>/task_type=<X>/date=<YYYY-MM-DD>/data.parquet`.
- **F6.3** 36-column row schema: experience metadata + script_steps (struct) + 5 continuous rewards + 5 discrete int8 rewards + Q values + parent_ids + tags.
- **F6.4** Optional PyTorch `Dataset` (lazy torch import).

## 7. Non-functional requirements

| Dimension | Target | How enforced |
|---|---|---|
| Latency (search) | p50 < 50 ms, p95 < 200 ms | Mixed-rank pre-filter on `review_status`; FTS5 limit 200 |
| Latency (push) | p95 < 90 s including 3-shot judge | Cost-aware judge routing; timeouts on `claude -p` |
| Storage | < 5 MB / skill bundle, < 100 KB / experience metadata | Hard caps, sanitizer re-emits text, tar.gz compression |
| Auth | HMAC-SHA256 with constant-time compare | `hmac.compare_digest`; 0600 mode on cred files |
| ACL | Fail-closed on unknown ACL string | `parse_acl` returns `private` for unknown |
| Sanitizer recall | > 95% on canary set of known patterns | Regex coverage tested against `tests/test_sanitize.py` (22 cases) |
| Operability | Single binary boot; <5 min to deploy | systemd unit + `EXP_ROOT` env var; SQLite single file |

## 8. Architecture

```
    Claude Code agent
         │
         ▼  invokes skill (~/.claude/skills/experience-pool)
    SKILL.md (frontmatter triggers on intent phrases)
         │
         ▼  shells out to npm-installed CLI
    `exp` (TypeScript, HMAC-SHA256 signed) ────────HTTP──────▶
                                                                FastAPI (uvicorn)
                                                                ├─ middleware: HMAC verify
                                                                ├─ /v1/experiences (sanitize → extract → judge → embed → dedup → credit)
                                                                ├─ /v1/skills (build_bundle → sanitize each file → tar.gz → store)
                                                                ├─ /v1/admin/* (dashboard, leaderboard)
                                                                └─ SQLite (WAL) + filesystem
```

Key design decisions:
- **One inline pipeline class (`ExperiencePool`)** instead of a worker
  fleet. Simpler to operate, fast enough for ~50 concurrent agents.
- **Vectors + FTS5 in the same SQLite**. No external Qdrant / Elasticsearch.
- **Hash-based deterministic embedding** as a stub. Replaceable via the
  `vectors.kind` payload (which records `embed_model_version`).
- **Skills get the same Q machinery as experiences**. The pool *learns
  which skills actually work* by watching downstream success.
- **Reviewer UI talks to SQLite directly** via `better-sqlite3`. No
  duplicate API; the UI *is* the admin backend.

## 9. Implementation status (current vs PRD)

### Done

| Section | Status | Evidence |
|---|---|---|
| F1 trajectory upload | ✅ | `tests/test_pool_mock.py`, real-Claude `integration_smoke.sh` step 4 |
| F2 search | ✅ | `tests/test_skills.py::test_fts_keyword_search`, real-Claude search returns parent above child |
| F3 skill bundle | ✅ | `tests/test_skills.py` (13 cases), real-Claude push-search-install round-trip with sha256 match |
| F4 review (UI + CLI) | ✅ | Live HTTP smoke against `:3000`, CLI `approve / reject / approve-skill / reject-skill` |
| F5 auth | ✅ | `tests/test_acl.py`, real-Claude tampered-secret returns 401 |
| F6 Parquet export | ✅ | `tests/test_export.py` (5 cases), partition discovery + discrete rewards in {-1,0,1} |
| Three-layer sanitizer | ✅ | 22 sanitizer tests; real-Claude PII trajectory redacts AKIA + email + card + phone |
| One-hop credit assignment | ✅ | Parent `q_update_count` rises 1→2 on child push (real-Claude) |
| Audit log | ✅ | Every state-changing path writes a row; UI Audit tab renders timeline |
| Layout / packaging | ✅ | `STATUS.md`, monorepo, `.gitignore`, Apache 2.0 |

### Not done — internal-network production gaps

| Section | Why blocked | Effort |
|---|---|---|
| systemd unit | not written | 15 min |
| Backup cron | not written | 15 min |
| slowapi rate-limit | not wired | 20 min |
| Real `/healthz` (probes SQLite + disk) | stub | 15 min |
| Disk water-mark + auto-archive | not written | 1 h |
| Real embeddings (BGE / jina / OpenAI) | hash stub in place | 1 h + one-off cost |
| Caddy / nginx config example | not written | 15 min |
| Docker / docker-compose | not written | 30 min |
| Logrotate config | not written | 10 min |

### Not in v1 scope (deferred to roadmap)

- TLS termination + replay protection (Phase C)
- Per-team rate limit + quota (Phase C)
- Reviewer roles + RBAC (Phase C)
- Postgres scale-out (Phase D, code already in `gateway/`)
- Cross-encoder reranker (Phase B)
- Skill versioning UX (Phase E)
- Multi-org federation (Phase F)

## 10. Success metrics

Tracked via `expctl dashboard` after rollout:

| Metric | Target | Read from |
|---|---|---|
| Weekly trajectory uploads / agent | ≥ 5 | `experiences.created_at` |
| Search → push ratio | ≥ 0.5 (every 2 pushes preceded by 1 search) | `search_log` joined with `experiences` by agent + window |
| Top-5 result reuse | ≥ 30% of search hits get re-pushed as a parent within 7 days | `experience_edges` join `search_log` |
| Sanitizer hit rate | < 5% of pushes go to `human_review` (otherwise rules are too aggressive) | `audit_log` action='sanitize' |
| Judge confidence p10 | > 0.6 (otherwise extractor / judge prompts need tuning) | `rewards.confidence` |
| Skill install count vs invoke count | install ≥ 0.5·invoke (skills get installed before they get used) | `skills` table |
| Q-update propagation | every parent_edge has `credit_applied=1` within 5 min | `experience_edges` |
| Failed extractions | < 2% of pushes hit `extraction_status='failed'` | `experiences.extraction_status` |

## 11. Risks

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Failed agent deployments leak secrets through trajectories | High before sanitizer; low after | Layer 1 + audit hold | Mitigated; canary set covers AKIA / sk-ant-* / Stripe / GH / Luhn cards / SSH keys / PEM blocks |
| Single agent runs `while true: exp push` and DDoS's the server | Medium | slowapi rate-limit | **Not yet mitigated — Phase A3** |
| SQLite corruption from power loss | Low | WAL mode + daily `.backup` cron | **Not yet mitigated — Phase A2** |
| Hash embeddings give garbage results, agents stop trusting search | Medium | FTS5 keyword pass partially compensates; real embeddings in Phase B1 | Partially mitigated |
| Reviewer fatigue → no one approves anything | Medium | Auto-approve when sanitizer is clean; only sensitive rows go to review | Mitigated by design |
| Skill author squats a popular name with a bad version | Low | `(name, version)` unique; rejection workflow | Mitigated |
| Disk fills up | Medium | Disk water-mark + auto-archive | **Not yet mitigated — Phase A5** |
| Judge prompt drift over time degrades scores silently | Medium | `expctl drift-record` + `drift-check` (>0.15 MAD alert) | Mitigated by design; needs operator to actually run the cron |
| Server crashes, no auto-restart | Low | systemd `Restart=on-failure` | **Not yet mitigated — Phase A1** |
| Cross-team data leakage via ACL bug | Low | Tested in `test_acl.py`; fail-closed on unknown ACL | Mitigated |

## 12. Milestones

| Milestone | Description | Status | Gate |
|---|---|---|---|
| **v0.1** Standalone in-process | SQLite + filesystem pipeline, mock LLM | ✅ done | 38 tests green |
| **v0.2** Real LLM | `claude -p` extractor + judge, `claude` CLI as backend | ✅ done | parent push 60 s end-to-end |
| **v0.3** Sanitizer + review | Three-layer + Next.js UI | ✅ done | 22 sanitizer tests + UI build |
| **v0.4** Skills | Bundle upload, install round-trip, credit propagation | ✅ done | 13 skill tests + real-Claude integration |
| **v0.5** npm CLI + HTTP server | TypeScript CLI talks to FastAPI server, HMAC verified | ✅ done | round-trip via npm CLI |
| **v0.6** GitHub release | Public repo, STATUS + ROADMAP + PRD + LICENSE | ✅ done | publish under the deployment owner's GitHub org/user |
| **v0.7** Internal-network deploy | Phase A complete: systemd, backup, rate-limit, real healthz | 🔜 next | runs on the team's intranet host without a babysitter |
| **v0.8** Real embeddings | BGE-large-zh or text-embedding-3-large | 🔜 next | search precision@5 measurably improves on a labeled set |
| **v1.0** Hardened internal | Phase C governance items: roles, retention, replay-protection if exposed off-VPN | future | 30-day operational review with no incidents |

## 13. Open questions

1. **Sanitizer rule ownership.** The YAML lives in `core/exp_core/sanitize_rules.yaml`. Should each team be able to add custom rules, or is it global? *Tentative answer: global v1, per-team in v2.*
2. **Reviewer roles.** Currently any human reviewer can approve any row. Move to "reviewers can only approve their own team's rows" before or after broad rollout? *Tentative answer: after, when there's friction.*
3. **Real embedding model.** OpenAI vs local BGE. Local removes a network dep but adds GPU cost. *Tentative answer: text-embedding-3-large for v0.8 (low setup cost), revisit when latency or privacy demands.*
4. **Stop-hook auto-upload by default?** The `auto-upload.sh` exists but isn't auto-wired. Forcing every session to upload is high-leverage but high-blast-radius. *Tentative answer: opt-in env var first, auto-on after a 2-week pilot.*
5. **Trajectory size cap.** A long agent session can be megabytes. Server needs a body-size cap. *Tentative answer: 10 MB body cap, larger trajectories chunked.*

## 14. Changelog

- **2026-04-30**: PRD written. Repo public on GitHub.
- **2026-04-29**: Standalone backend complete (M1–M9). FTS5 added. Real-Claude integration verified. Bug fixes (extractor fallback, mktemp, /tmp symlink). npm CLI + FastAPI server + Claude skill bundle shipped.
- **2026-04-28**: M1–M2 skeleton; first real-Claude end-to-end.

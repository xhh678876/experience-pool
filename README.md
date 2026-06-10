# Experience Pool

面向企业/团队内网、可支撑 100+ agent 的共享经验池。**完整产品由三个部分协同构成**：

1. **一个 Claude Code Skill**，位于 `dist/claude-skill/`（自动安装到 `~/.claude/skills/experience-pool`）。
   当 agent 需要借鉴过往工作、或把自己刚做的事沉淀出来时，就调用这个 skill，它在底层会调用 npm CLI。
2. **一个通过 npm 分发的 CLI**，位于 `cli/`（`@experience-pool/cli`）。本质是一个 HTTP 客户端，请求经 HMAC-SHA256 签名。
   `npm install -g @experience-pool/cli` 即可让所有 agent 用上最新版本。它对接的是：
3. **一个 FastAPI 服务**，位于 `core/exp_core/server.py`。以 SQLite 为存储（无需 Postgres），每个请求都经 HMAC 校验，单进程即可部署。
   启动命令：`uvicorn exp_core.server:app --host 0.0.0.0 --port 8080`。

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

本项目实现了 v2 设计方案，相较最初规范做了如下调整：

1. **反馈是推导出来的，而非主动声明的。** agent 在 push 时声明 `parent_experience_ids`。CreditAssigner 读取新经验的 reward，自动回写到父经验。agent 这一侧没有任何 `feedback` API。
2. **单跳信用分配（one-hop credit assignment）。** reward 只流向直接父节点——以避免 UI 编辑产生反向引用时形成环。
3. **从一开始就采用混合排序。** 自 M2 起，搜索同时使用相似度 z-score、Q 值 z-score 与 UCB 探索项。
4. **内部使用连续 reward。** Judge 产出 `[-1, 1]` 区间的浮点数。只有在导出和 UI 展示时才离散化为 `{-1, 0, 1}`。
5. **Extractor 双跑（double-run）。** 用两套 prompt 各跑一次；当结构化结果的差异超过 0.5 阈值时，标记 `unstable_extraction` 并转入人工 review。
6. **成本感知的 judge 路由。** 低敏感度 / 短轨迹 → Haiku 单次。其余情况 → Haiku/Sonnet 三次自洽（self-consistency）。仅在高敏感度 / 高复用价值时才走 ensemble。
7. **先记信用，再去重。** 即便某条子经验 push 后被发现与池中已有内容重复，它声明的父节点也会在合并前先拿到信用——因为该 agent 确实用到了它们。

## 实际产品的快速开始（Skill + npm CLI + server）

**1. 启动服务**

```bash
cd core
uv pip install -e ".[server]"
EXP_ROOT=/var/lib/expool uvicorn exp_core.server:app --host 0.0.0.0 --port 8080
```

生产环境建议放在 nginx / Cloudflare 之后。该服务单进程运行、以 SQLite 为存储、无任何外部依赖。
存储规模在需要切换到 Postgres 之前可扩展到数 GB；若确实超出，可改用 `gateway/` 下的完整 FastAPI 网关，
它使用 Postgres + Redis + Qdrant。

**2. 分发 npm CLI**

```bash
cd cli
npm install
npm run build
npm publish --access restricted    # to a private GitHub Packages or internal registry
```

agent 端通过 `npm install -g @experience-pool/cli` 安装。版本统一从中心下发——
每个 agent 在下次安装时即可获取新版本。

**3. 在每台 agent 主机上安装 Claude Code Skill**

```bash
EXP_BASE_URL=https://expool.your.corp \
EXP_AGENT_NAME=agent-$(hostname -s) \
EXP_TEAM=platform \
~/experience-pool/dist/claude-skill/scripts/install.sh
cp -r ~/experience-pool/dist/claude-skill ~/.claude/skills/experience-pool
```

至此该 agent 的 skill 目录中就有了 `experience-pool` skill。它会根据 SKILL.md 中的
触发关键词，自动调用 `exp search`、`exp push` 等命令。

## 独立后端的两种运行方式

**MVP 精简路径**（建议优先测试这条）。它跳过 judge / 信用分配 / skills，只走通核心链路：
本地脱敏 + 基于规则的结构化 → HMAC 签名上传 → SQLite + embedding 存储 → 经 ACL 过滤的纯向量搜索。

```bash
./scripts/mvp_smoke.sh
```

对接 FastAPI 服务的 CLI 流程：

```bash
cd core
EXP_ROOT=/tmp/exp-mvp uv run --extra server uvicorn exp_core.server:app --port 8080

# in another shell
cd cli
npm install
EXP_ROOT=/tmp/exp-mvp npm run dev -- register --name alice --team platform
EXP_ROOT=/tmp/exp-mvp npm run dev -- push-lite --file traj.json --task csv_analysis --acl private
EXP_ROOT=/tmp/exp-mvp npm run dev -- search-lite --q "csv revenue by region"
```

## 内网部署

部署相关资产位于 `deploy/`：

- `deploy/expool.service` — FastAPI API 服务，监听 `127.0.0.1:8080`
- `deploy/expool-ui.service` — Next.js UI，监听 `127.0.0.1:3000`
- `deploy/Caddyfile` — 内网反向代理，API 路由到 `:8080`，UI 路由到 `:3000`
- `deploy/Caddyfile.local` — 本地内网网关预览，监听 `:3080`
- `deploy/backup.sh` + `deploy/backup.cron` — SQLite 热备份与文件归档
- `deploy/docker-compose.yml` — 容器化部署的替代方案
- `deploy/README.md` — 内网部署的分步安装指南

本地内网风格预览：

```bash
./scripts/run-intranet-local.sh
# open http://127.0.0.1:3080
```

发布前检查（release gate）：

```bash
./scripts/release_check.sh
```

运行时健康检查：

```bash
curl --noproxy '*' http://127.0.0.1:3080/__gateway/health
curl --noproxy '*' http://127.0.0.1:3080/healthz
exp dashboard   # or signed GET /v1/admin/healthz for deeper checks
```

生产环境安全开关：

```bash
# Require X-Register-Token for legacy /v1/agents/register credential minting.
EXP_REGISTER_TOKEN=<random-long-secret>

# Optional: require X-User-Register-Token for web signup. The Next UI forwards
# this header server-side when EXP_USER_REGISTER_TOKEN is set.
EXP_USER_REGISTER_TOKEN=<random-long-secret>

# Required for /v1/admin/*. If unset, admin endpoints fail closed with 403.
# Callers must also send X-Admin-Token on top of Bearer/HMAC auth.
EXP_ADMIN_TOKEN=<random-long-secret>

# Auto-add Secure to session cookies on HTTPS; set to 1 to force.
EXP_SESSION_COOKIE_SECURE=auto

# Optional local-only claude-fleet monitor. Leave disabled for public releases.
EXP_FLEET_ENABLED=0
```

插件分发检查：

```bash
EXP_UI_PUBLIC_URL=https://<your-public-ui-host>/proxy/3002 \
  ./scripts/check-plugin-deploy.sh
```

该脚本会验证 `/v1/plugin/package`、`/plugins/expool.tgz`、`/plugins/install.sh`
以及 `/plugins` UI 页面，过程中不绑定凭据、不上传轨迹、也不启用自动上传。

**单机模式（Standalone）**（无需 Docker、无需任何基础设施）。SQLite + 文件系统 + 进程内向量。通过 `claude` CLI 调用真实 LLM。

```bash
cd core
uv venv --python 3.11 .venv && uv pip install -e .
uv run expctl register --name agent-a --team platform
uv run expctl push --agent agent-a --task csv_analysis --model claude-sonnet-4-6 \
    --file /tmp/traj.json --sensitivity low
uv run expctl search --agent agent-a --q "rank dimensions in tabular data"
```

**分布式模式（Distributed）**（FastAPI + Postgres + Redis Streams + Qdrant + MinIO）。逻辑与单机模式一致，仅将基础设施拆分出来以支撑规模化。

```bash
cd infra && docker compose -f docker-compose.dev.yml up -d
cd ../gateway && uv sync && uv run uvicorn app.main:app --reload --port 8080 &
cd ../workers && uv sync && uv run python -m workers.pipeline &
uv run python scripts/smoke.py
```

## 目录结构

```
core/       Standalone implementation (SQLite + numpy-style vectors). expctl CLI.
gateway/    FastAPI gateway. Stateless. push / search / get + admin endpoints.
workers/    Pipeline workers (sanitizer, extractor, judge, embedder, dedup, credit).
ui/         Next.js review UI (server actions hit SQLite directly).
infra/      docker-compose, postgres schema, qdrant config, MinIO bootstrap.
scripts/    Smoke tests for the distributed stack.
```

## 审核 UI（Review UI）

`ui/` 下是一个基于 Next.js 的审核界面。在单机模式下，UI *本身就是*后端：
server actions 通过 `better-sqlite3` 直接读写 SQLite，无需另外运行独立的 API。

```bash
cd ui
pnpm install   # or npm install
pnpm dev       # or npm run dev
# open http://localhost:3000
```

可用 `EXP_DB_PATH=/path/to/pool.db` 覆盖数据库位置。页面包括：
看板 `/`、列表 `/experiences`、详情 `/experiences/[id]`（含 Card / Trajectory / Lineage / Audit
标签页，以及操作栏：approve / reject / edit / re-judge / export / soft-delete）。
编辑与重新评判（re-judge）会在辅助表 `pending_reembed` 和 `pending_rejudge` 中入队，
交由 Python sidecar 消费——`core/` 下的 Python schema 文件不会被改动。
完整的 server-action 契约见 `ui/README.md`。

## 闭环验证（Closed-loop verification）

以 `claude` CLI 作为 LLM 后端运行：

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

## 进度（Status）

- [x] M1：骨架搭建（gateway + infra + workers 打通）
- [x] M2：extractor 双跑 + 五维成本感知 judge + 混合排序
- [x] 单机模式（SQLite + 以 claude CLI 作为 LLM）
- [x] expctl CLI（register / push / search / get / dump-audit / stats / dashboard / leaderboard / drift-record / drift-check / issue-credential / acl-search / acl-get / export）
- [x] 单跳信用分配，采用置信度加权的更新
- [x] 去重：由 intent + script + task_type 三元组共同匹配触发
- [x] M3：三层 sanitizer（规则 → 隐私过滤 → LLM 业务敏感度）
- [x] M4：Next.js 审核 UI（`/ui`）
- [x] M5：Q 值监控看板、judge 漂移检测（`exp_core.monitoring`）
- [x] M6：Parquet 导出
- [x] M7：ACL 强制管控（private/team/org）、HMAC 凭据、拒读（denied-read）审计
- [x] M8：看板统计、复用排行榜、漂移基线 + 检查
- [x] M9：skill bundle 的上传（`SKILL.md` + 辅助文件）、搜索、安装，且复用同一套单跳信用分配回路——使 skill 能从下游成功复用它的经验中赚取 Q 值。UI 位于 `/skills`。

## Skills（M9）

agent 可以上传可复用的 bundle（一个带 YAML frontmatter 的 `SKILL.md`，外加任意辅助文件），
其他 agent 则可以搜索 / 安装它们。skill 在信用分配中是**一等公民**：当某条经验 push 时声明了
`--uses-skill foo`，一旦这条经验的 judge reward 到达，foo 的 Q 值就会经由与父经验相同的
`α·c` 单跳更新发生变化。如此一来，池子学到的是*哪些 skill 在实践中真正有效*，而不是听信作者的自评。

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

sanitizer 会作用于 bundle 中的每一个文本文件（`SKILL.md` 以及任意
`.md/.py/.sh/.yaml/.json/.toml/...` 文件）。一旦发现高严重度问题，该 skill 会被卡在
`review_status='pending'` 状态，直到有人工审核通过。系统会记录 bundle 的 SHA-256，
以便安装方校验完整性。bundle 上限为 5 MB、200 个文件；解压时具备抗 tarbomb 能力，
可防御 `..` 路径穿越。

## 闭环集成测试

```bash
./scripts/integration_smoke.sh
```

它会覆盖 sanitizer（PII 脱敏、原始文件保留）、ACL（跨团队隔离 + 拒读审计）、
信用分配（父节点 Q 值随子经验 reward 更新）、监控（看板 + 排行榜）、凭据签发，
以及 Parquet 导出——全部以真实的 `claude` CLI 作为 LLM 驱动。

## Sanitizer（M3）

push 流水线会在写入轨迹与执行 extractor 之间，让每条轨迹都经过一个三层 sanitizer。
extractor 永远看不到原始文本；被索引和 embedding 的，是脱敏后的副本。

**第一层 —— 确定性正则规则。** 始终运行。由 `core/exp_core/sanitize_rules.yaml` 驱动。类别如下：

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

编辑 `sanitize_rules.yaml` 即可新增领域特定规则，无需改动代码。

**第二层 —— 启发式 PII 检测器。** 当 `sensitivity=low` 且第一层未命中时跳过。它会**标记**（而非脱敏）
姓名、地址、出生日期，以及形似信用卡号的连续数字串。审核者可在审计日志中看到这些标记。

**第三层 —— LLM 业务敏感度判定。** 当 `sensitivity=high`、或第二层发现了任何内容时运行。
它让模型将内容分类为 `internal_strategy / unreleased_product / financial_nonpublic /
legal_privileged / personnel / none`。

**状态路由。** 判定结果决定 `experiences.sanitization_status`：

- `done` —— 无发现
- `flagged` —— 第一/二层发现了内容，但未命中高严重度规则
- `human_review` —— 第一层命中了高严重度规则，或第三层判定为敏感

当状态为 `human_review` 时，即便 judge 本应自动通过，`review_status` 也会被强制置为 `pending`。
只要任意一层做出了改动，原始的未脱敏轨迹都会被保存为脱敏文件 `<id>.json` 旁边的
`<id>.raw.json`，供审核者比对究竟脱敏掉了哪些内容。

## 训练数据导出（M6）

经验池可导出为 Hive 分区的 Parquet 数据集，适用于离线训练流水线。分区列为 `task_type`
和 `date`（UTC，由 `created_at` 推导）。每一行将经验与其最新 reward、当前 Q 值、父边、
Q 更新次数连接起来，并附带每个 reward 维度的 `{-1, 0, 1}` 离散化结果。

```bash
# Default: dump everything under <out>/task_type=<X>/date=<YYYY-MM-DD>/data.parquet
uv run expctl export --out ./data/

# Filter by date range and task_type:
uv run expctl export --out ./data/ \
    --since 2026-04-01 --until 2026-04-30 \
    --task csv_analysis
```

PyTorch 用法（torch 为惰性导入——Parquet 导出本身不需要 torch）：

```python
from exp_core.dataset import ExperienceDataset

ds = ExperienceDataset("./data", task_type="csv_analysis")
loader = ds.to_dataloader(batch_size=32)
for batch in loader:
    print(batch[0]["experience_id"], batch[0]["r_outcome_discrete"])
```

## 测试

```bash
cd core && EXP_LLM=mock uv run pytest tests/    # pool + sanitize + acl + monitoring + export
cd gateway && uv run pytest tests/               # 9 tests, pure logic
```

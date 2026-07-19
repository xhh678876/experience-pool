# 经验池项目交接文档

本文档汇总在创智 sii 平台 pod (`<your-deployment-pod>`, <internal-api-host>) 上完成的所有进度，方便迁移到公网服务器继续开发。

最后更新: 2026-06-15（追加召回引擎瓶颈 pilot + 入库层排查结论）

---

## 0. 2026-06-15 增量：召回引擎瓶颈与入库排查结论

这次排查暴露了两层独立问题：一层是“东西没进池”，已基本修好；另一层是“进了池也召不回”，这是当前真正的核心瓶颈。

### 0.1 入库层：大 session 被静默跳过（已修复）

**根因：**
- `dist-public/session-extractor/extract_and_upload.py` 的 `--max-mb` 默认是 `3.0`。大于上限的 session 只计入 `skipped`，不加 `--verbose` 看不到每条被跳过的原因。
- NAVA 这类 4000+ turn 的大 session（例如 71M / 9.3M / 7.4M / 7.0M / 5.9M）因此没有进入池子；`failed=0` 不能说明全量成功。
- `glob("*/*.jsonl")` 只扫两层导致 extractor found 83、递归 find 有 242 的差异，漏掉的大多是 `subagents/agent-*.jsonl` 子 agent 转录；这些属于父 session，不按独立经验上传，暂不算 bug。

**已验证：**
- 用 `--max-mb 50 --verbose` 重跑后，池子数量从 349 增至 385。
- 关键 NAVA 主 session 已通过 extractor dedup 入池，但服务端会重新分配 `experience_id`，不能用本地 uuid 直接判断是否缺失。
- 已确认的映射包括：`10215557 -> 7bde8dfa`、`c2e59542 -> eea82f23`、`051ee0f6 -> 038f1d33`。
- 2026-07 已移除 `--max-mb 3` 默认硬跳过：默认 `--max-mb 0`，Claude 长
  JSONL 以稳定 `seg-0001...` 子会话分段上传并保留 `parent_session_id`；自动
  daemon 同样按分段上传，文件追加时只更新增长的尾段。`--max-mb` 现在仅在用户
  显式设置时作为整文件硬上限。

### 0.2 召回层：进池后仍召不回（核心未解）

pilot 结论：当前 raw session search as-is 基本不胜任“新任务自动召回”。

**现象：**
- 模拟“新开 AVGen-Bench 推理适配任务”的三个查询，top 命中相似度只有 0.20-0.27，真正相关历史经验没有浮上来。
- 最具体的“235 prompt 评测”查询结果反而最差，top 命中是 `hi`。
- 调试台里 `query=avgen` 的 50 个候选分布为 `high=0 / mid=0 / low=6 / far=44`，`sim >= 70%` 强命中为 0；实际注入 top-3 只有 18%-21%。

**确认根因：**
- 卡片 `intent` 直接取 session 第一条 user 消息，349 条里约 68 条是路径、`hi`、shell dump 等垃圾信号。
- 检索面主要是 LiteCard 的 `query + intent + outcome`，且各截断到 512 字，没有覆盖完整 trajectory 或高质量蒸馏摘要。
- “脏 intent + 薄检索面”叠加后，即使经验已经入池，有意义的新任务 query 也很难匹配到。

**下一步修复方向：**
- 卡片重写：用 LLM/worker 将 session 蒸馏成真实 `task_intent + key_steps + outputs + pitfalls`，替换“第一条消息当 intent”的逻辑。
- 索引扩面：embedding 目标改为完整 trajectory 的分块摘要或蒸馏摘要，而不是只嵌 LiteCard 截断字段。
- 对比 `skills-search` 蒸馏片段库与 raw session search 的召回效果，决定自动召回优先走哪条。
- 建一个小评测集：10 个“新任务 query -> 已知 ground-truth eid”，用 recall@5 / MRR 衡量改动前后效果。

---

## 0.3 2026-05-03 增量：完整客户端 + 自动 daemon

从公开网关镜像了完整客户端套件，已经放进 `dist/claude-skill/`，对端只要一行命令就能装上：

```
curl -sSL https://<your-domain>/install.sh | bash
```

**新加的文件：**

| 文件 | 大小 | 作用 |
|---|---|---|
| `dist/claude-skill/scripts/install.sh` | 11 KB | 一键 installer：装 uploader、注册 agent、patch Claude Code hooks、装 systemd/launchd 守护 |
| `dist/claude-skill/exp_uploader.py` | 110 KB | 完整客户端：含 9 个 adapter（Claude Code/Cursor/Hermes/Codex/Continue/Aider/AgentsChat/OpenInterpreter/Generic）|
| `dist/claude-skill/exp_annotator.py` | 23 KB | 离线 LLM judge（5 维 reward + summary） |
| `dist/claude-skill/session_start.sh` | 2.5 KB | Claude Code SessionStart hook，注入 `[task-summary]:` 自标签约定 |

**uploader 子命令全集（直接 `python3 exp_uploader.py --help`）：**

```
register             注册 + 凭据落本机
list-sessions        列本机某个 source 的 session
push                 上传一条
push-latest          --source claude-code 找最新一条 push
push-all             批量推某 source 全部
push-file            指定 trajectory.json 推
export               不走服务端，导出 IR JSONL
annotate-existing    给已上传的 trace 跑 LLM judge 并 POST rewards
get-rewards          拉回 rewards
daemon-tick          一次性增量同步（cron / launchd / systemd 调）
daemon-state         看每个 source 上次同步到哪
daemon-reset         清掉记忆，下次 tick 重扫
```

**uploader 找的本地 trace 路径：**

```
Claude Code:    ~/.claude/projects/*/<sid>.jsonl
Cursor:         ~/Library/Application Support/Cursor/User/{global,workspace}Storage/state.vscdb
Codex:          ~/.codex/sessions/.../*.jsonl
Continue.dev:   ~/.continue/sessions/*.json
Aider:          .aider.chat.history.md (cwd)
Hermes / AgentsChat / OpenInterpreter: 见 exp_uploader.py 各 Adapter 类
```

**为对接它们，新增的后端：**

| 端点 | 文件 | 备注 |
|---|---|---|
| `POST /v1/lite/rewards` | `core/exp_core/server.py` + `lite_rewards.py`（新文件）| 接受 per-turn reward 数组，scope by judge_model |
| `GET /v1/lite/rewards/{eid}` | 同上 | 拉回 rewards |
| `lite_rewards` 表（自动建） | `lite_rewards.py:ensure_schema()` | 不动 schema.py，首次访问时建 |

**端到端验证（已跑通）：**

1. `python3 exp_uploader.py register` → 凭据落到 `~/.experience-pool/credentials/<name>.json`
2. `push-file` 一条多轮 → DB 里多 1 条，trajectory 完整
3. `daemon-tick --dry-run` 自动发现 10 条 Claude Code 会话（含 683 turn 的本会话）+ 6 条 Codex
4. `POST /v1/lite/rewards` + `GET /v1/lite/rewards/<eid>` 双向通

**SessionStart hook 的厉害之处：**

它注入一段 system 提示让 agent 自己在每个子任务结束时输出 `[task-summary]: <一句话>`，uploader 直接拿来作为 intent 字段。**零额外推理成本**——比我们 rule-based 拍前 120 字漂亮太多。

---

## 一、TL;DR — 已经能干什么

```
[远程模型/终端] ── HTTPS+HMAC ──> [FastAPI :8081]  ──写──> [SQLite + sidecar JSON]
                                                              │
                                          [Next.js UI :3002] <┘ (5s 自动轮询)
```

- ✅ 任何内网 pod / 终端都能注册账号 + 上传完整 trace（含工具调用块）
- ✅ Trace 上传时递归脱敏（AKIA、邮箱、phone、IP、SSH key...），高敏自动转人审
- ✅ UI 首页 / 经验库 / 详情页全部市场风格，Trajectory tab 是 IM 气泡 + 工具调用折叠
- ✅ 5s 自动刷新，新 push 自动出现
- ✅ 三种上传姿势：纯 curl、单文件 Python、bridge 批量、npm CLI、Python expctl
- ✅ 跟 `claude_sft_delivery` / `cursor_sft_delivery` 输出对接好了（`bridge_push.py`）

**仍需按部署环境确认（P0/P1）**：HTTPS/反代、监控告警、持久化备份、生产 token 配置 —— 见第八节。

---

## 二、当前运行的服务

| 服务 | 端口 | 进程 | 监听 | 用途 |
|---|---|---|---|---|
| FastAPI API | 8081 | uvicorn | `0.0.0.0` | 给外部 push / search |
| Next.js dev | 3002 | next-server | `0.0.0.0` | UI |
| ~另一个 FastAPI~ | 8080 | uvicorn | 127.0.0.1 only | **不是我们的**，21h 前的旧实例，`EXP_ROOT=/tmp/chuangzhi-expool` |
| ~另一个 Next dev~ | 3000 | next-server | `0.0.0.0` | **不是我们的**，连别的 db |

**我们的两个服务的启动命令**（迁公网时直接套）：

```bash
# API
cd experience-pool/core
EXP_ROOT="$PWD/../.experience-pool" \
EXP_LLM=mock \
EXP_RATE_LIMIT_ENABLED=0 \
uv run uvicorn exp_core.server:app --host 0.0.0.0 --port 8081

# UI（注意：sii proxy 专用 env 公网不要用）
cd experience-pool/ui
EXP_DB_PATH="$PWD/../.experience-pool/pool.db" \
EXP_UI_PUBLIC_URL='https://<your-public-ui-host>/proxy/3002' \
npx next dev -p 3002 -H 0.0.0.0
```

**当前数据库**：`/tmp/exp-mvp/pool.db`（14 条经验，5 带完整 trace，9 是老 lite-only）。
**Trajectory sidecar**：`/tmp/exp-mvp/trajectories/<eid>.json`。
**凭据目录**：`~/.experience-pool/credentials/<agent>.json`（mode 0600）。

⚠️ 注意 `/tmp/exp-mvp/` 在 pod 重启会清空，迁公网必须挪到持久化路径（`/var/lib/expool` 或挂载卷）。

---

## 三、本次会话改了哪些代码

### 3.1 后端（FastAPI / Python core）

**`core/exp_core/server.py`**
- `LitePushReq` 加了 `trajectory / system / tools / meta` 四个可选字段（接收完整会话 IR）
- `lite_push` 调用串通这些字段

**`core/exp_core/lite.py`**
- `push_lite()` 签名增加 `trajectory / system / tools / meta / trajectories_dir`
- **递归脱敏器**：用 `_walk()` 闭包遍历 dict / list / str，对所有字符串叶子跑 layer1；`tool_use_id / id / type / role / name / model` 等路由字段在 `SKIP_KEYS` 白名单不动
- 落盘逻辑：`<eid>.json` sidecar 含 `trajectory + system + tools + meta`（按需）；`experiences.trajectory_path` 列填路径

**`core/exp_core/cli.py` (`expctl`)**
- `push-lite` 默认带 trajectory；加 `--no-trace` 标志回退到老行为

### 3.2 npm CLI

**`cli/src/index.ts`**
- `push-lite` 默认把 `trajectory` 字段塞进 body
- 加 `--no-trace` 标志
- `cli/dist/` 已 build（`npm run build`）

### 3.3 UI（Next.js）

**`ui/proxy.ts`**
- 负责登录重定向和代理前缀路径下的 Location 修正
- Next 16 使用 `proxy.ts` 约定，旧 `middleware.ts` 已移除

**`ui/next.config.mjs`**
- 加 `assetPrefix` 和 `NEXT_PUBLIC_UI_BASE` 派生（`EXP_UI_PUBLIC_URL` env 控制）
- `serverActions.allowedOrigins` + `allowedDevOrigins` 容许 sii proxy
- 公网部署改 `EXP_UI_PUBLIC_URL` 即可

**`ui/components/ui/link.tsx`** (新增)
- 自动给所有 `<Link href="/...">` 加 base 前缀
- 全局 `next/link` 改成这个 wrapper（`app/layout.tsx`、`app/page.tsx`、`app/experiences/page.tsx`、`app/experiences/[id]/page.tsx`、`app/experiences/[id]/_tabs/SkillsTab.tsx`、`app/experiences/[id]/_tabs/LineageTab.tsx`、`app/skills/page.tsx`、`app/skills/[id]/page.tsx`）

**`ui/components/ui/auto-refresh.tsx`** (新增)
- 客户端组件，`router.refresh()` 每 N 秒（默认 5s）
- 导航条右上角绿色 chip 可暂停

**`ui/app/layout.tsx`**
- 杀掉 `cz-silver` 主题（去 className）
- 导航条不再有 `/login` 入口
- 引入 `AutoRefresh`

**`ui/app/page.tsx`** (整页重写)
- Hero：渐变背景 + 大搜索胶囊（query / agent / task / 检索按钮一体）+ 4 chip 样例
- 4 格 metric ribbon
- 任务类型横向 chip 筛选
- 3 列卡片网格

**`ui/app/experiences/page.tsx`** (整页重写)
- 顶部 header + 检索框
- 3 行 chip 筛选条（状态 / 任务 / 敏感度）
- 3 列卡片网格

**`ui/app/skills/page.tsx`** (整页重写)
- 同样 market 风格 + 状态 chip

**`ui/app/experiences/[id]/_tabs/CardTab.tsx`**
- 修了"步骤渲染成空"bug：`script_steps` 字符串数组也能正确渲染
- 加了 query / outcome 显式区块
- 检测 lite ingest 时折叠"前置条件 / 工具能力 / 关键决策 / 风险点"为一行说明（避免显示一堆"无"）

**`ui/app/experiences/[id]/_tabs/TrajectoryTab.tsx`** (整页重写)
- IM 气泡视图：user 靠右、assistant 靠左、tool_use 折叠卡（cyan）、tool_result 折叠卡（橙/红）
- 顶部"气泡 / JSON"两段切换
- 兼容三种格式：Anthropic blocks、扁平 string、OpenAI tool_calls
- 保留"并排查看原始版"

### 3.4 新增脚本

**`scripts/upload.py`** (新增) — 零依赖单文件 Python 脚本，对端拿走就用
- `python3 upload.py health` — 测连通
- `python3 upload.py register --name X --team Y` — 注册 + 凭据落盘
- `python3 upload.py push --agent X --file traj.json [--task ...] [--acl ...]` — 上传

**`scripts/bridge_push.py`** (新增) — SFT delivery 输出批量导入
- 吃 `claude_sft_delivery` / `cursor_sft_delivery` 的输出 JSONL
- 自动检测来源（`system` 字段 / `segment_index` 字段）
- 派生 query / intent / steps / outcome
- 完整 trajectory + system + tools + meta 塞 sidecar

### 3.5 文档

- **`EXTERNAL_UPLOAD.md`**（新增）— 给对端的快速接入指南，3 种姿势
- **`HANDOFF.md`**（本文档）

---

## 四、HTTP API 端点速查

base = `http://127.0.0.1:8081`（公网部署后改成你域名）

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/healthz` | 无 | 健康检查 |
| POST | `/v1/agents/register` | **无** ⚠️公网必须改 | 申请 HMAC 凭据 |
| POST | `/v1/lite/push` | HMAC | 上传 LiteCard + 完整 trace |
| POST | `/v1/lite/search` | HMAC | 检索 |
| POST | `/v1/experiences` | HMAC | 完整路径上传（走 judge/extractor） |
| GET | `/v1/experiences/search` | HMAC | 检索（GET） |
| GET | `/v1/experiences/{id}` | HMAC | 取一条 |
| POST | `/v1/skills` | HMAC | 上传技能 bundle |
| GET | `/v1/skills/search` | HMAC | 搜技能 |
| GET | `/v1/skills/install` | HMAC | 装技能 bundle |
| GET | `/v1/admin/dashboard` | HMAC | 面板数据 |
| GET | `/v1/admin/leaderboard` | HMAC | 复用排行 |

### 4.1 HMAC 签名公式

```
canonical    = METHOD + "\n" + PATH(含query) + "\n" + BODY
X-Signature  = hex(hmac_sha256(secret, canonical))

请求头:
  X-Agent-Name: <agent name 注册时填的>
  X-Signature:  <上面算出来的 hex>
```

参考实现：
- TS: `cli/src/sign.ts:signRequest`
- Py: `core/exp_core/identity.py:sign_request`
- 单文件: `scripts/upload.py:sign`

### 4.2 LitePushReq 完整字段

```python
{
    # 必填
    "query": str,         # 用户原话
    "intent": str,        # 一句话任务描述
    "steps": list[str],   # 步骤数组
    "outcome": str,       # 结果

    # 元数据
    "task_type": str = "misc",
    "source_model": str = "unknown",
    "sensitivity": str = "low",     # low|medium|high
    "acl": str = "private",          # private|team:<X>|public
    "tags": list[str] = [],
    "redactions": dict[str,int] = {},

    # 完整会话 IR（从这里开始都可选）
    "trajectory": list[dict] | None = None,   # ← 完整 messages，含工具调用块
    "system":     list[dict] | str | None = None,   # claude 的 system prompt
    "tools":      list[dict] | None = None,    # 工具 schema
    "meta":       dict | None = None,          # version/entrypoint/source_file/...
}
```

---

## 五、对端怎么上传（四种姿势，新加 ★ 推荐）

### ★ 姿势 0 — `curl ... | bash` 一行装全套（最优）

```bash
curl -sSL https://<your-domain>/install.sh | bash
# 或带参数
env EXP_AGENT_NAME=alice EXP_TEAM=platform EXP_BASE_URL=https://<your-domain> \
  bash -c 'curl -fsSL "$EXP_BASE_URL/install.sh" | bash'
```

完成后：
- `~/.experience-pool/bin/exp` 二进制 wrapper
- `~/.experience-pool/credentials/<name>.json` HMAC 凭据
- Claude Code 的 `Stop` + `SessionStart` hook 已写进 `~/.claude/settings.json`
- macOS launchd / Linux systemd 守护，每 120s 跑 `daemon-tick`，全自动同步本机所有 Claude/Cursor/Codex/... 会话

`install.sh` 现在在 `dist/claude-skill/scripts/install.sh`，部署到公网时用 nginx/Caddy 把它 expose 在 `/install.sh` 就行。

---

### 姿势 A — 单文件 `upload.py`（次推荐，零依赖）

```bash
# 一次性
python3 scripts/upload.py register --base http://<server> --name myagent --team myteam

# 每次上传
python3 scripts/upload.py push --base http://<server> --agent myagent \
    --file my-trajectory.json --task my_task --acl team:myteam
```

trajectory.json 接受三种格式：
- 嵌套 + Anthropic block（推荐）
- 扁平字符串
- OpenAI tool_calls

### 姿势 B — `bridge_push.py`（吃 SFT delivery 输出）

```bash
python3 scripts/bridge_push.py \
    --jsonl claude_sft_delivery/output/run_xxx/extracted.jsonl \
    --base http://<server> --agent claude-sft --secret <hex>
```

自动检测来源、提 query、保留完整 system + tools + meta。

### 姿势 C — npm CLI

```bash
cd cli && npm install && npm run build
exp register --name myagent --team myteam --base http://<server>
exp push-lite --file traj.json --task my_task --acl team:myteam --base http://<server>
```

详细参考 `EXTERNAL_UPLOAD.md`。

---

## 六、UI 路由与功能

通过 sii proxy 访问（迁公网换成你的域名）：

```
.../proxy/3002/                       首页 hero + 卡片网格
.../proxy/3002/experiences            全库列表（chip 筛选）
.../proxy/3002/experiences/<eid>      详情页（5 个 tab）
.../proxy/3002/experiences/<eid>?tab=trajectory   IM 气泡视图
.../proxy/3002/skills                 技能 bundle 列表
.../proxy/3002/skills/<sid>           技能详情
.../proxy/3002/login                  自动 redirect 到 /
```

详情页 tabs：**卡片** / **轨迹**（气泡视图 + 工具调用折叠）/ **血缘** / **技能** / **审计**。底部 ActionBar：通过 / 拒绝 / 编辑 / 重判 / 导出 JSON / 软删除。

---

## 七、目录结构（重点文件）

```
experience-pool/
├── HANDOFF.md                       本文档
├── EXTERNAL_UPLOAD.md               对端接入指南
├── README.md                        老 README，三件套架构说明
│
├── core/                            FastAPI server + Python CLI
│   └── exp_core/
│       ├── server.py                ← LitePushReq 已扩展
│       ├── lite.py                  ← push_lite 已扩展（递归脱敏）
│       ├── cli.py                   ← expctl push-lite 已扩展
│       ├── pool.py                  老的 full path
│       ├── identity.py              HMAC + 凭据
│       ├── sanitize.py              3 层脱敏
│       └── sanitize_rules.yaml      正则规则
│
├── cli/                             npm 分发的 TS CLI
│   └── src/
│       ├── index.ts                 ← push-lite 已扩展
│       ├── client.ts
│       ├── config.ts                凭据加载
│       └── sign.ts                  HMAC 签名
│
├── ui/                              Next.js review UI
│   ├── proxy.ts                     ← login gate + proxy-aware redirects
│   ├── next.config.mjs              ← assetPrefix / allowedOrigins
│   ├── components/ui/
│   │   ├── link.tsx                 ← 新增：proxy-aware Link wrapper
│   │   └── auto-refresh.tsx         ← 新增：5s 自动刷新
│   └── app/
│       ├── layout.tsx               ← 已重写
│       ├── page.tsx                 ← 已重写（market 风格）
│       ├── experiences/page.tsx     ← 已重写（chip + 卡片网格）
│       ├── experiences/[id]/
│       │   ├── page.tsx             5-tab 详情页
│       │   └── _tabs/
│       │       ├── CardTab.tsx      ← 已修 lite 兼容
│       │       └── TrajectoryTab.tsx ← 已重写（IM 气泡）
│       └── skills/page.tsx          ← 已重写
│
├── scripts/
│   ├── upload.py                    ← 新增：单文件上传脚本（零依赖）
│   ├── bridge_push.py               ← 新增：SFT delivery → 经验池
│   └── （...原有 mvp_smoke / release_check 等）
│
├── deploy/                          公网/内网部署脚手架
│   ├── Caddyfile                    反代 + TLS（要改域名）
│   ├── expool.service               systemd unit
│   ├── expool-ui.service
│   ├── docker-compose.yml
│   ├── Dockerfile.api / Dockerfile.ui
│   ├── backup.sh + backup.cron      SQLite 热备
│   └── README.md                    部署指南
│
├── workers/                         pipeline workers (judge/credit/extractor)
└── gateway/                         分布式版 gateway（不是 MVP 路径）
```

---

## 八、迁公网部署 — 必改清单

按"不改的话会被怎么打"分级。

### 🔴 P0：不改就被刷废（**今天必做**）

**1. 锁住公开注册入口**
- legacy `/v1/agents/register` 可用 `EXP_REGISTER_TOKEN` + `X-Register-Token` 保护
- Web 注册可用 `EXP_USER_REGISTER_TOKEN` + `X-User-Register-Token` 保护
- Next UI 会在服务端转发 `EXP_USER_REGISTER_TOKEN`，不会暴露给浏览器

**2. 强制 HTTPS**
- 用 `deploy/Caddyfile` 的反代 + Let's Encrypt（Caddy 自动）
- `deploy/Caddyfile` 第一行 `:80` → 改成你的域名（如 `expool.example.com`）
- 没 HTTPS 注册时 secret 是明文

**3. 速率限制必须开**
- `EXP_RATE_LIMIT_ENABLED=1`（`expool.service` 里默认就是 1，但启动脚本里可能被覆盖，检查）
- Caddy 层加 IP 速率限制（防 DoS）

**4. `/docs` `/openapi.json` 不开公网**
- 现 `Caddyfile` 把 `/docs* /openapi.json /redoc*` 都路由到 8080
- 改：`@api path /v1/* /healthz` 只保留这两个

**5. 真鉴权**
- 当前 UI 已有登录 / session cookie / API key 管理
- 公网建议继续接 OIDC / OAuth；当前写操作基于登录 session 识别 reviewer

### 🟡 P1：会让公网用户体验诡异

**6. 数据持久化**
- 默认本地 `.experience-pool/pool.db` 需要在生产部署时放到持久卷
- `expool.service` 里写的 `EXP_ROOT=/var/lib/expool`，部署时建好这个目录、挂持久卷

**7. UI 的 `EXP_UI_PUBLIC_URL` 改公网域名**
- 本地默认是 localhost；生产部署改成公网/内网稳定域名
- 公网部署改 `https://expool.example.com`，`assetPrefix` 自动更新

**8. ACL 默认 private**
- 直传 `public/org` 会被服务端降级为 `private`
- 进入社区池必须走 `/v1/lite/publish` 严格脱敏门禁

**9. UI 加真鉴权**
- 三选一：OAuth (GitHub/Google)、SSO (OIDC)、最低限度的口令登录
- 修 `ui/lib/auth.ts` + 加 login flow

### 🟢 P2：上线 1-2 周内补

**10. 监控**
- Prometheus metrics endpoint
- structlog → 日志聚合（ELK / Loki）
- Caddy access log 已有

**11. Sanitizer 强化**
- 启用 `lite.py` 的 layer 3 LLM sanitizer（要 API key）
- skill bundle 上传加 magic byte 检查

**12. 备份**
- `deploy/backup.sh` 跑 cron（已有）
- 落地到对象存储（S3 兼容）

---

## 九、迁服务器具体步骤（最小可上线）

假设域名 `expool.example.com`，服务器 Ubuntu 22.04 + sudo + Docker（可选）。

```bash
# 1. clone 仓库
git clone <your-repo> /opt/experience-pool
cd /opt/experience-pool

# 2. 装 uv（Python）+ Node 22 + Caddy
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs caddy

# 3. 准备数据目录 + 凭据
sudo mkdir -p /var/lib/expool/{trajectories,credentials}
sudo useradd --system --home /var/lib/expool expool
sudo chown -R expool:expool /var/lib/expool

# 4. 装依赖、build UI
cd /opt/experience-pool/core && uv pip install -e .[server]
cd /opt/experience-pool/cli  && npm install && npm run build
cd /opt/experience-pool/ui   && npm install && npm run build

# 5. 改三个 env 文件
sudo tee /etc/expool.env > /dev/null <<'EOF'
EXP_ROOT=/var/lib/expool
EXP_LLM=mock                          # 真上 LLM 时换 claude/anthropic
EXP_RATE_LIMIT_ENABLED=1
EXP_REGISTER_TOKEN=$(openssl rand -hex 32)           # P0 #1
EXP_USER_REGISTER_TOKEN=$(openssl rand -hex 32)      # P0 #1
EXP_ADMIN_TOKEN=$(openssl rand -hex 32)
EXP_SESSION_COOKIE_SECURE=1
EXP_UI_PUBLIC_URL=https://expool.example.com         # P1 #7
EXP_DB_PATH=/var/lib/expool/pool.db
EOF

# 6. 改 Caddyfile（关键 3 处：域名、删 /docs、register 速率限制）
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i '1s|.*|expool.example.com {|' /etc/caddy/Caddyfile
sudo sed -i 's| /docs\* /openapi.json /redoc\*||' /etc/caddy/Caddyfile

# 7. 装 systemd unit
sudo cp deploy/expool.service /etc/systemd/system/
sudo cp deploy/expool-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now expool expool-ui caddy

# 8. 备份 cron
sudo cp deploy/backup.cron /etc/cron.d/expool-backup

# 9. 验
curl https://expool.example.com/healthz
./scripts/release_check.sh
```

注：`EXP_REGISTER_TOKEN`、`EXP_USER_REGISTER_TOKEN`、`EXP_ADMIN_TOKEN`
已经由 `core/exp_core/server.py` 读取；部署时配置环境变量即可。

要我现在就把这段补上吗？补完就是直接可生产部署。

---

## 十、SFT delivery 接入

仓库邻居目录里有两个完整的 trace 提取流水线：

- `claude_sft_delivery/` — 从 `~/.claude/projects/*.jsonl` replay cli.js 还原单轮记录
- `cursor_sft_delivery/` — 从 Cursor `state.vscdb` protobuf 解码还原 v13 session

它们各自的 `run.sh` 跑完会出 `output/run_xxx/extracted.jsonl`（claude）或 `v13_training_data.jsonl`（cursor），每行一条记录含 `messages + system + tools + meta`。

直接 `bridge_push.py` 一键导入：

```bash
python3 experience-pool/scripts/bridge_push.py \
    --jsonl claude_sft_delivery/output/run_xxx/extracted.jsonl \
    --base http://<server> \
    --agent claude-sft-import \
    --secret <hex>
```

我已经验过：1 条模拟 claude 记录 → push 后 sidecar 含 `['meta', 'system', 'tools', 'trajectory']`、8 turn、2 个工具 schema、完整 meta（version/entrypoint/source_file/...）。

---

## 十一、当前数据库状态（迁移参考）

```
db: /tmp/exp-mvp/pool.db
共 14 条经验：
  5 条带完整 trace（trajectory_path 已填）
  9 条 lite-only（老数据，没 trace，没法补）

agents 表注册过：
  alice / platform
  bob / platform
  carol / data
  + 几个测试用 (remote-test, hermes-trace-test, sft-bridge, ...)

skills 表 3 条：
  csv-helper / hmac-debug / k8s-oom-triage
```

迁公网时：**db 文件直接 cp 过去**就行（SQLite 是单文件），`/tmp/exp-mvp/trajectories/*.json` sidecar 也一起 cp（路径在 db 里是绝对路径，可能要批量 sed 一下）。

或者更干净：迁过去重新 seed，让对端重新 push。

---

## 十二、剩下的活清单

按优先级（公网部署后接着做）：

| # | 项 | 文件 | 工作量 |
|---|---|---|---|
| 1 | register token 鉴权 | `core/exp_core/server.py` | 30 分钟 |
| 2 | UI 真鉴权（OAuth/SSO） | `ui/lib/auth.ts` + 新 login flow | 1 天 |
| 3 | Trajectory tab 顶部展示 system + tools | `ui/app/experiences/[id]/_tabs/TrajectoryTab.tsx` | 2 小时 |
| 4 | UI 加 `/upload` 页（粘贴 trajectory 一键 push） | 新页 + server action | 半天 |
| 5 | UI 加 `/dashboard` 页（reuse leaderboard, drift, q 分布） | 拼现有 admin endpoint | 半天 |
| 6 | 经验"补 trace"入口（老条目重传） | UI + 后端 patch endpoint | 1 天 |
| 7 | 经验内容哈希去重 | `core/exp_core/lite.py` | 2 小时 |
| 8 | OpenClaw / Hermes 直连 adapter | `collectors/exp_collect/adapters/...` | 3-5 天 |
| 9 | i18n 中英切换 | `ui/lib/i18n.ts` | 半天 |

第 8 项是远期：在 `claude_sft_delivery` / `cursor_sft_delivery` 之外，给 OpenClaw / Hermes 各写一个 adapter，让它们的本地 trace 直接 → IR → push。

---

## 十三、有问题先看哪里

| 症状 | 大概率原因 |
|---|---|
| UI 浏览器看到的是裸 HTML、没样式 | `EXP_UI_PUBLIC_URL` 没设 / 设错；强刷一次 |
| UI 点链接没反应 / 跳到错误页 | 同上，或 `NEXT_PUBLIC_UI_BASE` 没传到客户端 |
| `connection refused` 连 8081 | server 还在 127.0.0.1，改 `--host 0.0.0.0`；或 NetworkPolicy 挡 |
| push 返 401 bad signature | METHOD/PATH/BODY 拼接顺序错；body 序列化和签名时不一致 |
| Trajectory tab 显示"没有 trajectory_path" | 老 push 没带 trajectory，重新 push 一次 |
| Card tab 步骤显示成"步骤 1, 步骤 2..." 没内容 | 已修，强刷 UI |
| 修改代码 UI 没更新 | next dev 大多数热更，layout/middleware 改了要重启 |

---

## 十四、联系方式

迁过去后开发新功能继续在这个仓库的 README 流程里跑：

```bash
cd experience-pool
./scripts/release_check.sh    # 上线前自检
./scripts/integration_smoke.sh # 闭环测试（需 claude CLI）
./scripts/mvp_smoke.sh        # MVP 路径快速验证
cd core && EXP_LLM=mock uv run pytest tests/   # 单测
```

Checkpoint：完成了"完整 trace 收集 + 含工具调用 + UI 渲染 + 自动同步"这个里程碑。

下一个里程碑建议：**P0 三件事补完 + UI 鉴权**，就能开公网。

# 经验池项目交接文档

本文档汇总在创智 sii 平台 pod (`openclaw-xhh--54f30c688f96-vpqqe2elxl`, 10.244.66.195) 上完成的所有进度，方便迁移到公网服务器继续开发。

最后更新: 2026-05-02

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

**未做（P0/P1）**：公网鉴权、HTTPS、注册端点 token、UI SSO、监控告警 —— 见第八节。

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
EXP_ROOT=/tmp/exp-mvp \
EXP_LLM=mock \
EXP_RATE_LIMIT_ENABLED=0 \
uv run uvicorn exp_core.server:app --host 0.0.0.0 --port 8081

# UI（注意：sii proxy 专用 env 公网不要用）
cd experience-pool/ui
EXP_DB_PATH=/tmp/exp-mvp/pool.db \
EXP_DEFAULT_REVIEWER=alice \
EXP_UI_PUBLIC_URL='https://nat2-notebook-inspire.sii.edu.cn/.../proxy/3002' \
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

### 3.3 UI（Next.js 15）

**`ui/middleware.ts`** (新增)
- 拦 `/login` 重定向到 `/`
- 自动写 `X-Reviewer-Name=<EXP_DEFAULT_REVIEWER || alice>` cookie，免登录
- 公网部署必须改：见第八节 P1

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

base = `http://10.244.66.195:8081`（公网部署后改成你域名）

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

## 五、对端怎么上传（三种姿势）

### 姿势 A — 单文件 `upload.py`（推荐）

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
├── ui/                              Next.js 15 review UI
│   ├── middleware.ts                ← 新增：login bypass + 自动登录
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

**1. `/v1/agents/register` 改成需要 token / 关掉**
- 现在公开。任何人都能 `POST /v1/agents/register` 拿 secret
- 修法：在 `core/exp_core/server.py` 的 register handler 里加 `X-Admin-Token` header 检查，env `EXP_REGISTER_REQUIRE_TOKEN` 配置
- 或：所有凭据通过 `expctl issue-credential` 由你手发

**2. 强制 HTTPS**
- 用 `deploy/Caddyfile` 的反代 + Let's Encrypt（Caddy 自动）
- `expool.internal` → 改成你的域名（如 `expool.example.com`）
- 没 HTTPS 注册时 secret 是明文

**3. 速率限制必须开**
- `EXP_RATE_LIMIT_ENABLED=1`（`expool.service` 里默认就是 1，但启动脚本里可能被覆盖，检查）
- Caddy 层加 IP 速率限制（防 DoS）

**4. `/docs` `/openapi.json` 不开公网**
- 现 `Caddyfile` 把 `/docs* /openapi.json /redoc*` 都路由到 8080
- 改：`@api path /v1/* /healthz` 只保留这两个

**5. 重新生成 EXP_DEFAULT_REVIEWER 行为**
- 现 `ui/middleware.ts` 把每个访客自动写成 `alice`
- 公网必须关：把 `DEFAULT_REVIEWER` 默认值清空，或加真鉴权

### 🟡 P1：会让公网用户体验诡异

**6. 数据持久化**
- 当前 `/tmp/exp-mvp/pool.db` 重启就没
- `expool.service` 里写的 `EXP_ROOT=/var/lib/expool`，部署时建好这个目录、挂持久卷

**7. UI 的 `EXP_UI_PUBLIC_URL` 改公网域名**
- 现 hardcode sii proxy URL
- 公网部署改 `https://expool.example.com`，`assetPrefix` 自动更新

**8. ACL 默认改 `private`**
- 现示例都用 `--acl public`，公网部署不能这样

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
EXP_REGISTER_REQUIRE_TOKEN=$(openssl rand -hex 32)   # P0 #1
EXP_DEFAULT_REVIEWER=                                  # P0 #5：清空
EXP_UI_PUBLIC_URL=https://expool.example.com         # P1 #7
EXP_DB_PATH=/var/lib/expool/pool.db
EOF

# 6. 改 Caddyfile（关键 3 处：域名、删 /docs、register 速率限制）
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/expool.internal/expool.example.com/' /etc/caddy/Caddyfile
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

注：步骤 5 里 `EXP_REGISTER_REQUIRE_TOKEN` 加上后，**`server.py` 的 register handler 还没读这个 env**，需要补一段代码：

```python
# core/exp_core/server.py - register handler 顶部加
required_token = os.environ.get("EXP_REGISTER_REQUIRE_TOKEN")
if required_token:
    if request.headers.get("X-Admin-Token") != required_token:
        return JSONResponse({"error": "register requires X-Admin-Token"}, status_code=403)
```

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

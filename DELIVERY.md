# Experience Pool 交付文档

> **经验池平台交付** - 面向 Claude Code / Codex / OpenClaw / Hermes 等 agent 的私有经验库、社区池、项目池和自动 RAG 召回系统。当前交付包括 FastAPI + SQLite 后端、Next.js 门户、agent 插件、自动上传、长 session 智能切分、DO / DO NOT 子经验召回和内网分发包。

## 一、交付内容

- **经验池门户**：支持登录、个人经验库、社区池、插件安装页、API key / pairing code、召回调试页、项目池页面。
- **经验入库链路**：支持 Claude Code / Codex / OpenClaw / Hermes 等本机会话扫描上传；默认写入 private，服务端保留完整 session，同时生成可检索 RAG chunk。
- **召回引擎**：长 session 会切分为 `context -> action -> outcome` 子经验单元，并生成 `do_unit` / `dont_unit`、`trajectory_segment`、`trajectory_overview`、`intent`、`steps`、`outcome` 等 chunk。
- **混合检索**：RAG 召回结合 FTS、关键词/代码 token、trigram 向量和质量分；插件端再做最小分数、条数和字符数过滤，避免把大段低相关经验注入上下文。
- **奖励反馈闭环**：`/v1/rag/context` 为每次召回生成 `event_id`，agent 或用户可通过 `/v1/reuse/feedback` 对召回 chunk 打正/负奖励；服务端会保留反馈事件并小步长更新对应经验的 Q 值。
- **插件分发**：官方插件仓库为 `xhh678876/expool-mcp-plugin`，npm 包为 `@haohui666/expool-plugin`，当前本地构建版本为 `0.3.3`。
- **项目池能力**：后端已提供 project / invite / grant 数据模型和 API；项目池逻辑是“授权读取成员个人池”，不是复制 private 数据到公共池。
- **运维交付**：包含 systemd、Caddy、本地统一网关、Docker compose、备份、logrotate、release check、插件包检查脚本。

## 二、产物位置（交付路径）

生产建议路径：

```text
/opt/experience-pool
```

生产数据目录：

```text
/var/lib/expool
```

本机开发路径含个人工作区信息，上传 GitHub 前不应固化到公开文档；以下以仓库相对路径和生产路径为准。

核心产物：

| 产物 | 路径 | 说明 |
|---|---|---|
| FastAPI 后端 | `core/exp_core/server.py` | 统一 API、鉴权、插件分发、RAG、项目池、用户和 key 管理 |
| RAG 召回引擎 | `core/exp_core/rag.py` | session 切分、chunk 建索引、混合检索、context pack 输出 |
| 项目池模块 | `core/exp_core/projects.py` | project、member、invite、owner grant |
| SQLite 数据库 | `/var/lib/expool/pool.db` | 生产主库；本地开发可用 `.experience-pool/pool.db` |
| 完整 trajectory | `/var/lib/expool/trajectories/` | 每条经验的原始/脱敏后 sidecar |
| Next.js 门户 | `ui/` | Web UI |
| 内网部署脚本 | `deploy/` | systemd、Caddy、备份、Docker |
| 安装器和公网静态文件 | `dist-public/` | `/install.sh`、`exp_uploader.py`、session extractor |
| 插件 tarball | `dist-public/plugins/expool.tgz` | 当前平台 `/plugins/expool.tgz` 分发包 |
| 插件源码仓库 | `../expool-mcp-plugin` | npm / Claude / Codex 插件 |
| 插件构建包 | `../expool-mcp-plugin/dist/haohui666-expool-plugin-0.3.3.tgz` | 当前本地 release artifact |

当前本地开发服务：

| 服务 | 地址 | 说明 |
|---|---|---|
| 统一本地网关 | `http://127.0.0.1:3080` | node local gateway，UI + API 合并入口 |
| API | `http://127.0.0.1:8081` | FastAPI，当前以 4 workers 运行 |
| UI | `https://nat2-notebook-inspire.sii.edu.cn/ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b/project-969649d6-31b8-45af-b6ff-ffb85bbfb3c9/user-ef4936dd-0231-4485-ba30-34e92bf3ea53/vscode/6bf937f8-4826-43cd-b0f6-54f30c688f96/a5119654-19ab-4e0d-9527-bb73a246b9a8/proxy/3002` | Next.js 门户，对外访问入口 |
| 网关健康检查 | `http://127.0.0.1:3080/__gateway/health` | 检查 API / UI 转发 |
| API 健康检查 | `http://127.0.0.1:3080/healthz` | 检查 SQLite 和磁盘 |

当前 SII notebook 可访问入口：

| 入口 | 地址 |
|---|---|
| 经验池门户首页 | `https://nat2-notebook-inspire.sii.edu.cn/ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b/project-969649d6-31b8-45af-b6ff-ffb85bbfb3c9/user-ef4936dd-0231-4485-ba30-34e92bf3ea53/vscode/6bf937f8-4826-43cd-b0f6-54f30c688f96/a5119654-19ab-4e0d-9527-bb73a246b9a8/proxy/3002` |
| 插件安装页 | `https://nat2-notebook-inspire.sii.edu.cn/ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b/project-969649d6-31b8-45af-b6ff-ffb85bbfb3c9/user-ef4936dd-0231-4485-ba30-34e92bf3ea53/vscode/6bf937f8-4826-43cd-b0f6-54f30c688f96/a5119654-19ab-4e0d-9527-bb73a246b9a8/proxy/3002/plugins` |
| API key / pairing code | `https://nat2-notebook-inspire.sii.edu.cn/ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b/project-969649d6-31b8-45af-b6ff-ffb85bbfb3c9/user-ef4936dd-0231-4485-ba30-34e92bf3ea53/vscode/6bf937f8-4826-43cd-b0f6-54f30c688f96/a5119654-19ab-4e0d-9527-bb73a246b9a8/proxy/3002/me/api-keys` |
| API / 安装脚本网关 | `https://nat2-notebook-inspire.sii.edu.cn/ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b/project-969649d6-31b8-45af-b6ff-ffb85bbfb3c9/user-ef4936dd-0231-4485-ba30-34e92bf3ea53/vscode/6bf937f8-4826-43cd-b0f6-54f30c688f96/a5119654-19ab-4e0d-9527-bb73a246b9a8/proxy/3080` |

内网 / 公网迁移时不要继续使用 notebook proxy 地址；以目标环境门户 `/plugins` 页面展示的 gateway 为准。

## 三、组件清单

| 组件 | 状态 | 主要能力 |
|---|---|---|
| `core/` | 代码已交付 | SQLite 单机后端、lite push/search、RAG、ACL、用户、API key、项目池、脱敏 |
| `ui/` | 代码已交付 | 首页、经验库、我的、社区池、技能/插件、召回、项目池、登录注册 |
| `dist-public/` | 代码已交付 | 一键安装脚本、session extractor、OPF bootstrap、agent contract、插件包分发 |
| `expool-mcp-plugin` | 构建包已交付 | Claude Code / Codex / OpenClaw / Hermes 安装、绑定、上传、召回、项目池命令 |
| `deploy/` | 脚本已交付，生产演练待补 | systemd、Caddy、备份、logrotate、Docker compose |
| `gateway/` + `workers/` | 可选扩展，非主流程 | Postgres / Redis / Qdrant / MinIO 规模化路径 |
| 项目池 | 后端/API 已交付，真实项目流待验收 | 创建项目、邀请、授权个人池、项目范围召回 |
| RAG 召回 | 已接入，质量仍需持续评测 | 长 session 切分、DO / DO NOT、混合检索、context pack |

插件版本：

| 项 | 值 |
|---|---|
| npm 包名 | `@haohui666/expool-plugin` |
| 当前版本 | `0.3.3` |
| 官方源码 | `https://github.com/xhh678876/expool-mcp-plugin` |
| 命名说明 | GitHub owner `xhh678876` 与 npm scope `@haohui666` 当前同属官方分发源；后续可统一品牌命名 |
| 平台 tarball sha256 | `76282460a5e4d52b29dbaf62bb4bb3313a1c2aeec818334c40ba226c26f5cee6` |
| 主要命令 | `/expool:search`、`/expool:rag-search`、`/expool:feedback`、`/expool:prep`、`/expool:upload-all`、`/expool:recall-on`、`/expool:projects` |
| Codex 入口 | `/prompts:ep ...` 或安装后的 prompts |

## 四、数据来源与类别

当前平台主要接入的经验来源：

- **Claude Code**：`~/.claude/projects/*/*.jsonl` 主 session；`subagents/agent-*.jsonl` 默认视为父 session 的子转录，不单独作为独立 session 入库。
- **Codex**：`~/.codex/sessions/.../*.jsonl`。
- **Hermes / OpenClaw / Cursor / Aider / Continue / AgentsChat / OpenInterpreter**：由 `dist-public/exp_uploader.py` 和插件 vendor uploader 中的 adapter 识别。
- **手动上传**：`/v1/lite/push`、`exp push-file`、`scripts/bridge_push.py`。
- **技能包**：包含 `SKILL.md` 的 bundle，可进入技能检索和安装流程。

当前开发库快照（2026-06-16 本机只读检查；用于说明数据规模，不作为生产 SLA）：

| 指标 | 数量 |
|---|---:|
| experiences | 2092 |
| 带完整 trajectory 的 experiences | 2029 |
| RAG chunks | 11558 |
| private 经验 | 2076 |
| public 经验 | 15 |
| `team:videogen` 经验 | 1 |
| memory eligible | 1751 |
| SFT eligible | 254 |
| 最大 session turn 数 | 2787 |

RAG chunk 类型分布：

| chunk 类型 | 数量 | 用途 |
|---|---:|---|
| `do_unit` | 4813 | 成功经验单元，用于“应该怎么做” |
| `trajectory_segment` | 2983 | 原始轨迹分段，用于补充上下文 |
| `dont_unit` | 1166 | 失败/报错经验单元，用于“不要怎么做” |
| `intent` | 610 | 任务意图 |
| `outcome` | 610 | 结果摘要 |
| `steps` | 599 | 步骤摘要 |
| `trajectory_overview` | 578 | 长 session 总览 |
| `experience_unit` | 199 | 旧版经验单元 |

## 五、怎么取用

### 1. 门户使用

打开统一入口后（当前 SII 入口见第二节）：

- `/`：总览和搜索入口。
- `/me`：个人经验、撤销、发布、backfill、API key / pairing code。
- `/plugins`：插件安装命令、npm 包、内网 tarball、源码仓库。
- `/recall`：召回调试，检查 query 命中的 RAG chunk。
- `/projects`：创建项目、邀请成员、授权个人池给项目。
- `/community`：查看社区池。

### 2. Agent 插件安装

推荐 npm 安装：

```bash
npx @haohui666/expool-plugin install
```

内网无 npm 时使用平台分发包：

```bash
curl --noproxy '*' -fsSL <gateway>/plugins/install.sh | bash
```

绑定本机：

```bash
npx @haohui666/expool-plugin pair expair_XXXXXXXX
# 或
npx @haohui666/expool-plugin bind-api expk_XXXXXXXX
```

开启自动召回：

```text
/expool:recall-on --targets claude,codex --top-k 2
/expool:recall-on --scope project:<slug> --top-k 3
```

开启自动上传：

```text
/expool:auto-on --sources claude-code,codex,hermes --run-now --verbose
```

### 3. API 使用

推荐 Bearer API key：

```bash
curl "<gateway>/v1/experiences/search?q=FastAPI%20HMAC&topK=5" \
  -H "Authorization: Bearer $EXPK"
```

RAG context pack：

```bash
curl -X POST "<gateway>/v1/rag/context" \
  -H "Authorization: Bearer $EXPK" \
  -H "Content-Type: application/json" \
  -d '{"q":"qzcli spec_id resource_spec_price","top_k":5,"scope":"personal"}'
```

召回反馈：

```bash
curl -X POST "<gateway>/v1/reuse/feedback" \
  -H "Authorization: Bearer $EXPK" \
  -H "Content-Type: application/json" \
  -d '{"event_id":"<event_id>","reward":1,"confidence":0.35,"reason":"helped","final_status":"success"}'
```

旧版 HMAC 仍保留给安装器和兼容场景；新插件优先使用 pairing code / Bearer API key。

## 六、设计与复现

### 6.1 总体设计

经验池不是简单的“全文搜索聊天记录”。当前设计是：

```text
agent session
  -> uploader / plugin
  -> /v1/lite/push
  -> 服务端脱敏 + sidecar 保存完整 trajectory
  -> RAG rebuild
  -> 拆成 do_unit / dont_unit / segment / overview
  -> FTS + trigram vector + lexical token + quality score 混排
  -> /v1/rag/context 返回短上下文包 + event_id
  -> 插件注入到下一次 agent 任务
  -> /v1/reuse/feedback 回传帮助/误导奖励
  -> confidence-weighted EMA 更新经验 Q 值
```

关键点：

- **保留完整 session**：完整 trajectory 仍在 sidecar，命中 chunk 后可以回看完整 session。
- **细粒度 RAG**：检索面不再只依赖整条 session embedding，避免长 session 被平均成“任务类型质心”。
- **DO / DO NOT 分离**：成功单元和失败单元以不同 chunk 类型进入召回包，prompt 使用方式不同。
- **奖励反馈学习**：召回不等于学习；`visit_count` 记录“被检索/展示”，`reuse_count` 记录“收到反馈并参与 Q 更新”。反馈会写入 `reuse_events`、`reuse_items` 和 `q_updates`，正反馈提高后续排序，负反馈压低误召回。同一个 event/chunk 首次反馈生效，重复提交只返回 skipped；`used=false` 只记录标注，不更新 Q。
- **runtime 噪声过滤**：过滤路径、随机 id、tool-use-id、shell dump、上传日志等低价值词。
- **短召回包**：插件默认阈值 `EXPOOL_AUTO_SEARCH_MIN_SCORE=0.32`，默认最多注入 2 条，单次总字数默认 900。

### 6.2 本地复现

启动统一内网网关预览：

```bash
cd <repo>/experience-pool
./scripts/run-intranet-local.sh
```

健康检查：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:3080/__gateway/health
curl --noproxy '*' -fsS http://127.0.0.1:3080/healthz
```

RAG 单测：

```bash
cd <repo>/experience-pool
EXP_DEFER_OPF=1 EXP_LLM=mock python3 -m pytest core/tests/test_lite_http_mvp.py -k "rag_context" -q
```

插件发版检查：

```bash
cd <repo>/expool-mcp-plugin
npm run test:syntax
npm run release:check
npm run release:artifact
npm pack --dry-run
```

将插件包同步到平台：

```bash
cd <repo>/expool-mcp-plugin
EXPOOL_PORTAL_ROOT=../experience-pool npm run release:artifact
```

### 6.3 当前本地健康快照

- `http://127.0.0.1:3080/__gateway/health` 返回 `status=ok`，API 和 UI 在当前本机可达。
- `http://127.0.0.1:3080/healthz` 返回 SQLite OK，`pool.db` 约 116 MB，当前开发机磁盘剩余约 44.76%。
- 当前开发机运行进程包括 local gateway、Next.js UI、FastAPI API。
- RAG 代码已支持长 session 自动切分；库内已有 11558 个 RAG chunks。
- 插件 `0.3.3` 已完成本地 release artifact，并同步到 `dist-public/plugins/expool.tgz`。

以上是本地运行快照，不等价于生产验收。生产验收还需要补齐 systemd 重启演练、备份恢复演练、注册口保护检查、项目池真实用户流和检索评测报告。

## 七、备注与风险

- **检索质量仍需持续评测**：当前已经解决“整 session 粒度错配”的主要问题，但 embedding 仍是 `trigram-256`，不是正式语义 embedding；下一步应引入 BGE / jina / text-embedding 级别模型，并保留 Recall@k / MRR / nDCG 离线评测。
- **项目池当前库内为空**：代码和 API 已存在，但当前开发库 `projects=0`，需要用真实用户流程创建项目、邀请、授权后再做端到端验收。
- **不要暴露注册口**：未设置 `EXP_REGISTER_TOKEN` / `EXP_USER_REGISTER_TOKEN` 时注册口是开放的；生产环境必须显式设置 `EXP_REGISTER_TOKEN`、`EXP_USER_REGISTER_TOKEN`、`EXP_ADMIN_TOKEN`。
- **数据目录要持久化**：生产环境应使用 `/var/lib/expool` 或挂载卷，不能依赖临时目录。
- **备份恢复需要演练**：`deploy/backup.sh` 和 cron 已有，但还需要在目标机器实际做一次恢复演练，确认 `pool.db`、`trajectories/`、`skills/`、`credentials/` 都能恢复。
- **SQLite 扩展边界要写清**：单机 SQLite 是当前主路径；并发/数据量继续增长时应切到 `gateway/` + Postgres / Redis / Qdrant / MinIO 路径。
- **大 session 入库仍要看 skipped**：历史版本曾默认用 `--max-mb 3` 跳过整文件；当前版本默认自动分段且不设整文件上限，但 `--verbose` 仍用于发现无任务、重复、显式硬上限或单段异常。
- **私有库不应做强制公开脱敏拦截**：private 入库以服务端基础脱敏和用户撤销为主；发布到社区池/公共池时再走严格 public check。
- **GitHub 发布前建议**：清理仓库里不应公开的本地路径、内部代理 URL、临时密码重置文件、数据库备份、日志和 `.experience-pool/` 数据目录。

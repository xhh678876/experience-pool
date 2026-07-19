# openclaw-xhh（expool 宿主机）开发运维文档

> 2026-07-12 实地勘察整理。这台机器跑着 expool 经验池的**生产实例**——动手前先读本文件 + 仓库自带的 `README.md` / `HANDOFF.md`，别重造已有机制。

## 1. 机器坐标

| 项 | 值 |
|---|---|
| notebook 名 | `openclaw-xhh` |
| notebook_id | `6bf937f8-4826-43cd-b0f6-54f30c688f96` |
| 所在空间 | 龙虾test空间 `ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b`（PRIVATE） |
| 规格 | 4C / 16G / 无 GPU，节点 hpc-compute201，镜像 `inspire-studio/lyz-dev:99` |
| 创建 | 2026-04-29，长期常驻（勿随手停机——见 §6 数据安全） |
| 持久盘根（下称 `$B`） | `/inspire/hdd/project/qizhilongxia/liangtianyi-253208120278` |
| 外部访问 base | `https://nat2-notebook-inspire.sii.edu.cn/ws-0349f1f3-e433-45b7-a935-1dd1bfaf8f6b/project-969649d6-31b8-45af-b6ff-ffb85bbfb3c9/user-ef4936dd-0231-4485-ba30-34e92bf3ea53/vscode/6bf937f8-4826-43cd-b0f6-54f30c688f96/a5119654-19ab-4e0d-9527-bb73a246b9a8/proxy/3080`（会随 vscode tab 重建变化，权威值读 Mac 上 `~/.config/expool/plugin.json` 的 `base` 字段） |

## 2. 怎么进去控制

```bash
# 单条幂等命令（不关心 cwd/env）
source ~/.skill-git/secrets.env && qzcli login -u "$QZCLI_USER" -p "$QZCLI_PASS"   # cookie 过期时
qzcli exec --timeout 60 6bf937f8-4826-43cd-b0f6-54f30c688f96 'hostname; uptime'

# 多步 stateful 操作 / 多 Claude 并发：用 notebook-term（Jupyter API 每 session 独立 terminal）
# 见 ~/.claude/skills/lark-remote-notebook/SKILL.md 的「notebook-term 速查」
```

注意：`/root` 是 ephemeral（容器重建即清零），**一切要持久的东西放 `$B`**。

## 3. 服务拓扑（全部由 `$B/experience-pool/scripts/babysit.sh` 守护，ppid=1）

```
浏览器/CLI ──▶ vscode proxy /proxy/3080
                    │
              node scripts/local-gateway.mjs  (:3080, 前置路由)
                ├──▶ FastAPI exp_core.server:app  (:8081, 4 workers)   ← 经验池 API 本体
                └──▶ Next.js UI                    (:3002)              ← 门户/看板
另有独立进程：
  claude-fleet dashboard  (:7878, 127.0.0.1, $B/claude-fleet/.venv)    ← 这台机器自己的 session 看板
  exp_uploader daemon-tick（cron 型自归档：claude-code,codex,hermes → 本池）
```

- babysit.sh 检测 500/000/死进程自动重启对应服务；重启服务优先 `kill` 目标进程让 babysit 拉起，不要手工起第二份。
- gateway 注册 token 等 env 在 `$B/experience-pool/.experience-pool/runtime.env`。

## 4. 目录地图

```
$B/
├── experience-pool/            ★ 生产代码仓（详见 §5）
│   ├── core/exp_core/server.py   FastAPI 本体；core/.venv 是它的虚拟环境
│   ├── cli/                      @experience-pool/cli（HMAC HTTP 客户端）
│   ├── ui/                       Next.js 门户
│   ├── workers/                  Sanitizer/Extractor/Judge/CreditAssigner/Embedder
│   ├── scripts/                  babysit.sh · rag_maintenance.py · integration_smoke.sh
│   │                             · mvp_smoke.sh · release_check.sh · local-gateway.mjs
│   ├── dist/claude-skill/        随附的 Claude skill
│   └── *.md                      README/HANDOFF/API_PROTOCOL/PRD/ROADMAP/STATUS/
│                                 SECURITY/OPF_HOST_SETUP/DELIVERY/EXTERNAL_UPLOAD
│   └── .experience-pool/       ★ 数据目录（改代码不碰它）
│       ├── pool.db (+wal/shm)     SQLite 主库 ~320M；旁边多份手工 .bak
│       ├── trajectories/          全量 session 存档（~907MB，2255 份）
│       ├── credentials/ seed/ skills/ eval/
│       ├── server.log · rag-maintenance.log · ui-3002.log
│       └── runtime.env session_secret
├── experience-pool-public/     公开发布用副本（xhh 维护）
├── expool-mcp-plugin/          MCP 插件源码（npm @haohui666/expool-plugin）
├── experience-pool-skill/ FastAPI-OPF/ opf-deploy/   周边组件
└── claude-fleet/               这台机器上的 fleet dashboard
```

## 5. 更新功能的标准流程

1. **先读**：`$B/experience-pool/README.md`（三件套架构：skill → npm CLI → FastAPI）+ `HANDOFF.md` + `ROADMAP.md`；改 API 对齐 `API_PROTOCOL.md`。
2. **⚠️ git 状态特殊**：远端 `git@github.com:xhh678876/experience-pool.git`（xhh 的仓），分支 `public-release`，**工作区有 ~91 个未提交改动 = 生产热修没进 git**。严禁 `git checkout .` / `git pull --rebase` 之类会冲掉工作区的操作；改前先 `git stash list` + 对目标文件 `git diff` 看清楚。
3. 改代码 → 重启：`kill <uvicorn pid>`（babysit 自动拉起新的），UI 改动进 `ui/` 后需 next build（看 `ui/package.json`）。
4. **冒烟**：`bash scripts/integration_smoke.sh`（或 `mvp_smoke.sh`）；对外验证从 Mac 跑 `EXP_CRED_DIR=~/.config/expool python3 ~/.claude/mcp-servers/expool/vendor/exp_uploader.py --base <base> list --limit 3`。
5. RAG 索引维护：`scripts/rag_maintenance.py`（有 `rag-maintenance.log` 和运行前自动 db 快照 `pool.pre-rag-*.db` 的惯例）。
6. 大改 pool.db 前先 `cp pool.db pool.db.bak-<topic>-$(date +%Y%m%d%H%M%S)`（沿用现有命名惯例）。
7. 协作者：这套系统与 **xhh（haohui）** 共建——协议级改动（API/schema）先跟他对齐，别单方面改破坏他的客户端。

## 6. 数据安全（历史教训换来的）

- `pool.db` + `trajectories/` 是唯一真本，**这台 notebook 重建时 `$B` 在持久盘不受影响，但空间/项目变更前务必确认挂载不变**。
- Mac 端有全库镜像兜底：`~/claude-transcript-backups/expool-mirror/`（2026-07-05 快照，907MB + index.tsv），恢复配方见 memory `reference_expool.md`。
- 服务端文件可从 Mac 直接读写：启智 WebDAV `https://file-server.sii.edu.cn:81/<绝对路径>`（Basic auth 用 `inspire_get_sftp_connection_info(storage_name=hdd)` 现取；**必须 :81 + https**）。
- 已知客户端坑（详见 memory `reference_expool.md`）：MCP runner 的 `EXPOOL_BASE` 重装会被重置回错误的 clawsii 域名；上传 size cap 默认 4MB 静默跳过（Mac launchd 已调 256MB，**机上 daemon-tick 仍是 4096KB，改它记得同步**）；gateway 的 `GET /v1/experiences/{eid}` 不支持内联 trajectory，全文只能走 WebDAV 拿 `trajectory_path`。

## 7. 相关入口速查

| 要做什么 | 入口 |
|---|---|
| Mac 上调池子 | MCP `mcp__expool__*` / vendored CLI（带 `EXP_CRED_DIR=~/.config/expool`） |
| 看门户 | `<base>`（即 proxy/3080）浏览器打开 |
| 看这台机器的 agent session | fleet dashboard :7878（机上）或 Mac `fleet search --remote ...`（若注册） |
| 平台层面管理 notebook | inspire-code MCP（`inspire_get_notebook_list` workspace=ws-0349f1f3...）或 qz.sii.edu.cn 网页 |
| 僵尸排队机 | `openclaw-xhh-copy`（32C/128G PENDING 自 05-13，待删或降配重提） |

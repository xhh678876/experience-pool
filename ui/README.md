# 经验池审核 UI

面向独立部署（SQLite）经验池的 Next.js 审核界面。该 UI *同时也是后端*：
server actions 通过 `better-sqlite3` 直接读写位于 `$EXP_DB_PATH`
（默认 `~/.experience-pool/pool.db`）的 SQLite 文件，没有单独的 API 服务。

## 运行

```bash
cd ui
pnpm install   # 或：npm install
pnpm dev       # 或：npm run dev
# 打开 http://localhost:3000
```

首次请求会：

- 打开 `EXP_DB_PATH` 指向的 SQLite 数据库
- 若 `pending_reembed` 与 `pending_rejudge` 辅助表不存在则创建它们
  （**不会**修改 Python 侧的 schema 文件）

指向其他数据库：

```bash
EXP_DB_PATH=/path/to/pool.db pnpm dev
```

## 鉴权

UI 使用基于 FastAPI 的邮箱/密码登录。`/login` 会写入一个 `exp_session`
cookie，server actions 在写入审核状态前，会从该 session 解析当前用户。
只读页面可以以 `anonymous` 身份渲染；审核操作则要求已登录用户，并会向
`audit_log` 追加一行 `actor=reviewer:<default_agent_name>`。

## 页面

- `/` 仪表盘 —— 总量、待审核积压、q 值分布直方图、近 7 天入库趋势 sparkline、
  复用 Top-10 经验。
- `/experiences` 列表 —— 可排序的筛选侧栏（review_status、task_type、
  sensitivity）与意图搜索。
- `/experiences/[id]` 详情 —— 四个标签页：
  - **Card** —— 渲染意图 + 前置条件 + 脚本步骤（带编号，含 why/how）+
    工具能力 + 关键决策 + 踩坑点。侧栏展示最新一条 reward（5 维拆解 + 置信度
    + 理由 + judge 元信息）、当前 Q 状态，以及从 `q_updates` 表派生出的
    Q 值更新历史。
  - **Trajectory** —— 从 `trajectory_path` 读取并美化展示的 JSON。若存在内容不同的
    `<stem>.raw.json` 兄弟文件，会出现一条提示横幅，带有 "show raw side-by-side"
    并排对比开关。
  - **Lineage** —— SVG 关系图：父节点在左、当前节点居中、子节点在右。每条子边在
    `credit_applied=0` 时为虚线琥珀色，在 `credit_applied=1` 时为实线绿色。节点可点击。
  - **Audit** —— 来自 `audit_log` 且按本 `target_id` 过滤的记录，最新优先。
- 底部操作栏：Approve、Reject（需填原因）、Edit、Re-judge、Export JSON、Soft-delete。

## Server actions

定义于 `app/_actions/actions.ts`。所有操作都会写入一条带审核人标记的 `audit_log` 记录。

| 操作      | 效果 |
|-------------|--------|
| `approve`   | 置 `review_status='approved'` |
| `reject`    | 置 `review_status='rejected'`，审计记录中保存原因 |
| `editCard`  | 更新可编辑的 card 字段，置 `review_status='edited'`，重置 `q_update_count=0`，并在 `pending_reembed` 中入队一行供 Python sidecar 处理 |
| `rejudge`   | 在 `pending_rejudge` 中入队一行 |
| `softDelete`| 置 `review_status='rejected'`，向 `tags` 追加 `soft_deleted` |
| `exportJson`| 重定向到 `/api/export/[id]`，流式返回一份 JSON 快照 |

## 说明

- `better-sqlite3` 被声明为 server-external package，因此不会被打包进客户端。
- 所有读写都在进程内完成。通过 `journal_mode=WAL` 降低了与 Python 流水线的锁竞争。
  审核端永远不会修改 `core/` 下的 Python schema 文件。

# 经验池 —— HTTP API 协议

本文是直接与经验池服务通信的底层协议参考。大多数用户应当从门户
（`/plugins`）安装 agent 插件，或使用随附的 `exp` CLI；本文档面向的是想要
手动调用 API 的 **LLM agent** 和 **集成方**。

需要带"在线试用"表单的交互式参考，请看 `/docs`（Swagger UI）。

---

## 1. Base URL

在内网部署中，服务通过门户提供的网关 URL 访问（以 `EXP_BIND_BASE_URL` /
`/plugins` 上展示的为准）。下文所有路径都相对于该网关。

```bash
# notebook 代理示例。请使用 /plugins 上展示的网关地址。
BASE=https://<your-public-api-host>/proxy/3080
```

---

## 2. 鉴权 —— 三选一

服务支持 **三种** 鉴权方式。除公开接口外，每个请求都必须恰好使用其中一种。

### 2a. Bearer API key —— 推荐用于 bot / LLM agent

最简单的方式：在 Web UI 上签发一次 key（或带 session cookie 签发），之后每个
请求都带上它：

```http
Authorization: Bearer expk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- 获取 key：在 `/login` 登录 → 访问 `/me/api-keys` → "新建 key"。
- 或通过 curl 带 session cookie：
  ```bash
  curl -X POST "$BASE/v1/users/me/api-keys" \
       -b cookies.txt \
       -H "Content-Type: application/json" \
       -d '{"name":"my-bot"}'
  ```
  原始 key **只在创建时返回一次**，在 `api_key` 字段里，请保存好。
- key 绑定到你的默认 agent。通过 key push 的经验，归属于该 agent，并遵循
  ACL 规则。
- 每个用户最多 **5 个活跃 key**。请及时吊销不用的。
- 吊销：`DELETE /v1/users/me/api-keys/{key_id}`（需带 session cookie）。

### 2b. HMAC 签名 —— 旧版 / 安装器生成

`curl ... | bash` 安装器会把一份 HMAC 凭据写到
`~/.experience-pool/credentials/<agent>.json`。手动签名方式：

```
canonical = METHOD + "\n" + PATH[?QUERY] + "\n" + BODY
X-Signature = hex(HMAC-SHA256(secret, canonical))
X-Agent-Name = <agent_name>
```

`BODY` 是原始请求体字节（GET 请求为空字符串）。带 query string 的请求，需在
PATH 后包含 `?...`。

### 2c. Session cookie —— 仅限 Web UI

由 `POST /v1/users/login` 设置的 `exp_session=...` cookie。只有
`/v1/users/me/*` 接口接受它；其余接口都需要 Bearer 或 HMAC。

---

## 3. 公开（免鉴权）接口

| Method | Path | 用途 |
|---|---|---|
| GET | `/healthz` | 存活检测 + sqlite/磁盘检查 |
| GET | `/docs` | Swagger UI |
| GET | `/openapi.json` | OpenAPI 3.1 规范 |
| GET | `/plugins/expool.tgz` | 当前内网 `expool-plugin` 的 npm tarball |
| GET | `/plugins/install.sh` | 自包含的内网插件安装器，带 sha256 校验 |
| GET | `/api-protocol` | 本文档 |
| GET | `/install`, `/install.sh` | 自改写安装脚本 |
| GET | `/exp_uploader.py`, `/exp_annotator.py`, … | 安装器侧的 Python 客户端 |
| POST | `/v1/users/register` | 邮箱/密码注册 |
| POST | `/v1/users/login` | 邮箱/密码登录（设置 cookie） |
| POST | `/v1/agents/register` | 签发 HMAC 凭据（旧版）。若设置了 `EXP_REGISTER_TOKEN`，需带 `X-Register-Token`。 |

---

### 3a. 可选的运维锁

以下开关默认关闭，除非运维设置了对应的环境变量。

- `EXP_REGISTER_TOKEN`：保护旧版 `/v1/agents/register`；调用方必须带
  `X-Register-Token: <value>`。
- `EXP_USER_REGISTER_TOKEN`：保护 `/v1/users/register`；设置此环境变量后，
  Next UI 会在服务端转发 `X-User-Register-Token`。
- `EXP_ADMIN_TOKEN`：所有 `/v1/admin/*` 路由必需。若未设置，admin 路由默认
  拒绝（fail closed），返回 403。调用方在常规 Bearer/HMAC 鉴权之外，还须带
  `X-Admin-Token: <value>`。
- `EXP_SESSION_COOKIE_SECURE`：默认 `auto`；设为 `1` 强制使用 Secure
  cookie，设为 `0` 用于本地 HTTP 部署。

## 4. 常见请求/响应结构

### push 一条 "lite" 经验（推荐给 agent 使用）

```bash
curl -X POST "$BASE/v1/lite/push" \
     -H "Authorization: Bearer $EXPK" \
     -H "Content-Type: application/json" \
     -d '{
       "session_id": "demo-001",
       "agent_type":  "claude-code",
       "started_at":  "2026-05-12T10:00:00Z",
       "ended_at":    "2026-05-12T10:05:00Z",
       "query":   "How do I split a tar.gz into 100MB chunks?",
       "intent":  "Split a tar.gz archive into 100MB parts",
       "outcome": "Solved with split -b 100M file.tar.gz part.",
       "steps":   ["check file size", "split -b 100M", "verify chunks"],
       "trajectory": [
         {"role":"user","content":"How to split a tar.gz into 100MB?"},
         {"role":"assistant","content":"Use split -b 100M ..."}
       ]
     }'
```

响应（202 Accepted）：
```json
{
  "experience_id": "...",
  "status": "queued",
  "redactions": { "opf_private_email": 0, "regex_secret": 0 }
}
```

### 搜索

```bash
curl "$BASE/v1/experiences/search?q=tar.gz%20split&topK=5" \
     -H "Authorization: Bearer $EXPK"
```

### 获取单条经验

```bash
curl "$BASE/v1/experiences/<experience_id>" \
     -H "Authorization: Bearer $EXPK"
```

### 发布到社区池

```bash
curl -X POST "$BASE/v1/lite/publish" \
     -H "Authorization: Bearer $EXPK" \
     -H "Content-Type: application/json" \
     -d '{"experience_id":"..."}'
```

---

## 5. 服务端脱敏

每次 push 在入库前都会经过三层处理：

1. **Regex 层** —— token、文件路径、IP 等（`exp_core/sanitize.py`）。
2. **OpenAI Privacy Filter (OPF)** —— 神经网络 PII 检测器（~2.8GB
   transformer，跑在 CPU 上）。能捕获 regex 漏掉的邮箱、人名、电话、
   地址、密钥。
3. **严格公开检查（Strict-public check）** —— 仅在发布时额外应用的规则
   （发布副本中不得含 file://、不得含 localhost URL、不得含 UUID）。

响应里的 `redactions` 字段会显示捕获到了什么。客户端无需预先脱敏；脱敏以服务
端为准。

---

## 6. 错误

所有错误均为 JSON：
```json
{"error": "<short code>", "detail": "<optional human message>"}
```

| HTTP | 含义 |
|---|---|
| 400 | 请求体格式错误 |
| 401 | 缺失 / 无效凭据 |
| 403 | 已鉴权但无权限（ACL、非所有者等） |
| 404 | 未找到 |
| 409 | 冲突（如 api key 配额已满） |
| 422 | 校验错误（FastAPI schema 不匹配） |
| 429 | 被限流 —— 在 `Retry-After` 秒后重试 |
| 500 | 服务端 bug —— 请提 issue |
| 503 | 后端（sqlite、OPF 模型）暂时不可用 |

---

## 7. 限流

按 agent 分桶，60 秒窗口。默认值：

| 分组 | 每分钟 |
|---|---|
| `register` | 30 |
| `push` | 60 |
| `push_skill` | 10 |
| `search` | 1000 |
| `rewards` | 60 |
| `revoke` | 30 |
| `publish` | 30 |
| `quota` | 60 |

可在服务端用 `EXP_RATE_<GROUP>_PER_MIN` 环境变量覆盖。

---

## 8. 版本管理

当前主版本为 `v1`（URL 前缀）。不兼容变更会以新前缀（`v2`）发布，且 `v1`
会在至少一个弃用周期内继续可用。

OpenAPI 规范版本（`info.version` 中）跟随服务端构建，不代表 API 契约。

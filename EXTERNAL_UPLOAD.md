# 经验池快速接入（5 分钟）

任何能访问网关的模型 / 终端，都可以用下面任意一种方式上传经验。重复上传没有副作用——每次都会返回一个新的 `experience_id`，UI 会自动刷新出来。

## 服务地址

把下面的地址换成你自己部署的网关（本地默认如下）：

```
API:  http://127.0.0.1:8080
UI:   http://127.0.0.1:3000
```

## 第 0 步：先确认能连通

```bash
curl -m 4 http://127.0.0.1:8080/healthz
```

返回 `{"status":"ok",...}` 即表示连通。如果返回 `connection refused` / `timed out`，多半是地址 / 端口写错，或防火墙 / 反向代理没放行对应端口。

## 第 1 步：注册一个账号（一次性）

```bash
curl -X POST http://127.0.0.1:8080/v1/agents/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-agent-name","team":"my-team"}'
```

返回的 JSON 里有一个 `secret` 字段，**请妥善保存**——后续所有 push 都用它来签名。

```json
{"agent_id":"...","agent_name":"my-agent-name","team":"my-team","secret":"<hex 64 字符>"}
```

> 注册接口默认开放；生产部署请设置 `EXP_REGISTER_TOKEN` 并在请求头带 `X-Register-Token`。
> 一个 `name` 只能注册一次：用同名重复注册会被拒绝（HTTP 409），避免他人顶替你的凭据。
> secret 弄丢了就在门户 `/me` 页重新签发 API key，或由运维持 `X-Register-Token` 轮换。

## 第 2 步：上传一条经验（任选一种方式）

### 方式 A —— 纯 curl + openssl（任何 Linux 都能跑，无需安装额外依赖）

```bash
SECRET="<上一步拿到的 secret>"
AGENT="my-agent-name"
BASE="http://127.0.0.1:8080"

# trajectory.json 格式：
#   {"trajectory": [
#       {"role":"user", "content":"..."},
#       {"role":"assistant", "content":[
#           {"type":"text","text":"..."},
#           {"type":"tool_use","id":"toolu_1","name":"Read","input":{"file_path":"..."}}]},
#       {"role":"user", "content":[
#           {"type":"tool_result","tool_use_id":"toolu_1","content":"..."}]}
#   ]}

# 组装 LiteCard + trajectory 一并 push
TRAJ=$(jq -c '.trajectory' trajectory.json)        # 提取原始 messages
QUERY=$(jq -r '.trajectory[0].content // "(no query)"' trajectory.json)
LAST_ASST=$(jq -r '[.trajectory[] | select(.role=="assistant")] | last | .content // "(no outcome)"' trajectory.json)

BODY=$(jq -nc \
  --arg q "$QUERY" \
  --arg out "$LAST_ASST" \
  --argjson traj "$TRAJ" \
  '{query:$q, intent:($q | .[0:120]), steps:["..."], outcome:$out,
    task_type:"misc", source_model:"my-model", sensitivity:"low", acl:"public",
    trajectory:$traj}')

SIG=$(printf 'POST\n/v1/lite/push\n%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $NF}')

curl -X POST "$BASE/v1/lite/push" \
  -H "X-Agent-Name: $AGENT" \
  -H "X-Signature: $SIG" \
  -H 'Content-Type: application/json' \
  -d "$BODY"
```

### 方式 B —— Python 脚本（推荐：30 行复制即用）

```python
#!/usr/bin/env python3
import hashlib, hmac, json, sys, urllib.request

BASE   = "http://127.0.0.1:8080"
AGENT  = "my-agent-name"
SECRET = "<上一步拿到的 secret>"

trajectory = json.load(open(sys.argv[1]))["trajectory"]   # 你的 messages 列表
last_user = next((t["content"] for t in reversed(trajectory) if t.get("role")=="user" and isinstance(t.get("content"),str)), "(no query)")
last_asst = next((t["content"] for t in reversed(trajectory) if t.get("role")=="assistant" and isinstance(t.get("content"),str)), "(no outcome)")

card = {
    "query": last_user,
    "intent": last_user[:120],
    "steps": ["..."],
    "outcome": last_asst[:500],
    "task_type": "misc", "source_model": "my-model",
    "sensitivity": "low", "acl": "public",
    "trajectory": trajectory,           # ← 关键：把完整 messages 一并带上
}

body = json.dumps(card, ensure_ascii=False).encode()
sig  = hmac.new(SECRET.encode(),
                b"POST\n/v1/lite/push\n" + body,
                hashlib.sha256).hexdigest()

req = urllib.request.Request(
    BASE + "/v1/lite/push", data=body, method="POST",
    headers={"Content-Type":"application/json", "X-Agent-Name":AGENT, "X-Signature":sig})
print(urllib.request.urlopen(req).read().decode())
```

### 方式 C —— 使用现成的 bridge_push.py

如果你已经有一份 SFT 风格的 `.jsonl`（每行一条记录），可以直接批量 push：

```bash
python3 experience-pool/scripts/bridge_push.py \
    --jsonl <你的 .jsonl> \
    --base  http://127.0.0.1:8080 \
    --agent my-agent-name \
    --secret <hex 64> \
    --task  my-session \
    --acl   team:my-team
```

每行一条记录，会按顺序逐条 push，每条都带完整的 system + tools + messages + meta。

## 验证

打开 UI 首页，右上角会自动刷新，5 秒内你新 push 的内容就会出现。点开即可查看：

- **卡片** tab：query / intent / steps / outcome
- **轨迹** tab：气泡视图 + 工具调用折叠卡片（注意切到“气泡”按钮）
- **审计** tab：你的 agent name + 时间

## 关键提示

- **可以重复上传**——每次都会返回新的 `experience_id`，没有去重逻辑会拦你。
- **必须带 trajectory**——不带的话 UI 上的“轨迹” tab 就是空的。
- **敏感内容会自动脱敏**——AKIA*** key、邮箱、电话、IP 这类信息会被替换成 `<KEY>`、`<EMAIL>` 等占位符；tool_use_id / role 这类路由字段不会被改动。
- **限流**——可通过 `EXP_RATE_*` 环境变量配置，默认开启。
- **ACL**：`private`（仅自己可见）/ `team:<team>`（同 team 可见）/ `public`（所有人可见）。

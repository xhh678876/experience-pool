# 内网经验池快速接入 (5 分钟)

任何在创智内网的模型 / 终端，按下面任一姿势上传经验。重复上传无所谓，每次都得到新的 `experience_id`，UI 会自动刷出来。

## 服务地址

```
API:  http://10.244.66.195:8081
UI:   https://nat2-notebook-inspire.sii.edu.cn/.../proxy/3002/
```

## 第 0 步：先确认能连通

在你的 pod / 终端里跑：

```bash
curl -m 4 http://10.244.66.195:8081/healthz
```

返回 `{"status":"ok",...}` 就通了。返回 `connection refused` / `timed out` 是 k8s 网络策略挡了，找平台运维放行 `10.244.66.195:8081`。

## 第 1 步：注册一个账号（一次性，**无需任何凭据**）

```bash
curl -X POST http://10.244.66.195:8081/v1/agents/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-agent-name","team":"my-team"}'
```

返回的 JSON 里有 `secret` 字段，**保存起来**——所有 push 都用它签名。

```json
{"agent_id":"...","agent_name":"my-agent-name","team":"my-team","secret":"<hex 64 字符>"}
```

> 同一个 name 重复注册会拿到同一个旧 secret，安全。

## 第 2 步：上传一条经验（任选一种）

### 姿势 A — 纯 curl + openssl（任何 Linux 都能跑，无需装任何东西）

```bash
SECRET="<上一步拿到的 secret>"
AGENT="my-agent-name"
BASE="http://10.244.66.195:8081"

# trajectory.json 格式：
#   {"trajectory": [
#       {"role":"user", "content":"..."},
#       {"role":"assistant", "content":[
#           {"type":"text","text":"..."},
#           {"type":"tool_use","id":"toolu_1","name":"Read","input":{"file_path":"..."}}]},
#       {"role":"user", "content":[
#           {"type":"tool_result","tool_use_id":"toolu_1","content":"..."}]}
#   ]}

# 把它压成 LiteCard + trajectory 一并 push
TRAJ=$(jq -c '.trajectory' trajectory.json)        # 提原始 messages
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

### 姿势 B — Python 脚本（推荐：30 行复制粘贴）

```python
#!/usr/bin/env python3
import hashlib, hmac, json, sys, urllib.request

BASE   = "http://10.244.66.195:8081"
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
    "trajectory": trajectory,           # ← 关键：把完整 messages 也带上
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

### 姿势 C — 用我们写好的 bridge_push.py（如果你已经跑过 `claude_sft_delivery` 或 `cursor_sft_delivery`）

```bash
python3 experience-pool/scripts/bridge_push.py \
    --jsonl <SFT_delivery 输出的 .jsonl> \
    --base  http://10.244.66.195:8081 \
    --agent my-agent-name \
    --secret <hex 64> \
    --task  claude_session \
    --acl   team:videogen
```

每行一条记录，会按顺序 push，每条都带完整 system + tools + messages + meta。

## 验证

打开 UI（`...:3002/`）首页右上看自动刷新，5 秒内你的新 push 会出现。点开就能看：

- **卡片** tab：query / intent / steps / outcome
- **轨迹** tab：气泡视图 + 工具调用折叠卡片（注意切到"气泡"按钮）
- **审计** tab：你的 agent name + 时间

## 关键提示

- **重复上传 ok**——每次都得到新 `experience_id`，没有去重逻辑挡你。
- **trajectory 必须带**——不带的话 UI 上"轨迹"tab 就是空的。
- **敏感内容自动脱敏**——AKIA*** key、邮箱、phone、IP 这些会被替换成 `<KEY>` `<EMAIL>` 等占位符。tool_use_id / role 这些路由字段不动。
- **限流目前已关**——push 不会被限速。
- **ACL**：`private`（只你自己看）/ `team:<team>`（同 team 看）/ `public`（全部看）。

# OPF 主机部署 —— 单文件部署指南

> 把这个文件拖到 GPU 机器上，按 3 步走完即可。
> 无需 clone 整个仓库——代码会从主 API
> `http://127.0.0.1:3080` 拉取。

## 你将部署的是什么

一个独立的 **OPF 脱敏服务**，运行在 GPU 主机上，由主经验池 API 通过 HTTP 调用。架构如下：

```
   agent ─HTTP→  main API (127.0.0.1:8080)
                    │  Layer 1 regex (fast)
                    │  DB write — sanitization_status='layer1_only'
                    │  return 202 in ~1s
                    │
                    ▼ async (via worker)
              ┌──────────────────────────────┐
              │  exp_opf_worker (any CPU box)│
              │  poll layer1_only rows       │
              │  ──HTTP──▶ THIS MACHINE      │
              │           opf_service:8085   │
              │           (cuda, 3GB model)  │
              │  update DB → 'done'          │
              └──────────────────────────────┘
```

本文档面向 **这台机器**——即 GPU 主机。主 API 已经在运行。

---

## 第 1 步 —— 安装 OPF 模型 + Python 依赖

```bash
# Python ≥ 3.9 + 一块支持 CUDA 的 GPU + ~4GB 空闲磁盘用于存放权重
pip install --user "git+https://github.com/openai/privacy-filter.git" fastapi uvicorn pydantic

# 首次加载模型会下载 ~3GB 权重到 ~/.opf/privacy_filter
python3 -c "from opf._api import OPF; OPF(model='~/.opf/privacy_filter', device='cuda')"
```

如果模型下载失败（这台机器没有外网出口），需要从一台已有权重的主机手动拷贝 `~/.opf/privacy_filter` 过来。

---

## 第 2 步 —— 拉取服务代码

需要两个文件：`opf_service.py`（FastAPI 应用）和 `opf_filter.py`（OPF 封装层）。二者都由主 API 提供：

```bash
mkdir -p /opt/experience-pool && cd /opt/experience-pool

curl -fsSL http://127.0.0.1:3080/opf_service.py -o opf_service.py
curl -fsSL http://127.0.0.1:3080/opf_filter.py  -o opf_filter.py
chmod +x opf_service.py
```

自检：

```bash
ls -la opf_service.py opf_filter.py
# 两个文件都应在 ~6-9KB 左右。
head -1 opf_service.py
# #!/usr/bin/env python3
```

---

## 第 3 步 —— 启动服务

### 3a —— 手动 / 前台运行（先验证）

```bash
cd /opt/experience-pool

OPF_BIND_HOST=0.0.0.0 \
OPF_BIND_PORT=8085 \
OPF_DEVICE=cuda \
OPF_CHECKPOINT=$HOME/.opf/privacy_filter \
OPF_OPERATING_POINT=balanced \
PYTHONPATH=/opt/experience-pool \
python3 -m uvicorn opf_service:app --host 0.0.0.0 --port 8085 --workers 1
```

它会：
1. 在 `0.0.0.0:8085` 上启动 uvicorn
2. 首次调用 redact 时惰性加载 OPF 模型（~10s）
3. 提供 `/healthz`、`/status`、`/redact-text`、`/redact-batch`、
   `/redact-trajectory` 接口

从内网另一台机器验证：

```bash
# 把 <opf-ip> 换成这台机器实际的 eth0 IP。
curl http://<opf-ip>:8085/healthz
# {"status":"ok"}

curl http://<opf-ip>:8085/status
# {"enabled":true, "loaded":false, "device":"cuda", ...}

curl -X POST http://<opf-ip>:8085/redact-text \
     -H "content-type: application/json" \
     -d '{"text":"my email is alice@example.com"}'
# {"text":"my email is <PRIVATE_FILTER:private_email>", ...}
```

首次调用 /redact 之后再访问一次 /status，`loaded` 应当变为 `true`。

### 3b —— 指定某块 GPU（多卡机器）

```bash
CUDA_VISIBLE_DEVICES=1 \
OPF_DEVICE=cuda \
... (3a 中其余的环境变量) ...
python3 -m uvicorn opf_service:app --host 0.0.0.0 --port 8085
```

`CUDA_VISIBLE_DEVICES=1` 让进程只看到第二块 GPU；进程内部会把它当作 `cuda:0`。

### 3c —— systemd 服务（长期运行）

```bash
sudo tee /etc/systemd/system/opf-service.service <<'EOF'
[Unit]
Description=experience-pool OPF redaction service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/experience-pool
Environment=OPF_BIND_HOST=0.0.0.0
Environment=OPF_BIND_PORT=8085
Environment=OPF_DEVICE=cuda
Environment=OPF_CHECKPOINT=/root/.opf/privacy_filter
Environment=OPF_OPERATING_POINT=balanced
Environment=PYTHONPATH=/opt/experience-pool
# 如需指定 GPU：
# Environment=CUDA_VISIBLE_DEVICES=1
# 可选的共享密钥鉴权（必须与主 API 上的 EXP_OPF_AUTH_TOKEN 一致）：
# Environment=OPF_AUTH_TOKEN=<long-random>
ExecStart=/usr/bin/env python3 -m uvicorn opf_service:app \
    --host 0.0.0.0 --port 8085 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now opf-service
sudo systemctl status opf-service
```

查看日志：

```bash
sudo journalctl -u opf-service -f
```

---

## 这台机器起来之后 —— 对接主 API

（以下操作在 **主 API 机器** 上执行，不是这里。）

```bash
# 加到主 uvicorn 启动参数 / 配置中：
export EXP_DEFER_OPF=1                          # push 在 ~1s 内返回
export EXP_OPF_REMOTE_URL=http://<opf-ip>:8085  # 异步回填会调用这里
# 仅当上面设置了 OPF_AUTH_TOKEN 时才需要：
# export EXP_OPF_AUTH_TOKEN=<same long-random>
```

然后重启主 API。

启动 **OPF 回填 worker**（任意一台能读写 DB、并能连到这个 OPF 服务的机器均可）：

```bash
EXP_DB_PATH=/tmp/exp-mvp/pool.db \
EXP_TRAJECTORIES_DIR=/tmp/exp-mvp/trajectories \
EXP_OPF_REMOTE_URL=http://<opf-ip>:8085 \
nohup python3 scripts/exp_opf_worker.py > /tmp/opf-worker.log 2>&1 &
```

worker 每次有实际处理的 tick 都会打日志。查看进度：

```bash
sqlite3 /tmp/exp-mvp/pool.db \
  "SELECT sanitization_status, COUNT(*) FROM experiences GROUP BY 1"
# layer1_only | <待处理数量>
# done        | <已完成数量>
```

---

## 接口一览（本机，端口 8085）

| method | path | body | returns |
|---|---|---|---|
| GET | `/healthz` | — | `{"status":"ok"}` |
| GET | `/status` | — | 模型加载状态、device、鉴权 |
| POST | `/redact-text` | `{"text": "..."}` | `{text, hits, triggered_high, used}` |
| POST | `/redact-batch` | `{"texts": [..]}` | `{results: [...]}` |
| POST | `/redact-trajectory` | `{"trajectory": [...]}` | `{trajectory, hits, triggered_high}` |

如果设置了 `OPF_AUTH_TOKEN`，除 `/healthz` 外的每个接口都要求带
`X-OPF-Token: <token>` 请求头。

---

## 调优说明

| 关注点 | 调节项 |
|---|---|
| GPU 显存 | ~3-4GB；可用 `CUDA_VISIBLE_DEVICES` 绑定到负载较轻的卡 |
| 模型设备 | `OPF_DEVICE=cuda:1` 等（相对于可见设备而言） |
| 单线程瓶颈 | 在不同 GPU 上跑 >1 个实例，前面挂一个负载均衡器 |
| 召回 vs 精度 | `OPF_OPERATING_POINT=high_recall` / `high_precision` / `balanced` |
| 单次调用超时（worker 侧） | `EXP_OPF_TIMEOUT_SECONDS=60`（默认值） |

---

## 故障排查

启动时报 **`opf package not importable`** —— 模型没装好，重新执行第 1 步。

**`opf load failed`** —— checkpoint 目录缺失。可尝试：
```bash
python3 -c "from opf._api import OPF; OPF(model='~/.opf/privacy_filter', device='cpu')"
```
强制重新下载。

**显存不足（Out of GPU memory）** —— 有别的进程在占用 GPU。用
`nvidia-smi` 检查。可用 `CUDA_VISIBLE_DEVICES` 换一块卡，或退回
`OPF_DEVICE=cpu`（慢很多但能用）。

**主 API 调用本机超时** —— 主 API 对同步调用默认使用
`EXP_OPF_TIMEOUT_SECONDS=8`。开启 `EXP_DEFER_OPF=1` 后它不再做同步调用，慢路径交由 worker（60s 超时）处理。

**客户端收到 `401 unauthorized`** —— 鉴权 token 不匹配。本机的
`OPF_AUTH_TOKEN` 必须与主 API 和 worker 上的 `EXP_OPF_AUTH_TOKEN` 一致。

---

## 任意机器上的快速健康检查

```bash
curl http://<opf-ip>:8085/healthz
curl http://<opf-ip>:8085/status | python3 -m json.tool
curl -X POST http://<opf-ip>:8085/redact-text \
     -H "content-type: application/json" \
     -d '{"text":"contact alice@example.com at 555-0100"}'
```

最后一条请求的预期响应（模型已被首次调用预热后）：

```json
{
  "text": "contact <PRIVATE_FILTER:private_email> at <PRIVATE_FILTER:private_phone>",
  "hits": {"private_email": 1, "private_phone": 1},
  "triggered_high": false,
  "used": true
}
```

# OPF 服务部署指南

OPF（OpenAI Privacy Filter）是脱敏流水线中较慢、依赖 GPU 的环节
（在进程内运行时，每次 push 约需数十秒）。生产环境中，我们把它部署在
**独立机器**上，让主 API 保持快速、无状态。

## 架构

```
  agent ──HTTP──▶ main API (8080, CPU)
                     │
                     ├─ Layer 1 regex sanitize (fast)
                     ├─ DB insert (fast, mark sanitization_status='layer1_only')
                     └─ return 202 in ~1s
                            │
                            ▼ (async)
                  exp_opf_worker (CPU box, near DB)
                       │
                       └──HTTP──▶ opf_service (8085, GPU box)
                                    │
                                    └─ load OPF model on cuda:N
                                       redact trajectory
                                       return cleaned + hits
                       │
                       └─ writes back: sanitization_status='done',
                          updated trajectory file, audit_log entry
```

共三个角色（可分布在三台机器，也可在更少机器上承担多个角色）：

| 角色 | 进程 | 网络职责 |
|------|--------|---------|
| main API | `uvicorn exp_core.server:app --port 8080` | 接收上传 |
| OPF service | `uvicorn opf_service:app --port 8085` | 接收脱敏调用 |
| OPF worker | `python3 scripts/exp_opf_worker.py` | 读取 pool.db，调用 OPF service |

OPF worker 可以跑在 main API 所在机器上（它只需要对 DB 的读写权限，
以及能访问 OPF service 的网络）。

---

## 步骤 1 —— 启动 OPF 服务主机

在 GPU 机器上：

```bash
# 安装 OPF（约需下载 3GB 模型权重）
pip install --user "git+https://github.com/openai/privacy-filter.git"
python3 -c "from opf._api import OPF; OPF(model='~/.opf/privacy_filter', device='cpu')"
# （首次调用会下载权重）

# 获取服务代码（只需 2 个文件：opf_service.py + opf_filter.py）
git clone <repo>  /opt/experience-pool
cd /opt/experience-pool

# 手动前台运行
OPF_DEVICE=cuda CUDA_VISIBLE_DEVICES=0 \
  ./scripts/run_opf_service.sh

# 或以 systemd 服务方式运行
sudo cp deploy/opf-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opf-service
```

验证：

```bash
curl http://<opf-host>:8085/healthz
# {"status":"ok"}

curl -X POST http://<opf-host>:8085/redact-text \
     -H "content-type: application/json" \
     -d '{"text":"my email is alice@example.com"}'
# {"text":"my email is <PRIVATE_FILTER:private_email>", "hits":{"private_email":1}, ...}
```

### 可选：启用共享密钥鉴权

如果 OPF 服务的可达范围超出了你的可信内网，请在服务端设置
`OPF_AUTH_TOKEN=<long-random>`。此时客户端（main API 与 worker）必须在
请求头携带 `X-OPF-Token: <token>` —— 即在其环境变量中设置
`EXP_OPF_AUTH_TOKEN=<token>`。

---

## 步骤 2 —— 让主 API 指向 OPF 服务

在 main API 主机上，配置以下两个新环境变量后重启：

```bash
export EXP_OPF_REMOTE_URL=http://<opf-host>:8085
# export EXP_OPF_AUTH_TOKEN=<token>           # 若设置了 OPF_AUTH_TOKEN
# export EXP_OPF_TIMEOUT_SECONDS=8            # 默认 8s

# （仍然支持：EXP_DEFER_OPF=1 在热路径上完全跳过 OPF）
```

`lite.py` 中的模式判定规则：

| EXP_DEFER_OPF | EXP_OPF_REMOTE_URL | 行为 |
|---|---|---|
| 1 | （任意） | 仅 layer1_only；由 worker 异步回填 |
| 0 | 已设置 | 每次 push 时同步调用远程服务 |
| 0 | 未设置 | 进程内加载 OPF（旧方式，慢） |

**生产环境推荐**：`EXP_DEFER_OPF=1` + 设置远程 URL。
此时 push 约 1s 返回，OPF 由 worker 异步处理。

---

## 步骤 3 —— 启动 worker

在任意对 pool.db 有读写权限的机器上：

```bash
EXP_DB_PATH=/var/lib/expool/pool.db \
EXP_TRAJECTORIES_DIR=/var/lib/expool/trajectories \
EXP_OPF_REMOTE_URL=http://<opf-host>:8085 \
python3 scripts/exp_opf_worker.py --limit 20
```

或单次执行（用于 CI / cron）：

```bash
python3 scripts/exp_opf_worker.py --once --limit 100
```

默认每 15s 轮询一次（`EXP_OPF_WORKER_INTERVAL`）。该 worker 是幂等的——
对同一个 DB 同时运行多个实例也是安全的。

---

## 调优

- **显存**：标准 checkpoint 约占用 3-4GB。若 cuda:0 繁忙，可选用 `OPF_DEVICE=cuda:1`。
- **吞吐**：单个 OPF service worker 一次只处理一次脱敏调用（模型是单线程的）。
  若需更高吞吐，可在不同 GPU 上运行多个服务实例并在前面挂负载均衡
  （或为每个 main API 指定不同的 OPF 主机）。
- **延迟**：对一个较小的 claude-code session，典型的 redact-trajectory 调用耗时
  1-5s。worker 的 `EXP_OPF_TIMEOUT_SECONDS` 应明显高于最慢预期调用（默认 60s）。
- **失败处理**：worker 会在下一轮重试。main API 的同步 OPF 调用在超时后会降级为
  "不脱敏"——相比阻塞用户，先落一行 layer1_only 再异步回填是更好的选择。

---

## 健康检查

主 `/healthz` 会通过 `EXP_OPF_REMOTE_URL` 上报其 OPF 目标。验证整条 worker 链路：

```bash
# 有多少行正在等待 OPF 回填
sqlite3 $EXP_DB_PATH \
  "SELECT COUNT(*) FROM experiences WHERE sanitization_status='layer1_only'"

# 运行 worker 之后
sqlite3 $EXP_DB_PATH \
  "SELECT sanitization_status, COUNT(*) FROM experiences GROUP BY 1"
```

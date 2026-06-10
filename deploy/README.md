# 内网部署说明

推荐的目标机器配置：

- Linux x86_64
- Python 3.11+、Node 22、npm、uv、sqlite3
- Caddy 或 nginx 做内网反向代理
- 数据目录：`/var/lib/expool`
- 代码目录：`/opt/experience-pool`

## 1. 安装

```bash
sudo useradd --system --home /var/lib/expool --shell /usr/sbin/nologin expool
sudo mkdir -p /opt/experience-pool /var/lib/expool /var/backups/expool
sudo chown -R expool:expool /opt/experience-pool /var/lib/expool /var/backups/expool

sudo rsync -a --delete ./ /opt/experience-pool/
cd /opt/experience-pool/core
sudo -u expool uv sync --extra server
cd /opt/experience-pool/ui
sudo -u expool npm install
sudo -u expool npm run build
```

## 2. systemd

生产环境建议先配置安全相关的环境变量：

```bash
sudo install -d -m 0750 -o root -g expool /etc/expool
sudo tee /etc/expool/expool.env >/dev/null <<'EOF'
EXP_BIND_BASE_URL=https://expool.example.com
EXP_REGISTER_TOKEN=<random-long-secret>
EXP_USER_REGISTER_TOKEN=<random-long-secret>
EXP_ADMIN_TOKEN=<random-long-secret>
EXP_SESSION_COOKIE_SECURE=1
EXP_FLEET_ENABLED=0
EOF
sudo chmod 0640 /etc/expool/expool.env
```

```bash
sudo cp /opt/experience-pool/deploy/expool.service /etc/systemd/system/expool.service
sudo cp /opt/experience-pool/deploy/expool-ui.service /etc/systemd/system/expool-ui.service
sudo systemctl daemon-reload
sudo systemctl enable --now expool expool-ui
sudo systemctl status expool expool-ui
```

## 3. 反向代理

```bash
sudo cp /opt/experience-pool/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 默认监听 `:80`，上线前请把 `deploy/Caddyfile` 第一行改成你自己的域名。

发布后对外应只暴露内网页面域名，统一入口的路由分发如下：

- `/`、`/experiences`、`/skills` 等 UI 路由转发到 `127.0.0.1:3000`
- `/v1/*`、`/healthz`、`/docs`、`/openapi.json` 转发到 `127.0.0.1:8080`
- `/__gateway/health` 返回网关自身的健康状态

## 本地内网网关预览

本地调试不要再只看裸 `3000` 端口，建议直接用统一网关预览线上发布形态：

```bash
./scripts/run-intranet-local.sh
```

默认地址：

- 统一入口：`http://127.0.0.1:3080`
- 网关健康检查：`http://127.0.0.1:3080/__gateway/health`
- API 健康检查：`http://127.0.0.1:3080/healthz`
- UI 直连调试：`http://127.0.0.1:3000`
- API 直连调试：`http://127.0.0.1:8080`

如果机器上已安装 Caddy，可以切换到 Caddy 实现的本地网关：

```bash
EXP_GATEWAY_IMPL=caddy ./scripts/run-intranet-local.sh
```

端口可通过 `EXP_GATEWAY_PORT`、`EXP_UI_PORT`、`EXP_API_PORT` 覆盖。
如果当前 shell 配置了 `http_proxy` / `https_proxy`，脚本会自动设置
`NO_PROXY=127.0.0.1,localhost`；手工验证时建议使用 `curl --noproxy '*'`。

## 4. 备份

```bash
sudo install -m 0755 /opt/experience-pool/deploy/backup.sh /opt/experience-pool/deploy/backup.sh
sudo cp /opt/experience-pool/deploy/backup.cron /etc/cron.d/expool-backup
sudo cp /opt/experience-pool/deploy/logrotate.conf /etc/logrotate.d/expool
sudo -u expool EXP_ROOT=/var/lib/expool BACKUP_ROOT=/var/backups/expool /opt/experience-pool/deploy/backup.sh
```

备份内容包括：

- `pool.db` SQLite 热备份
- `trajectories/`
- `skills/`
- `credentials/`

`credentials/` 中含有 HMAC secret，备份目录必须保持 `0700/0600` 权限。

## 5. 验收

```bash
curl --noproxy '*' -fsS http://127.0.0.1:3080/__gateway/health
curl --noproxy '*' -fsS http://127.0.0.1:3080/healthz
curl --noproxy '*' -fsS http://127.0.0.1:3080/
./scripts/mvp_smoke.sh
```

## 6. 发布范围

当前发布的是 MVP Lite 闭环，包含：

- Agent 独立 HMAC 凭据
- 本地脱敏与基于规则的结构化
- `/v1/lite/push`
- `/v1/lite/search`
- SQLite + 向量存储
- private / team / public 三级 ACL
- 内网 UI

评分、信用回流、技能市场暂不作为主流程启用，相关代码仍保留在仓库中。

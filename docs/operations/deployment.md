# 部署 SOP

> 把 Miao AI 部署到一台新机器、或对现有部署做升级 / 回滚的统一操作手册。
> **当前生产**：`yunmiao@yunmiao.site`（Ubuntu 22.04，81.70.216.46），域名 `https://agent.yunmiao.site`。

## 适用场景

- 首次部署到新机器
- 重大版本升级
- 灾难恢复重建

> 想了解"为什么这么部署 / 部署时踩过哪些坑"，看 [`docs/history/2026-06-deployment-process.md`](../history/2026-06-deployment-process.md) 的过程叙事。

---

## 1. 前置要求

### 1.1 服务器

| 项 | 要求 |
|---|---|
| 系统 | Linux x86_64（Ubuntu 22.04+ 推荐） |
| 配置 | 4 核 / 8G RAM 起步（agent 容器每实例建议 384m limit） |
| 端口 | 22（SSH）/ 80 / 443 由 nginx 占，其余容器端口只绑 127.0.0.1 |
| 权限 | sudo 免密 |
| 工具 | Docker / Docker Compose / Git / Node 20+ |

### 1.2 域名 / 网络

- 域名 A 记录解析到服务器公网 IP
- HTTPS 证书（宝塔 / Let's Encrypt / 自有）

### 1.3 基础设施与云服务

| 层 | 服务 | 用途 | 部署方式 |
|---|---|---|---|
| **基础设施** | MySQL 8.4 + Redis 7 | 业务数据库 + 缓存 | 本地 `miao-infra` compose（独立于 miao-ai） |
| **云服务** | Langfuse Cloud | trace 追踪 | SaaS |
| **云服务** | 腾讯云 COS | agent 代码包存储 | SaaS |
| **云服务** | 阿里云 DashScope（或 OpenAI） | LLM provider | SaaS |

> 云服务注册步骤见 [`infra/CLOUD_SETUP.md`](../../infra/CLOUD_SETUP.md)。miao-infra 只需 `docker compose up -d`，不需要注册账号。

---

## 2. 服务器一次性准备

```bash
# 1. 装 Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER

# 2. 配置 systemd 代理（如果服务器在中国大陆/有公网代理）
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/http-proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,::1,.local,github.com,ghcr.io"
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker

# 3. 拉代码
mkdir -p ~/apps && cd ~/apps
git clone git@github.com:moonlight2893267956/miao-ai.git
cd miao-ai
```

> `NO_PROXY` 列表非常重要：docker.io / ghcr.io / 腾讯云镜像仓库都**不能**走代理，否则 build / pull 超时或 502。

---

## 3. 部署步骤

### 3.0 确认基础设施已启动（miao-infra）

miao-ai **不创建自己的 MySQL/Redis 容器**，而是通过 `miao-infra-net` 连接独立的 miao-infra 编排。

```bash
# 1. 确认 miao-infra 已部署
cd ~/apps/miao-infra
docker compose ps
# 应该看到 miao-mysql + miao-redis 两个容器 running

# 2. 如果还没创建 miao_ai 库（只需执行一次）
docker exec miao-mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" -e "
  CREATE DATABASE IF NOT EXISTS miao_ai
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  GRANT ALL PRIVILEGES ON miao_ai.* TO 'miao'@'%';
  FLUSH PRIVILEGES;
"
```

### 3.1 写 `.env`

```bash
cd ~/apps/miao-ai
cp .env.example .env
# 编辑 .env，填入生产凭证：
#   DATABASE_URL=mysql+aiomysql://miao:PASSWORD@miao-mysql:3306/miao_ai?charset=utf8mb4
#   （注意 host 是 miao-mysql —— miao-infra 里的容器名，不是 localhost）
```

> `ENCRYPTION_KEY` 必须是 32 字节 URL-safe base64。生成新 key：
> ```bash
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```
> **如果是从旧库迁移**：必须用旧的 `ENCRYPTION_KEY`，否则所有已加密的 provider API key 都解不开。

### 3.2 拉镜像 + 启动

```bash
docker compose -f docker-compose.prod.yml pull        # 可选：先拉基础镜像
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps          # 等 backend 状态 healthy
```

### 3.3 数据库迁移

```bash
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
```

### 3.4 初始化登录用户

```bash
docker compose -f docker-compose.prod.yml exec -T backend python -c "
import asyncio
from sqlalchemy import text
from app.db import engine

async def main():
    async with engine.begin() as conn:
        await conn.execute(text(\"\"\"
            INSERT INTO users (username, password, is_active)
            VALUES ('admin', 'your-password', true)
            ON DUPLICATE KEY UPDATE username=username
        \"\"\"))
asyncio.run(main())
"
```

> 项目不开放注册。初始 admin 用户由 DBA 手动插入。

### 3.5 域名反代

通过宝塔或现有 nginx 加站点：

```nginx
server {
    listen 443 ssl;
    server_name agent.yunmiao.site;
    # ... 证书配置（宝塔 / Let's Encrypt）...

    # SSE 流式接口避免缓冲
    location ~ ^/api/v1/agents/.*/invoke/stream$ {
        proxy_pass http://127.0.0.1:18000;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:18000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:13000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 3.6 验收

```bash
# 容器状态
cd ~/apps/miao-ai
docker compose -f docker-compose.prod.yml ps

# 公网健康检查
curl -i https://agent.yunmiao.site/api/v1/health
# 预期：200 {"status":"ok"}
curl -i https://agent.yunmiao.site/api/v1/health/ready
# 预期：200 {"status":"ready","db":"ok"}

# 登录 → 调 agent → 看 Langfuse
# 浏览器开 https://agent.yunmiao.site
```

---

## 4. 升级流程

```bash
# 本地先提交并 push
git add . && git commit -m "..." && git push origin main

# 服务器
cd ~/apps/miao-ai
git pull --ff-only origin main
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
docker compose -f docker-compose.prod.yml ps  # 等 backend healthy
```

---

## 5. 已知陷阱

| 陷阱 | 后果 | 解决 |
|---|---|---|
| **Docker build 慢** | 服务器 build 几分钟到几十分钟 | 后端 Dockerfile 默认用阿里云 Debian / PyPI 源（`BUILD_APT_MIRROR=mirrors.aliyun.com` / `BUILD_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/`） |
| **bridge 网络隔离** | agent 容器起在 `bridge` 网，backend 容器在 `miao-ai_default` 网，互相访问不到 | 后端用 shared_network + container_name DNS 解决（commit `b751320`） |
| **apt 不读 HTTP_PROXY** | `apt-get` 在容器内不走代理 | 后端 Dockerfile 单独配 `/etc/apt/apt.conf.d/proxy.conf`，代理地址用 **bridge 网关 `172.17.0.1:7890`**（不是 `127.0.0.1`，那是容器自己） |
| **ENCRYPTION_KEY 丢失** | 所有 provider 加密数据解不开 | **必须备份 `.env` 到密码管理器**；换 key 要写脚本重加密所有 provider 记录（`security.md` §轮换） |
| **NO_PROXY 误配** | docker.io / ghcr.io 走代理，502 | systemd 配置 NO_PROXY 时 `github.com,ghcr.io` 不能漏 |
| **healthcheck SyntaxError** | backend 一直 unhealthy 但 HTTP 200 | docker-compose.prod.yml healthcheck URL 必须用单引号包：`urllib.request.urlopen('http://...')` |
| **NEXT_PUBLIC_API_BASE** | 浏览器调后端路径 404 | 必须配成公网域名（如 `https://agent.yunmiao.site`）+ nginx `/api` 反代到 backend |
| **watchdog 回收后冷启动 5-10s** | 第一次 invoke 慢 | 5 分钟空闲自动 stop 是设计行为，**不调 IDLE_TIMEOUT** 除非有强需求 |

完整踩坑叙事：[`docs/history/2026-06-deployment-process.md`](../history/2026-06-deployment-process.md)

---

## 6. 回滚

### 6.1 应用回滚

```bash
cd ~/apps/miao-ai
git log --oneline -5
git checkout <previous-good-commit>
docker compose -f docker-compose.prod.yml up -d --build
```

### 6.2 停服

```bash
cd ~/apps/miao-ai
docker compose -f docker-compose.prod.yml down
```

### 6.3 DB 回滚

```bash
# 谨慎：alembic downgrade 不可逆操作多
docker compose -f docker-compose.prod.yml exec backend alembic downgrade -1
```

优先策略：**回滚应用代码** > **DB downgrade**。登录表相关迁移（`users` / `user_sessions`）会影响所有用户。

### 6.4 nginx 回滚

```bash
# 删除 / 禁用 agent.yunmiao.site vhost
sudo nginx -t && sudo systemctl reload nginx
```

---

## 7. 部署架构

| 服务 | 容器内端口 | 宿主机绑定 | 镜像 |
|---|---:|---:|---|
| `miao-backend` | 8000 | `127.0.0.1:18000` | `python:3.12-slim` + 自构建 |
| `miao-frontend` | 3000 | `127.0.0.1:13000` | `node:20-slim` + pnpm |
| `miao-{agent-name}` | 8080 | **不暴露**（用容器名 DNS） | per-agent 镜像 |

> MySQL + Redis 由 `miao-infra` compose 独立管理，不在此表。miao-backend 通过 `miao-infra-net` 连接 `miao-mysql:3306`。

公网只暴露 nginx 443。后端和 frontend 容器只 bind `127.0.0.1`。

**环境变量**：
- 后端：读根 `.env`（生产凭证全部在这里）
- 前端：构建时注入 `NEXT_PUBLIC_API_BASE` / `NEXT_PUBLIC_LANGFUSE_*`（运行时不再读 env）
- backend 容器挂载 `/var/run/docker.sock` 用于 docker 模式管理 agent 容器
- backend 容器挂载 `miao-agent-work` volume 到 `/tmp/miao/agents`（agent 工作目录）

详见 [`docker-compose.prod.yml`](../../docker-compose.prod.yml) 和 [`backend/Dockerfile`](../../backend/Dockerfile)。

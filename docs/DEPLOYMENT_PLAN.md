# Miao AI 部署计划

> 目标：将 Miao AI 部署到 `yunmiao@81.70.216.46`，通过 `https://agent.yunmiao.site` 对外访问。
> 当前状态：本地已准备登录功能与生产 Docker/Compose 部署配置，并完成本地验证；服务器已开始部署但仍需同步最新代码、修复 nginx 反代并完成最终验证。
> 本文记录计划和已完成的本地准备工作，不包含真实密钥。

## 1. 已确认环境

### 本地项目

- 仓库：`git@github.com:moonlight2893267956/miao-ai.git`
- 分支：`main`
- 当前部署策略：先提交并推送本地改动，再由服务器从 GitHub 拉取。
- 当前工作区已准备生产 Docker / Compose 配置文件，但尚未提交推送：
  - `.dockerignore`
  - `backend/Dockerfile`
  - `frontend/Dockerfile`
  - `docker-compose.prod.yml`
- 当前工作区已准备登录态相关代码和数据库迁移：
  - 后端登录 / 登出 / 当前用户接口
  - `users` / `user_sessions` 表迁移
  - 前端登录页、登录态检查和退出登录入口

### 服务器

- 主机：`81.70.216.46`
- 用户：`yunmiao`
- 系统：Ubuntu 22.04 LTS
- 已确认可用：
  - `git`
  - Docker / Docker Compose
  - Node 20.11.1
  - nginx / 宝塔 nginx
  - 免密 `sudo`
  - GitHub SSH 访问
- 当前不适合直接原生运行的原因：
  - 服务器系统 Python 是 3.10，项目要求 Python 3.11+
  - 服务器未安装 `uv`
  - 服务器未安装 `pnpm`
- 当前端口情况：
  - `80` / `443` 已由 nginx 占用
  - `3000` 已被现有服务占用
  - Docker 容器应只绑定到 `127.0.0.1` 的未占用端口

### 域名

- 目标域名：`agent.yunmiao.site`
- DNS：已解析到 `81.70.216.46`
- HTTPS：沿用宝塔 / 现有 nginx 证书管理流程

## 2. 部署架构

采用 Docker Compose 管理两个应用服务：

| 服务 | 容器端口 | 宿主绑定 | 说明 |
|---|---:|---:|---|
| `miao-backend` | `8000` | `127.0.0.1:18000` | FastAPI / Uvicorn |
| `miao-frontend` | `3000` | `127.0.0.1:13000` | Next.js production server |

公网只暴露 nginx 的 `80` / `443`。

反向代理规则：

- `https://agent.yunmiao.site/api/` -> `http://127.0.0.1:18000/api/`
- `https://agent.yunmiao.site/` -> `http://127.0.0.1:13000/`

前端生产环境变量：

```bash
NEXT_PUBLIC_API_BASE=https://agent.yunmiao.site
```

后端继续使用根 `.env` 中的云服务配置：

- `DATABASE_URL`
- `LANGFUSE_*`
- `TENCENT_*`
- `COS_ENDPOINT`
- `DASHSCOPE_*`
- `ENCRYPTION_KEY`

不得把真实密钥提交到 Git。

## 3. 已完成的本地准备

### 3.1 生产部署文件

已新增以下生产部署文件：

- `.dockerignore`
  - 排除 `.git`、缓存、虚拟环境、`node_modules`、`.env` 等内容
  - 保留示例环境文件
- `backend/Dockerfile`
  - 基于 `python:3.12-slim`
  - 从 `docker:27-cli` 拷贝 Docker CLI，供 docker runtime 管理 agent 容器
  - 默认禁用基础镜像自带 Debian source，改用 `mirrors.aliyun.com` Debian 源；可通过 `BUILD_APT_MIRROR` / `BUILD_APT_SECURITY_MIRROR` 调整
  - 默认使用 `mirrors.aliyun.com` PyPI 源；可通过 `BUILD_PIP_INDEX_URL` / `BUILD_PIP_TRUSTED_HOST` 调整
  - 安装 `build-essential`、`curl`、`uv`
  - 安装 backend 包依赖
  - 复制 `app`、`agent_templates`、`alembic` 和 `alembic.ini`
  - 启动命令：`uvicorn app.main:app --host 0.0.0.0 --port 8000`
- `frontend/Dockerfile`
  - 基于 `node:20-slim`
  - 使用 `pnpm@8.15.9`
  - 支持构建时注入 `NEXT_PUBLIC_API_BASE`、`NEXT_PUBLIC_LANGFUSE_BASE_URL`、`NEXT_PUBLIC_LANGFUSE_PROJECT_ID`
  - 执行 `pnpm build`
  - 启动命令：`pnpm start`
- `docker-compose.prod.yml`
  - 定义 `backend` / `frontend` 两个服务
  - 后端使用根目录 `.env`
  - 前端只接收 `NEXT_PUBLIC_*` 构建参数，避免注入后端密钥
  - 镜像构建代理使用 `BUILD_HTTP_PROXY` / `BUILD_HTTPS_PROXY`，避免把运行时 `HTTP_PROXY` 注入后端
  - 后端映射 `127.0.0.1:18000:8000`
  - 前端映射 `127.0.0.1:13000:3000`
  - 后端默认 `agent_runtime_mode=docker` / `AGENT_RUNTIME_MODE=docker`
  - 后端挂载 `/var/run/docker.sock`，用于 docker 模式管理 agent 容器
  - 后端挂载 `miao-agent-work:/tmp/miao/agents`
  - 配置 `restart: unless-stopped`
  - 后端包含 `/api/v1/health` 健康检查

### 3.2 登录功能准备

已在本地工作区准备简易登录功能：

- 登录用户由数据库维护，不提供注册入口
- 密码按当前要求明文存储，不做加密
- 登录成功后写入服务端 session，并通过 cookie 维持登录态
- 接口调用通过当前登录态鉴权
- 未登录访问受保护接口时返回未授权状态
- 前端启动后先检查当前登录态，未登录展示登录页

### 3.3 本地验证结果

已完成以下本地验证：

```bash
cd backend
source .venv/bin/activate
python -m compileall app tests
pytest
```

结果：`21 passed`

测试数据清理检查：

- 测试生成的 agents：`0`
- 测试生成的 users：`0`
- 测试生成的 providers：`0`

前端构建：

```bash
cd frontend
CI=true pnpm build
```

结果：构建通过。

Compose 配置检查：

```bash
docker compose -f docker-compose.prod.yml config
```

结果：配置可解析。注意该命令会读取本地 `.env`，不要把输出中的真实密钥写入文档或提交记录。

本地 Docker 镜像构建曾启动验证，但受 Docker Hub 基础镜像下载速度影响中断；未观察到代码层面的构建错误。实际镜像构建留到服务器部署时执行。

## 4. 发布流程

### 4.1 本地提交

部署前必须先完成本地提交，避免服务器部署到不完整状态。

```bash
git status
git add <changed-files>
git commit -m "Add login auth and Docker deployment config"
git push origin main
```

提交前建议验证：

```bash
cd backend
source .venv/bin/activate
pytest

cd ../frontend
CI=true pnpm build
```

### 4.2 服务器拉取代码

```bash
/Users/wuxiangyi/Desktop/script/server_connect.sh yunmiao
mkdir -p /home/yunmiao/apps
cd /home/yunmiao/apps

if [ ! -d miao-ai ]; then
  git clone git@github.com:moonlight2893267956/miao-ai.git
fi

cd /home/yunmiao/apps/miao-ai
git checkout main
git pull --ff-only origin main
```

### 4.3 配置生产环境变量

在服务器创建 `/home/yunmiao/apps/miao-ai/.env`。

要求：

- 只在服务器保存真实密钥
- 不提交 `.env`
- `DATABASE_URL` 使用 asyncpg 可识别的连接格式
- `ENCRYPTION_KEY` 必须与已有 provider 加密数据兼容；如果是新库，可新生成

生成新 `ENCRYPTION_KEY`：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4.4 启动服务

```bash
cd /home/yunmiao/apps/miao-ai
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

### 4.5 数据库迁移

```bash
cd /home/yunmiao/apps/miao-ai
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
```

### 4.6 初始化登录用户

登录用户由数据库配置，不提供注册功能。

```sql
INSERT INTO users (username, password, is_active)
VALUES ('admin', 'your-password', true);
```

如果需要重置密码：

```sql
UPDATE users
SET password = 'new-password'
WHERE username = 'admin';
```

## 5. nginx / HTTPS 计划

通过宝塔或现有 nginx 配置新增站点：

- `server_name agent.yunmiao.site`
- 证书：宝塔申请或绑定现有证书
- HTTP 自动跳转 HTTPS

反向代理要求：

```nginx
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
```

SSE 流式接口需要避免代理缓冲影响：

```nginx
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
```

配置完成后验证：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 6. 验证清单

### 容器状态

```bash
cd /home/yunmiao/apps/miao-ai
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs backend --tail=100
docker compose -f docker-compose.prod.yml logs frontend --tail=100
```

### 后端健康检查

```bash
curl -i https://agent.yunmiao.site/api/v1/health
curl -i https://agent.yunmiao.site/api/v1/health/ready
```

预期：

- `/health` 返回 `200`
- `/health/ready` 返回 `200`，并包含 DB ready 信息

### 登录态

未登录：

```bash
curl -i https://agent.yunmiao.site/api/v1/auth/me
```

预期：`401`

登录：

```bash
curl -i -c /tmp/miao-cookie.txt \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}' \
  https://agent.yunmiao.site/api/v1/auth/login

curl -i -b /tmp/miao-cookie.txt \
  https://agent.yunmiao.site/api/v1/auth/me
```

预期：登录成功后 `/auth/me` 返回当前用户。

### 前端

- 浏览器打开 `https://agent.yunmiao.site`
- 未登录时展示登录页
- 登录后进入控制台
- 模型管理页能正常读取 provider / model
- Agent 列表能正常读取

## 7. 回滚方案

### 应用回滚

```bash
cd /home/yunmiao/apps/miao-ai
git log --oneline -5
git checkout <previous-good-commit>
docker compose -f docker-compose.prod.yml up -d --build
```

### 容器回滚 / 停服

```bash
cd /home/yunmiao/apps/miao-ai
docker compose -f docker-compose.prod.yml down
```

### nginx 回滚

- 删除或禁用 `agent.yunmiao.site` vhost
- 执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 数据库回滚

数据库迁移回滚需谨慎执行。登录表相关迁移会影响 `users` / `user_sessions`。

优先策略：

1. 保留数据库结构
2. 回滚应用代码
3. 只在明确需要时执行 Alembic downgrade

## 8. 假设和待实施项

### 假设

- `agent.yunmiao.site` 继续解析到 `81.70.216.46`
- 服务器继续使用现有宝塔 nginx
- 后端数据库继续使用 Neon，不在服务器自建 PostgreSQL
- 对象存储继续使用腾讯云 COS
- Docker 服务仅绑定 `127.0.0.1`，不直接暴露容器端口到公网

### 待实施项

- 提交并推送当前代码
- 服务器创建生产 `.env`
- 服务器拉取代码并构建容器
- 执行 Alembic 迁移
- 初始化登录用户
- 配置宝塔 / nginx vhost 和 HTTPS
- 完成部署后验证清单

# 2026-06 部署过程叙事

> 2026-06-27 把 Miao AI 部署到 `yunmiao@81.70.216.46` 的**完整过程记录**。
> 包含踩过的所有坑和最终方案。**SOP 在 [`docs/operations/deployment.md`](../operations/deployment.md)**；本文是"为什么这么写"的追溯。

## 背景

- 目标：服务器 `yunmiao@81.70.216.46`（Ubuntu 22.04），通过 `https://agent.yunmiao.site` 对外
- 起始状态：本地项目 ready，但完全没有生产配置（无 Dockerfile / compose / 反代）
- 最终状态：3 容器（backend / frontend / agent）跑起来，nginx 反代到位，watchdog + recovery 链路完整

## 时间线

### 阶段 1：写部署文件

新增 / 修改：

- `.dockerignore` —— 排除 `.git` / 缓存 / venv / `node_modules` / `.env`
- `backend/Dockerfile` —— `python:3.12-slim` + 从 `docker:27-cli` 拷 Docker CLI + 阿里云源 + `build-essential`
- `frontend/Dockerfile` —— `node:20-slim` + `pnpm@8.15.9` + 构建时注入 `NEXT_PUBLIC_*`
- `docker-compose.prod.yml` —— backend / frontend 两服务，绑 127.0.0.1，backend 挂 docker socket

**踩坑 1：服务器上 `git push` 失败** —— `Connection closed by 198.18.0.116`。原因：服务器到 GitHub SSH 节点网络瞬断。**解决**：重试 / 换 HTTPS remote + PAT。

### 阶段 2：服务器首次部署

```bash
ssh yunmiao@81.70.216.46
mkdir -p ~/apps && cd ~/apps
git clone git@github.com:moonlight2893267956/miao-ai.git
cd miao-ai
# 写 .env（生产凭证）
docker compose -f docker-compose.prod.yml up -d --build
```

### 阶段 3：Docker build 慢 / 超时

**症状**：后端 image build 卡在 `pip install` 阶段，最终 timeout / 502 Bad Gateway。

**根因排查**：
1. 服务器在中国大陆，pip 默认源（pypi.org）拉不动
2. 服务器本地有 7890 端口代理（clash / v2ray），但 `pip` 不读 `HTTP_PROXY` 环境变量
3. Dockerfile 里要装 `build-essential` / `curl`（apt 装），**apt 也不读 `HTTP_PROXY`**

**解决**（commit `a016bd5`）：
- Dockerfile 用阿里云 PyPI 源：`pip install -i https://mirrors.aliyun.com/pypi/simple/`
- Dockerfile 用阿里云 Debian 源：`sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources`
- 容器内 `apt-proxy.conf` 单独配代理到 `172.17.0.1:7890`（**bridge 网关**，不是 `127.0.0.1`）

**关键点**：
- 容器内 `127.0.0.1` 是容器自己，不是宿主机
- `172.17.0.1` 是 docker bridge 网关，默认情况下**所有容器都能访问**
- daemon 配的 `127.0.0.1:7890` 是在宿主机上，容器内要用网关 IP 才能访问

### 阶段 4：docker mode 下 agent 容器起不来

**症状**：`miao-qwen-chat` 容器在跑，但 backend 调它 `health check timeout (url=None)`。

**根因**：
- backend 容器在 `miao-ai_default` 网络（compose 创建的）
- agent 容器用 `docker run` 启的，默认在 `bridge` 网络
- 两个网络**不互通**（subnet 172.19.0.0/16 vs 172.17.0.0/16）
- 之前设计是用 `-p 9103:8080` 端口映射，host 转发——但 backend 容器在另一个网络里访问不到 host

**解决路径 1（尝试 + 失败）**：让 backend 容器访问 `host.docker.internal:9103`——在 docker compose 里要给 backend 加 `extra_hosts`，且 health check 路径要对——配置复杂。

**解决路径 2（最终方案，commit `b751320`）**：
- `DockerRunner` 加 `shared_network` 参数：把 agent 容器也加进 `miao-ai_default` 网络
- 不再 `-p` 端口映射，改用 **容器名 DNS** 解析
- backend 调 agent：`http://miao-qwen-chat:8080/health`
- 加 `_detect_shared_network()` 工具方法：自动检测 backend 是不是在 docker 里，是的话跟 compose 一起的网络名

效果：health check 200，`miao.recovery.ok` 日志正常出现。

### 阶段 5：agent 容器绑 127.0.0.1 端口不响应

**症状**：`shared_network` 模式下，backend 访问 `http://miao-qwen-chat:8080/health` 超时。

**根因**：`miao_runner.py` 里 `uvicorn.run(host="127.0.0.1")` ——容器内 `127.0.0.1` 是容器自己，**同网络的其他容器访问不到**。

**解决**：`host="0.0.0.0"`。

### 阶段 6：healthcheck 一直 unhealthy

**症状**：`docker ps` 显示 `miao-backend (unhealthy)`，但 `curl http://localhost:18000/api/v1/health` 返回 200。

**根因**：`docker-compose.prod.yml` 的 healthcheck：
```yaml
test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen(http://127.0.0.1:8000/api/v1/health, timeout=5)"]
```
URL 没引号，Python 解释成变量名 → `SyntaxError` 1000+ 次。

**解决**：URL 加单引号：
```yaml
test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5)"]
```

### 阶段 7：前端调后端路径 404

**症状**：浏览器开 `https://agent.yunmiao.site/api/v1/agents` → 404。

**根因**：构建时 `NEXT_PUBLIC_API_BASE=https://agent.yunmiao.site`，浏览器 fetch 同源路径走到 nginx，但 nginx 的 `/` 反代到 frontend (3000)，`/api/` 反代到 backend (18000)——配置正确但 frontend 容器**没**起 `/api` 反代，所以 frontend 自己响应 404。

**最终方案**：宝塔 nginx extension 配置文件 `/www/server/panel/vhost/nginx/extension/agent.yunmiao.site/00-miao-reverse-proxy.conf`，规则：

```nginx
location /api/ { proxy_pass http://127.0.0.1:18000; ... }
location /     { proxy_pass http://127.0.0.1:13000; ... }
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

验证：`https://agent.yunmiao.site/api/v1/health` → 200，`/api/v1/agents` → 401（auth 拦截，说明 backend 真的在响应）。

### 阶段 8：recovery + watchdog 验证

部署后 backend 启动时跑 `_recover_active_agents()`，自动拉起 `is_active=True` 的 agents（`qwen-chat` v4）。日志：

```
miao.recovery.start
miao.recovery.build_skip agent=qwen-chat image_exists=true
miao.recovery.ok agent=qwen-chat port=0
miao.recovery.done recovered=1 total_active=1
```

5 分钟后 watchdog 检测到 idle，调 `miao-qwen-chat` 容器 stop。日志：

```
miao.watchdog.idle_stop agent=qwen-chat idle=309s
docker.container.stopped name=miao-qwen-chat
```

下次 invoke 会自动重新拉起（`_try_auto_activate`），image 复用不重 build。冷启动 ~5s。

---

## 复盘

### 关键技术决策

| 决策 | 原因 |
|---|---|
| backend / frontend 容器只绑 127.0.0.1 | 减少攻击面；公网只暴露 nginx 443 |
| agent 容器不暴露端口 | shared_network + 容器名 DNS，零端口映射 |
| `ENCRYPTION_KEY` 必须在 .env 备份 | 丢了 = 所有 provider 加密数据解不开 |
| `NEXT_PUBLIC_API_BASE=公网域名` 而不是 backend 容器名 | 浏览器 fetch 走同源，nginx 统一反代 |

### 踩过的 5 类坑

1. **网络层**：apt/pip 代理、容器内 host 访问（用 172.17.0.1）、NO_PROXY 误配
2. **Docker 网络**：bridge vs miao-ai_default 隔离、`127.0.0.1` vs `0.0.0.0` 绑定
3. **配置语法**：healthcheck 引号、asyncpg vs psycopg2 URL 协议
4. **架构**：多容器协调（backend + agent）、端口暴露策略
5. **可观测**：healthcheck 不准、unhealthy 不一定真坏

详见 [`docs/operations/deployment.md` §5 已知陷阱](../operations/deployment.md#5-已知陷阱)。

### 给未来的自己 / 接手者

- **不要在生产环境的容器内 `apt install` 后不 commit** —— 改 Dockerfile，rebuild image
- **任何 cross-container 调用都用容器名 DNS**，不要假设端口映射
- **改 healthcheck 时手验一遍 `docker exec`**，别等部署后才发现 SyntaxError
- **凭证只在 `.env` 出现**，从不在聊天记录 / commit message / 日志里
- **遇到 docker build 慢先想源、再想代理**，90% 是源的问题

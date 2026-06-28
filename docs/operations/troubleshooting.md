# 排错手册

> 6 类常见问题的症状、根因、修法。
> **不覆盖**：日常使用问题（看 [`local-dev.md`](local-dev.md)）和部署问题（看 [`deployment.md`](deployment.md)）。

---

## 1. 后端起不来

### 1.1 `ImportError: No module named psycopg2`

```
ImportError: No module named 'psycopg2'
  File ".../sqlalchemy/dialects/postgresql/asyncpg.py", line ...
```

**根因**：`DATABASE_URL` 用了 `postgresql://` 前缀，SQLAlchemy 默认找 psycopg2（项目用 asyncpg）。

**修法**：把 URL 前缀改成 `postgresql+asyncpg://`，query string 用 `?ssl=require`（不是 `sslmode=require`）。

```diff
- DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
+ DATABASE_URL=postgresql+asyncpg://user:pass@host/db?ssl=require
```

### 1.2 `/api/v1/health/ready` 报 `psycopg2.OperationalError` 或 500

**先看 `/tmp/miao-backend.log` 找具体异常**。

#### 1.2.1 `relation "users" does not exist`

**根因**：Neon dev branch 的 `search_path` 默认是 `""`（prod 是 `"$user", public`），SQLAlchemy `SELECT FROM users` 找不到 `public.users`。

**修法**：确认 `backend/app/db.py` 包含 connect event listener：

```python
@event.listens_for(engine.sync_engine, "connect")
def _set_search_path(dbapi_connection, connection_record):
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SET search_path TO public")
```

如果是 git pull 后新装的，rebuild 后端镜像或 `pip install -e backend/` 重装。

#### 1.2.2 `connection refused` / `could not connect to server`

- 检查 `.env` / `.env.local` 的 `DATABASE_URL` host / port
- Neon free tier 会 sleep，第一次连会等 5-10s
- 公司网络：是否需要代理？`HTTPS_PROXY` 是否设置

#### 1.2.3 `password authentication failed`

- `DATABASE_URL` 的 user / pass 错了
- Neon 控制台重置密码后忘了同步到 `.env` / `.env.local`

### 1.3 `Address already in use` on 8000

**根因**：8000 端口被占（之前 start 的没停干净，或别的服务）。

**修法**：
```bash
# 看谁占了
lsof -nP -iTCP:8000 -sTCP:LISTEN
# 杀掉
lsof -ti:8000 | xargs kill -9
# 重启
bash scripts/start-all.sh
```

---

## 2. Agent 启动失败

### 2.1 `miao.recovery.failed: docker not available`

```
miao.recovery.failed agent=qwen-chat error=docker not available
```

**根因**：backend 容器内没装 Docker CLI / socket 没挂。

**修法**（生产）：
- `backend/Dockerfile` 包含 `docker-ce-cli` 安装
- `docker-compose.prod.yml` backend 服务挂载 `/var/run/docker.sock:/var/run/docker.sock`
- rebuild 镜像

**修法**（本地 dev）：换 venv 模式（见 2.3）

### 2.2 `docker container health check timeout (url=None)`

```
miao.recovery.failed agent=qwen-chat error=docker container health check timeout (url=None)
```

**根因 A**：`AGENT_RUNTIME_MODE=docker` 但 backend 在 host（不在 docker 里），`_detect_shared_network()` 返回 None，agent 容器用 bridge 网络——backend 在 host 访问不到。

**修法 A**（生产 server）：保持 docker 模式，agent 容器必须跟 backend 容器在同一 docker network。

**修法 B**（本地 Mac dev）：`.env.local` 加 `AGENT_RUNTIME_MODE=venv`：

```bash
echo "AGENT_RUNTIME_MODE=venv" >> .env.local
bash scripts/stop-all.sh && bash scripts/start-all.sh
```

**根因 B**：`miao_runner.py` 绑了 `127.0.0.1` 而非 `0.0.0.0`——容器内 `127.0.0.1` 是容器自己。

**修法**：看 `backend/agent_templates/miao_runner.py` 应该是 `host="0.0.0.0"`，如果是 `127.0.0.1` 改回来。

### 2.3 invoke 卡 30s+ 后 504

**根因**：agent 第一次启动要 build（pip install + agent 自己的依赖），1-2 分钟。`invoke` 端点 timeout 30s 是预期——让前端 loading 转圈直到返回。

**加速**：保持 `image_exists` 缓存（commit `b751320`），第二次 invoke ~5s。

### 2.4 agent 启动后立刻 crash

**根因**：agent.py 语法错 / 缺依赖 / 入口函数名不对。

**查日志**：
```bash
# venv 模式
cat /tmp/miao/agents/<name>/agent.log 2>/dev/null
tail -f /tmp/miao-backend.log | grep -i "agent\|<name>"

# docker 模式
docker logs miao-<name> --tail 50
```

**修法**：
- 自己用 `cd demos/sample-agent && uv run python agent.py` 验证本地能跑
- 上传 zip 必须含 `agent.py`（默认入口 `agent:invoke`）
- `requirements.txt` 列所有依赖

---

## 3. 登录 / Auth

### 3.1 401 但凭证对

**根因 A**：cookie 没带上
- 浏览器：跨域 fetch 默认不发 cookie，要 `credentials: include`
- curl：加 `-b /tmp/cookie.txt`（用 `-c` 写）

**根因 B**：CORS
- `backend/app/main.py` `CORSMiddleware allow_origins` 没加你访问的源
- 加完重启 backend

### 3.2 `invalid credentials`

**根因**：密码错了。数据库 `users.password` 字段是明文存的（项目早期决策），不是前端加密后传——是后端拿明文直接对比。

**查真实密码**：
```bash
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"
psql "$DATABASE_URL" -c "SELECT username, password FROM users;"
```

### 3.3 Session 一直过期

**根因 A**：`user_sessions.expires_at` 过期（默认 7 天）。
**根因 B**：服务重启把内存里的 session 清了？看 backend 的 session 是不是 DB 存。

**修法**：DB 存 session（项目已用 `user_sessions` 表）。如果清空，**重新登录**即可。

---

## 4. API Key 错误

### 4.1 `invalid or revoked` 但 key 看起来对

**根因**：你用错了字段。`GET /api/v1/agents/{name}/keys` 列表返回 `id`（DB 主键 UUID），**不是 API token**。要拿 `key` 字段（只在 `POST .../keys` 创建时返回明文一次）。

**正确用法**：
```bash
# 创建 key（只这一次返回明文）
KEY=$(curl -X POST $BASE/api/v1/agents/$NAME/keys -H "Content-Type: application/json" -d '{"label":"my-key"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")
echo "$KEY"  # miao_xxxxxxxx...

# 用 key
curl -X POST $BASE/api/v1/agents/$NAME/invoke \
  -H "Authorization: Bearer $KEY" \
  -d '...'
```

**列表拿 key 不可能**：明文不存 DB，只存 hash。忘了就 revoke 旧的、建新的。

### 4.2 401 但 key 在用

**根因**：key 被 `revoked_at` 了。`GET .../keys` 列表看 `revoked_at` 字段。

---

## 5. Langfuse 看不到 trace

### 5.1 完全看不到

**查顺序**：
1. 后端日志里有没有 `miao.trace.sent` / `miao.trace.failed`
2. `.env.local` / `.env` 里 `LANGFUSE_PUBLIC_KEY` 前 8 字符是不是 dev / prod project 对应的那一对
3. invoke 响应里有 `trace_id` 字段
4. Langfuse Cloud UI → 切到对应 project

**最常见错**：本地 dev 用了 prod 的 `LANGFUSE_PUBLIC_KEY`，trace 全跑到 prod project 了。立刻在 Langfuse 控制台**重置 dev project key**。

### 5.2 偶发断流

**根因**：trace 上报是异步的（背景线程），单次失败不影响 invoke 返回。生产环境 Langfuse 偶发 5xx 正常。

**如果持续断流**：
- 检查 `LANGFUSE_BASE_URL`（默认 `https://cloud.langfuse.com`）
- 公司网络：是否要加 HTTPS_PROXY

### 5.3 trace 跟 agent 不对应

**根因**：用了错误的 `session_id` / `tags`。可以在 invoke body 显式传：

```json
{
  "input": {...},
  "metadata": {
    "user_id": "alice",
    "session_id": "session-2026-06-28-001",
    "tags": ["dev", "isolation-test"]
  }
}
```

---

## 6. CORS 错误

```
Access to fetch at 'https://agent.yunmiao.site/api/...' from origin 'http://localhost:3000' has been blocked by CORS policy
```

**根因**：浏览器发起跨域请求，后端 `CORSMiddleware` 没允许该 origin。

**修法**：在 `backend/app/main.py` 找到 `CORSMiddleware` 配置，加 origin：

```python
allow_origins=[
    "http://localhost:3000",
    "https://agent.yunmiao.site",
    "https://新域名.com",  # ← 加这里
]
```

重启 backend。

---

## 7. 数据库迁移问题

### 7.1 `alembic upgrade head` 报 "relation already exists"

**根因**：表已经存在（手动建的 / 之前 migrate 跑了一半）。

**修法**：
- 如果是空表：`alembic stamp head`（标记当前状态为最新，不动数据）
- 如果有数据：别乱搞，联系维护者

### 7.2 `Multiple head revisions`

**根因**：本地分支 alembic revision 没 merge。

**修法**：
```bash
cd backend
uv run alembic merge -m "merge heads" <rev1> <rev2>
uv run alembic upgrade head
```

---

## 8. 容器相关

### 8.1 容器起不来：`port is already allocated`

**根因**：宿主机端口被占。容器绑 `127.0.0.1:18000` 但 host 上有别的服务占了。

**修法**：
```bash
lsof -nP -iTCP:18000 -sTCP:LISTEN
# 改 docker-compose.prod.yml 的 ports 映射到别的端口（同时改 nginx 反代）
```

### 8.2 容器一直 restarting

**查日志**：
```bash
docker logs miao-backend --tail 100
docker inspect miao-backend --format '{{.State.Error}}'
```

**常见根因**：
- alembic 迁移失败 → 手动进容器跑
- `.env` 缺关键变量 → 补
- ENCRYPTION_KEY 不匹配 → 还原旧 key

### 8.3 `docker.sock: permission denied`

**根因**：当前用户没在 `docker` group。

**修法**：
```bash
sudo usermod -aG docker $USER
# 重新登录 shell
```

---

## 9. 快速查问题

| 症状 | 先看 |
|---|---|
| 后端报错 | `/tmp/miao-backend.log` |
| 前端报错 | `/tmp/miao-frontend.log` |
| agent 报错 | `/tmp/miao/agents/<name>/agent.log`（venv） / `docker logs miao-<name>`（docker） |
| 容器状态 | `docker compose -f docker-compose.prod.yml ps` |
| 网络通不通 | `docker network inspect miao-ai_default` |
| 容器内进程 | `docker exec miao-backend ps aux` |
| DB 状态 | `psql $DATABASE_URL -c "\dt"` |

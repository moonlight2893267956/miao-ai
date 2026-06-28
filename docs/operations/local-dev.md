# 本地开发指南

> 自托管 AI Agent 平台。第一次跑通 5~10 分钟。

## 1. 装工具（一次性）

| 工具 | 用途 | 安装 |
|---|---|---|
| **Python 3.11+** | 后端 | macOS 自带或 `brew install python@3.11` |
| **Node 20+** | 前端（Next.js 14 要 ≥ 18.17） | 推荐 [nvm](https://github.com/nvm-sh/nvm) 装：`nvm install 22` |
| **uv** | Python 包管理（替代 pip + venv） | `brew install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **pnpm** | Node 包管理 | `brew install pnpm` 或 `npm i -g pnpm` |
| **mysql client** | 本地跑 `mysql` 命令（验证 DB 时用） | `brew install mysql-client` |

> 后端硬性要 uv；前端硬性要 pnpm + Node 20+。

## 2. 准备凭证（一次性，**双层 .env**）

本项目用 **两层 .env 文件** 隔离本地和生产（commit `336454f`）：

| 文件 | 作用 | 是否 git ignore | 用于 |
|---|---|---|---|
| `.env` | 生产凭证 | 是 | 服务器 `docker-compose.prod.yml` |
| `.env.local` | 本地凭证 | 是 | 本地 `scripts/start-all.sh` |

**强制流程**：本地 dev **必须**用 `.env.local`，不能直接 `cp .env.example .env` 然后填生产凭证跑本地 —— 会污染生产 Langfuse trace 和 MySQL DB。

### 2.1 在两个云服务建 dev 资源

一次性操作，不重复：

1. **MySQL**：确保本地 MySQL 实例运行，创建 `miao_ai` 库并授权：`CREATE DATABASE miao_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL ON miao_ai.* TO 'miao'@'%';`
2. **Langfuse**：登录 [cloud.langfuse.com](https://cloud.langfuse.com) → **New Project** 命名 `miao-ai-dev` → 进项目 → **Settings → API Keys** → **Create new API key** → 复制 `Public Key` 和 `Secret Key`

> 详细步骤：供应商侧看 [`infra/CLOUD_SETUP.md`](../../infra/CLOUD_SETUP.md)。MySQL/Langfuse/腾讯云 COS/阿里云 DashScope 都要注册，但本地 dev **只需要** MySQL + Langfuse 两个。

### 2.2 写 `.env.local`

```bash
cd miao-ai
cp .env.local.example .env.local
# 用编辑器打开，把以下 4 行替换成 dev 凭证：
#   DATABASE_URL=mysql+aiomysql://miao:miao_dev@localhost:3306/miao_ai?charset=utf8mb4
#   LANGFUSE_PUBLIC_KEY=pk-lf-xxx
#   LANGFUSE_SECRET_KEY=sk-lf-xxx
#   LANGFUSE_BASE_URL=https://cloud.langfuse.com
#
# 其它变量（ENCRYPTION_KEY / COS_* / DASHSCOPE_*）保持 .env.local.example 里的值，
# 这些是生产值，本地不存敏感数据所以复用 OK；想完全隔离的话改成自己的。
```

> **DATABASE_URL 协议前缀必须是 `mysql+aiomysql://`，query 必须包含 `?charset=utf8mb4`** —— aiomysql 需要 charset 参数来正确处理中文和 emoji

## 3. 启动服务

```bash
bash scripts/start-all.sh
```

脚本会：

- 检查工具
- 创建/复用 venv（后端 `backend/.venv`、前端 `frontend/node_modules`）
- **优先加载 `.env.local`**（找不到再 fallback 到 `.env` 并打 warning）
- 后台启动后端（8000）+ 前端（3000）
- 写 PID 到 `/tmp/miao-{backend,frontend}.pid`
- 等服务就绪后打印 URL

预期输出：

```
▶ 加载本地凭证 .env.local（隔离生产 Langfuse/MySQL）
✅ 启动完成
   后端  http://localhost:8000    (PID 12345,  日志 /tmp/miao-backend.log)
   前端  http://localhost:3000    (PID 12346,  日志 /tmp/miao-frontend.log)
   OpenAPI  http://localhost:8000/docs
```

如果看到 `⚠️  未找到 .env.local，回退到根 .env`：说明你跳过了 §2.2，去建 `.env.local`。**不要继续往下走**，否则会污染生产。

## 4. 验证

```bash
bash scripts/status.sh
# 后端 /api/v1/health/ready → {"status":"ready","db":"ok"}
# 前端 / → 200
```

或直接打开：

- 前端：<http://localhost:3000/agents>
- 后端 OpenAPI：<http://localhost:8000/docs>

## 5. 第一次使用

### 5.1 打包一个 sample agent

```bash
cd demos/sample-agent
zip -j /tmp/my-agent.zip agent.py requirements.txt
```

这会把 `agent.py`（简单 LangChain + DashScope）+ `requirements.txt` 打成一个 zip。

### 5.2 UI 里跑通

1. 打开 <http://localhost:3000/agents>
2. 点 **New Agent** → 填名字（如 `my-first-agent`）→ Create
3. 进 agent 详情 → **Upload** → 选 `/tmp/my-agent.zip` + 填 version（如 `v1`）
4. 点 **Activate**（**首次会构建 venv，1~2 分钟**，后续会跳过）
5. 创建 API Key（Create 按钮）→ 复制明文 key（只显示一次）
6. 在试运行区粘贴 key → **Run** → 看到 qwen-plus 回答
7. 点 trace_id 链接 → 去 Langfuse Cloud → 切到 `miao-ai-dev` project → 看完整 trace

### 5.3 同样流程的 curl 版

```bash
BASE=http://localhost:8000
NAME=my-first-agent

# 1) 创建
curl -X POST $BASE/api/v1/agents -H "Content-Type: application/json" \
  -d "{\"name\":\"$NAME\"}"

# 2) 上传 zip
curl -X POST $BASE/api/v1/agents/$NAME/versions \
  -F "version=v1" -F "file=@/tmp/my-agent.zip"

# 3) 激活（首次要 1-2 分钟）
curl -X POST "$BASE/api/v1/agents/$NAME/versions/activate?version=v1"

# 4) 创建 API key
KEY=$(curl -X POST $BASE/api/v1/agents/$NAME/keys | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")

# 5) 调用
curl -X POST $BASE/api/v1/agents/$NAME/invoke \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"input":{"question":"一句话介绍你自己"}}'
```

## 6. 常用命令

```bash
bash scripts/start-all.sh    # 启动（自动检查 + 装依赖 + 起服务）
bash scripts/stop-all.sh     # 停止（按 PID + 端口兜底）
bash scripts/status.sh       # 看运行状态

# 看日志
tail -f /tmp/miao-backend.log
tail -f /tmp/miao-frontend.log
```

**手动重启单个服务**：

```bash
# 停后端
kill $(cat /tmp/miao-backend.pid)

# 启动后端（前台，能看日志）
cd backend && uv run uvicorn app.main:app --reload --port 8000
```

## 7. 自己写 agent 代码

上传的 zip 必须含 `agent.py`，里面定义入口函数（默认 `agent:invoke`）：

```python
# agent.py
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
import os

llm = ChatOpenAI(
    model=os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("DASHSCOPE_BASE_URL"),
)
chain = ChatPromptTemplate.from_messages([
    ("system", "你是一个简洁的中文助手。"),
    ("human", "{input}"),
]) | llm

def invoke(input: dict, config: dict) -> dict:
    """Miao agent entry point. Returns dict with 'answer' key."""
    return {"answer": chain.invoke(input).content}
```

子进程从父进程继承环境变量（`.env.local` 已加载），所以可以直接 `os.getenv("DASHSCOPE_API_KEY")`。

更详细：[`backend/agent_templates/miao_runner.py`](../../backend/agent_templates/miao_runner.py)

## 8. 排错

详细排错手册：[`docs/operations/troubleshooting.md`](troubleshooting.md)

下面是几个本地 dev 最高频的问题：

| 症状 | 解决 |
|---|---|
| `uv: command not found` | 装 uv（见 §1） |
| `pnpm: command not found` | 装 pnpm（见 §1） |
| `Node 18.x is too old` | `nvm install 22 && nvm use 22` |
| 端口 8000/3000 已被占用 | `bash scripts/stop-all.sh` 或 `lsof -ti:8000 \| xargs kill` |
| 启动后 `/api/v1/health/ready` 报 `ImportError` | `.env.local` 的 `DATABASE_URL` 用了 `postgresql://` 而非 `mysql+aiomysql://` |
| 启动后 `/api/v1/health/ready` 500 报 `Table 'miao_ai.agents' doesn't exist` | 未跑 alembic 迁移：`cd backend && alembic upgrade head` |
| 启动后 invoke 报 `docker health check timeout` | Mac 本地 Docker Desktop 桥接问题：临时在 `.env.local` 加 `AGENT_RUNTIME_MODE=venv`（生产服务器用 docker 不受影响） |
| trace 在 Langfuse dev project 看不到 | 检查 `.env.local` 的 `LANGFUSE_PUBLIC_KEY` 前 8 字符是不是 dev 的（不是 prod 的）。dev key 跟 prod key 长得像，极易混 |
| 登录 401 / invalid credentials | 密码是数据库 `users.password` 字段的明文。看下 MySQL 实际值 |

# Miao AI 快速开始

> 自托管 AI Agent 平台。Trace + Agent 上传/部署 + API 调用。
> 第一次跑通，5~10 分钟。

## 1. 装工具（一次性）

| 工具 | 用途 | 安装 |
|---|---|---|
| **Python 3.11+** | 后端 | macOS 自带或 `brew install python@3.11` |
| **Node 20+** | 前端（Next.js 14 要 ≥ 18.17） | 推荐 [nvm](https://github.com/nvm-sh/nvm) 装：`nvm install 22` |
| **uv** | Python 包管理（替代 pip + venv） | `brew install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **pnpm** | Node 包管理 | `brew install pnpm` 或 `npm i -g pnpm` |

> 后端硬性要 uv；前端硬性要 pnpm + Node 20+。

## 2. 准备凭证（一次性）

参考 [`infra/CLOUD_SETUP.md`](../infra/CLOUD_SETUP.md)，注册 4 个云服务并拿到凭证：

1. **Langfuse Cloud** — Public Key / Secret Key
2. **Neon** — Connection string
3. **腾讯云 COS** — SecretId / SecretKey / Region / Bucket
4. **DashScope（通义千问）** — API Key

然后：

```bash
cd miao-ai
cp .env.example .env
# 用你喜欢的编辑器填入凭证（每个变量 .env.example 都有注释）
```

`.env` 已经在 `.gitignore` 里，不会被提交。

## 3. 启动服务

```bash
bash scripts/start-all.sh
```

脚本会：
- 检查工具
- 创建/复用 venv（后端 `backend/.venv`、前端 `frontend/node_modules`）
- 后台启动后端（8000）+ 前端（3000）
- 写 PID 到 `/tmp/miao-{backend,frontend}.pid`
- 等服务就绪后打印 URL

预期输出：

```
✅ 启动完成
   后端  http://localhost:8000    (PID 12345,  日志 /tmp/miao-backend.log)
   前端  http://localhost:3000    (PID 12346,  日志 /tmp/miao-frontend.log)
   OpenAPI  http://localhost:8000/docs
```

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
7. 点 trace_id 链接 → 去 Langfuse Cloud 看完整 trace

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
    """Miao agent 入口。"""
    return {"answer": chain.invoke(input).content}
```

子进程从父进程继承环境变量（`.env` 已加载），所以可以直接 `os.getenv("DASHSCOPE_API_KEY")`。

更详细：[`backend/agent_templates/miao_runner.py`](../backend/agent_templates/miao_runner.py)

## 8. 排错

### 服务起不来

| 症状 | 解决 |
|---|---|
| `uv: command not found` | 装 uv（见 §1） |
| `pnpm: command not found` | 装 pnpm（见 §1） |
| `Node 18.x is too old` | `nvm install 22 && nvm use 22` |
| `端口 8000/3000 已被占用` | 跑 `bash scripts/stop-all.sh`，或手动 `lsof -ti:8000 \| xargs kill` |
| `找不到 .env` | `cp .env.example .env` 并填入凭证 |
| `permission denied: scripts/*.sh` | `chmod +x scripts/*.sh` |

### 后端 / DB 连不上

- 检查 `.env` 里的 `DATABASE_URL`：`postgresql+asyncpg://...?ssl=require`（不是 `?sslmode=require`，asyncpg 不认）
- 检查 Neon 项目是否在运行（free tier 会被自动 sleep，第一次连会等几秒）

### 激活卡住 / 子进程起不来

- 看 `cat /tmp/miao/agents/<name>/agent.log`（agent 子进程自己的日志）
- 最常见原因：agent.py 语法错 / requirements 装失败
- 解决：在 `demos/sample-agent/` 跑 `uv run python agent.py` 验证本地能否跑

### trace 没上报到 Langfuse

- 检查 `LANGFUSE_*` 三个变量对不对
- Langfuse UI → Settings → API Keys 重新复制一次
- trace 可能要 5~10 秒才在 UI 出现（异步上报）

## 9. 项目结构

```
miao-ai/
├── scripts/              # ⭐ start-all.sh / stop-all.sh / status.sh
├── infra/CLOUD_SETUP.md  # 云服务注册步骤
├── backend/              # FastAPI 后端（含 README）
├── frontend/             # Next.js 前端（含 README）
├── demos/                # 独立 demo（hello-trace、sample-agent）
├── docs/QUICKSTART.md    # ⭐ 本文档
├── .env / .env.example   # 凭证（git ignore）
└── README.md
```

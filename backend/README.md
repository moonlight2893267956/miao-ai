# Miao AI Backend

自托管 AI Agent 平台后端。FastAPI + SQLAlchemy 2.0 async + Neon + 腾讯云 COS + Langfuse。

## 当前进度

- ✅ **Phase 1a** — 后端骨架（FastAPI + SQLAlchemy async + 配置 + 日志 + 健康检查）
- ✅ **Phase 1b** — 实体/CRUD（Agent / AgentVersion / ApiKey + Alembic + REST API）
- ✅ **Phase 1c** — Agent Runtime（子进程 + uv venv + COS + AgentRegistry）
- ✅ **Phase 1d** — invoke API（API Key 鉴权 + Langfuse trace 注入）
- ✅ **Phase 1e** — 测试 + 文档（12/12 集成测试通过）

## 架构

```
┌─────────────────┐    ┌──────────────────┐    ┌────────────────┐
│  用户上传 zip   │───▶│  Miao AI 后端    │───▶│  COS 存 zip    │
└─────────────────┘    │  (FastAPI)       │    └────────────────┘
                       │                  │
┌─────────────────┐    │  ┌────────────┐  │    ┌────────────────┐
│  客户端 invoke  │───▶│  │  Runtime   │──┼───▶│  Agent 子进程  │
│  (API Key)      │    │  │  Registry  │  │    │  (uv venv)     │
└─────────────────┘    │  └────────────┘  │    └───────┬────────┘
                       └────────┬─────────┘            │
                                │ trace                │
                                ▼                      ▼
                       ┌──────────────────┐    ┌────────────────┐
                       │  Langfuse Cloud  │    │  DashScope     │
                       └──────────────────┘    │  (qwen-plus)   │
                                              └────────────────┘
```

## 完整使用流程

### 1. 启动 backend

```bash
cd backend
uv venv  # 第一次需要
uv pip install -e ".[dev]"

# 启动（确保根 .env 已就位）
uv run uvicorn app.main:app --reload --port 8000
```

访问 <http://localhost:8000/docs> 看 OpenAPI。

### 2. 创建 agent

```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -d '{"name":"hello-agent","description":"first agent"}'
```

### 3. 上传 agent 代码包

```bash
# 用户代码：agent.py（入口函数）+ requirements.txt
zip my-agent.zip agent.py requirements.txt

curl -X POST http://localhost:8000/api/v1/agents/hello-agent/versions \
  -F "version=v1" \
  -F "file=@my-agent.zip"
```

### 4. 激活版本

```bash
curl -X POST "http://localhost:8000/api/v1/agents/hello-agent/versions/activate?version=v1"
# 首次激活会构建 venv（uv venv + pip install）耗时 1~2 分钟
# 返回 {"status": "running", "is_active": true, ...}
```

### 5. 颁发 API Key

```bash
curl -X POST http://localhost:8000/api/v1/agents/hello-agent/keys \
  -H "Content-Type: application/json" \
  -d '{"label":"prod"}'
# 返回 {"key": "miao_xxxxxx", ...}  ← 明文只显示一次
```

### 6. 调用 agent

```bash
curl -X POST http://localhost:8000/api/v1/agents/hello-agent/invoke \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer miao_xxxxxx" \
  -d '{
    "input": {"question": "什么是 Langfuse？"},
    "metadata": {"user_id": "u-1", "session_id": "s-1", "tags": ["prod"]}
  }'
# 返回 {"output": {...}, "trace_id": "abc123..."}
```

### 7. 去 Langfuse Cloud 看 trace

<https://cloud.langfuse.com> → 你的 project → Traces。会看到：
- Tag `miao-agent` + `agent:hello-agent`
- metadata.latency_ms
- user_id / session_id

## 用户代码约定

上传的 zip 必须包含 `agent.py`，里面定义入口函数（默认 `invoke`）：

```python
# agent.py
def invoke(input: dict, config: dict) -> dict:
    """Miao agent 入口。
    
    Args:
        input: 调用方传入的 dict
        config: 调用方传入的 config（含 langfuse_user_id / session_id / tags）
    Returns:
        dict: 返回给调用方的结果
    """
    question = input.get("question", "你好")
    # ... 你的 LangChain / LangGraph / 任何逻辑 ...
    return {"answer": "..."}
```

可选 `requirements.txt` 列依赖，平台会自动装到独立 venv。

## 端到端冒烟测试

```bash
# 启动 server 后
bash scripts/e2e_smoke.sh
```

## API 速查

| 路径 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/api/v1/health` | GET | - | 健康检查 |
| `/api/v1/health/ready` | GET | - | 含 DB 探活 |
| `/api/v1/agents` | POST | - | 创建 agent |
| `/api/v1/agents` | GET | - | 列出 agent |
| `/api/v1/agents/{name}` | GET | - | 详情（含实时 status） |
| `/api/v1/agents/{name}` | DELETE | - | 删除（先 stop 进程） |
| `/api/v1/agents/{name}/versions` | POST | - | 上传 zip（multipart） |
| `/api/v1/agents/{name}/versions` | GET | - | 列出 versions |
| `/api/v1/agents/{name}/versions/activate` | POST | - | 激活 version |
| `/api/v1/agents/{name}/keys` | POST | - | 颁发 API key |
| `/api/v1/agents/{name}/keys` | GET | - | 列出未撤销 keys |
| `/api/v1/agents/{name}/keys/{id}` | DELETE | - | 撤销 key |
| `/api/v1/agents/{name}/invoke` | POST | Bearer | 调用 agent |

## 配置

Backend 启动时自动加载根 `miao-ai/.env`（与 demos 共享）。需要的环境变量：

| 变量 | 来源 |
|---|---|
| `DATABASE_URL` | Neon connection string（`postgresql+asyncpg://...?ssl=require`） |
| `LANGFUSE_*` | Langfuse Cloud project keys |
| `TENCENT_*` / `COS_*` | 腾讯云 COS secret + bucket |
| `DASHSCOPE_*` | 通义千问 API key |

完整模板见 `miao-ai/.env.example`。

## 测试

```bash
cd backend
uv run pytest -v
```

12 个测试：
- `test_health.py` × 2：基础健康检查
- `test_agents.py` × 5：Agent/Key CRUD
- `test_invoke.py` × 5：Invoke 鉴权 + 透传（mock Runtime，不真启动子进程）

## 目录

```
backend/
├── app/
│   ├── main.py           # FastAPI 入口
│   ├── config.py         # Pydantic Settings
│   ├── db.py             # SQLAlchemy async 引擎
│   ├── logging.py        # structlog 配置
│   ├── api/              # 路由（agents/versions/keys/invoke/health）
│   ├── models/           # SQLAlchemy 模型
│   ├── runtime/          # Agent Runtime（storage/venv/process/manager/registry）
│   └── schemas/          # Pydantic schemas
├── agent_templates/      # miao_runner.py（每个 agent 子进程跑的 FastAPI app）
├── alembic/              # 迁移
├── tests/                # pytest 测试
└── scripts/              # 冒烟脚本
```

## 已知限制 / 后续工作

- **Phase 2** — 多 agent 并存 + 进程崩溃自动重启 + venv 哈希复用
- **Phase 3** — SSE 流式输出 + Docker 沙箱 + Langfuse Datasets 评估
- 当前没有流式输出（invoke 同步返回）
- 进程崩溃不会自动重启（需要手动重新 activate）
- 重启 server 后 is_active 的 agent 不会自动起来（Phase 2 修）

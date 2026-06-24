# Miao AI — 系统架构文档

> **版本**: v0.1.0 | **最后更新**: 2026-06-23

---

## 1. 概述

Miao AI 是一个**自托管的 AI Agent 运行平台**。用户上传自己的 Python agent 代码（zip 包），平台自动构建独立运行环境（隔离 venv 或 Docker 容器），然后通过 REST API 调用 agent。全程由 Langfuse 记录追踪（trace）。

**核心特性**:
- 每个 agent 运行在独立子进程（venv 模式）或 Docker 容器中
- 支持普通调用和 SSE 流式输出
- 异步任务提交 + Webhook 回调
- 进程崩溃自动重启、空闲超时回收（invoke 时自动唤醒）、per-agent 令牌桶限流
- 启动时自动恢复已激活的 agent
- 代码包存储于腾讯云 COS，业务数据存 Neon PostgreSQL

---

## 2. 系统架构全景

```
┌──────────────────────────────────────────────────────────────┐
│                      前端 (Next.js 14)                       │
│                  localhost:3000                               │
│  Agents 列表 / Agent 详情 / 版本管理 / API Key 管理 / 试运行  │
└────────────────────────┬─────────────────────────────────────┘
                         │ REST (JSON) / SSE
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                   后端 (FastAPI + Uvicorn)                    │
│                  localhost:8000                               │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ agents   │  │ versions │  │  keys    │  │   invoke    │ │
│  │  CRUD    │  │ 上传/激活│  │颁发/吊销 │  │调用/流式/异步│ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────────┘ │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │               Agent Runtime Layer                     │   │
│  │  ┌───────────┐  ┌────────────┐  ┌────────────────┐   │   │
│  │  │  Registry │  │  Manager   │  │ TaskWorker     │   │   │
│  │  │ (注册中心) │  │(单agent管理)│  │(异步调用执行器) │   │   │
│  │  └───────────┘  └────────────┘  └────────────────┘   │   │
│  │  ┌───────────┐  ┌────────────┐  ┌────────────────┐   │   │
│  │  │  Venv     │  │  Docker    │  │   Watchdog     │   │   │
│  │  │ Builder   │  │  Builder   │  │ (崩溃重启/回收) │   │   │
│  │  └───────────┘  └────────────┘  └────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────┬──────────┬─────────────┬─────────────┬───────────┘
           │          │             │             │
           ▼          ▼             ▼             ▼
    ┌──────────┐ ┌───────┐  ┌───────────┐  ┌──────────┐
    │  Neon PG │ │  COS  │  │ Langfuse  │  │ DashScope│
    │ (业务DB) │ │(zip包)│  │  (trace)  │  │  (LLM)   │
    └──────────┘ └───────┘  └───────────┘  └──────────┘
```

---

## 3. 技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| **后端框架** | FastAPI + Uvicorn | 异步 Python Web 框架 |
| **ORM** | SQLAlchemy 2.0 (async) | 异步数据库操作 |
| **数据库** | Neon PostgreSQL (Serverless) | 业务数据存储 |
| **数据迁移** | Alembic | 版本化管理 DB schema |
| **对象存储** | 腾讯云 COS (S3 兼容) | agent zip 包存储 |
| **可观测性** | Langfuse + structlog | 完整调用链路追踪 |
| **Agent 隔离** | subprocess (venv) / Docker CLI | 子进程或容器隔离 |
| **依赖管理** | uv (Pipenv 替代) | venv 构建 + pip install |
| **限流** | 令牌桶 (token bucket) | per-agent QPS 控制 |
| **前端框架** | Next.js 14 (App Router) | React + TypeScript |
| **UI 组件** | Radix UI + Tailwind CSS | 无障碍 UI 组件库 |
| **LLM Provider** | DashScope (通义千问) | qwen-plus 等模型 |

---

## 4. 数据模型

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────┐
│    Agent     │ 1───N │  AgentVersion    │ 1───1 │  ManagedAgent │
│              │       │                  │       │  (内存对象)   │
│ id (UUID)    │       │ id (UUID)        │       │               │
│ name (str)   │       │ agent_id (FK)    │       │ name          │
│ description  │       │ version (str)    │       │ port          │
│ created_at   │       │ artifact_url     │       │ status        │
│              │       │ entrypoint       │       │ process       │
└──────────────┘       │ is_active (bool) │       │ _docker       │
       │               │ status           │       └──────────────┘
       │               │ created_at       │
       │ 1───N         └──────────────────┘
       │
       ▼
┌──────────────┐       ┌──────────────────┐
│   ApiKey     │       │   InvokeTask     │
│              │       │                  │
│ id (UUID)    │       │ id (UUID)        │
│ agent_id(FK) │       │ agent_id (FK)    │
│ key_hash     │       │ agent_name       │
│ label        │       │ request_id       │
│ revoked_at   │       │ webhook_url      │
│ created_at   │       │ status           │
└──────────────┘       │ input_payload    │
                       │ output_payload   │
                       │ error_message    │
                       │ trace_id         │
                       │ webhook_delivered│
                       │ created_at       │
                       │ completed_at     │
                       └──────────────────┘
```

### 4.1 Agent（机器人）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | String(64) | 唯一名称（用作 URL path） |
| description | Text | 可选描述 |
| created_at | DateTime | 创建时间 |

### 4.2 AgentVersion（版本）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| agent_id | UUID (FK) | 所属 agent |
| version | String(64) | 版本号（如 "v1", "v2"） |
| artifact_url | String | COS 上的 zip 包 key |
| entrypoint | String | 入口函数（如 "agent:invoke"） |
| is_active | Boolean | 是否当前激活版本 |
| status | String | building / running / crashed |
| created_at | DateTime | 创建时间 |

### 4.3 ApiKey（API 密钥）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| agent_id | UUID (FK) | 所属 agent |
| key_hash | String(64) | SHA-256 哈希（原文不存） |
| label | String | 备注标签 |
| revoked_at | DateTime | 吊销时间（NULL = 有效） |

### 4.4 InvokeTask（异步调用任务）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| agent_id | UUID (FK) | 所属 agent |
| agent_name | String(64) | agent 名称（冗余） |
| request_id | String(64) | 唯一请求 ID（用于轮询） |
| webhook_url | Text | 回调 URL |
| status | String | pending / running / success / failed |
| input_payload | JSON | 输入参数 |
| output_payload | JSON | 返回结果 |
| error_message | Text | 错误信息 |
| trace_id | String | Langfuse trace ID |
| webhook_delivered | Boolean | webhook 是否成功投递 |
| created_at | DateTime | 创建时间 |
| completed_at | DateTime | 完成时间 |

---

## 5. API 端点全览

所有端点前缀: `/api/v1`

### 5.1 健康检查
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 基础健康检查 |
| GET | `/health/ready` | 就绪检查（含 DB 连接） |

### 5.2 Agent CRUD
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agents` | 创建 agent |
| GET | `/agents` | 列出所有 agent（含实时 status） |
| GET | `/agents/{name}` | 获取单个 agent |
| DELETE | `/agents/{name}` | 删除 agent（停止进程 + CASCADE） |

### 5.3 版本管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/agents/{name}/versions` | 列出所有版本 |
| POST | `/agents/{name}/versions` | 上传 zip 包（FormData） |
| POST | `/agents/{name}/versions/activate?version=v1` | 激活版本（构建+启动） |

### 5.4 API Key 管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/agents/{name}/keys` | 列出所有 key（不含原文） |
| POST | `/agents/{name}/keys` | 创建 key（返回原文，仅一次） |
| DELETE | `/agents/{name}/keys/{id}` | 吊销 key |

### 5.5 调用（Invoke）
| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/agents/{name}/invoke` | Bearer | 同步调用 |
| POST | `/agents/{name}/invoke/stream` | Bearer | SSE 流式调用 |
| POST | `/agents/{name}/invoke/async` | Bearer | 异步调用（返回 request_id） |
| GET | `/agents/{name}/invoke/async/{id}` | 无 | 查询异步任务状态 |

---

## 6. Agent 运行时架构（核心）

### 6.1 运行时模式

```
                     ┌─────────────────┐
                     │ ManagedAgent    │
                     │ (统一抽象)       │
                     └────────┬────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
    ┌──────────────────┐            ┌──────────────────┐
    │   venv 模式       │            │   docker 模式      │
    │                   │            │                   │
    │ VenvBuilder       │            │ DockerBuilder     │
    │  ├─ uv venv       │            │  ├─ Dockerfile    │
    │  ├─ uv pip install│            │  ├─ docker build  │
    │  └─ 哈希缓存       │            │  └─ docker run    │
    │                   │            │                   │
    │ spawn_agent_      │            │ DockerRunner      │
    │ process()         │            │  ├─ start         │
    │  └─ subprocess    │            │  ├─ stop          │
    │     .Popen(...)   │            │  └─ is_running    │
    └──────────────────┘            └──────────────────┘
```

### 6.2 核心模块

#### ManagedAgent（`backend/app/runtime/manager.py`）
单个 agent 的生命周期管理器（dataclass）。

| 职责 | 关键方法 |
|------|----------|
| 构建启动 | `build_and_start()` → 按 mode 分发到 venv 或 docker |
| 停止 | `stop()` → 杀死子进程或 docker stop |
| 同步调用 | `invoke(payload, timeout, config)` → httpx POST → agent 子进程 |
| 流式调用 | `invoke_stream(payload, config)` → httpx stream → 返回行迭代器 |
| 崩溃重启 | `try_restart()` → 指数退避（delay × 2^count） |
| 存活检测 | `is_alive()` → 兼容 venv (proc.poll) 和 docker (docker inspect) |
| 限流 | `try_acquire_token()` → 令牌桶，线程安全（threading.Lock） |
| 空闲 | `idle_seconds()` → 距离上次 invoke 的秒数 |

#### AgentRegistry（`backend/app/runtime/registry.py`）
单例注册中心，存储所有运行中的 ManagedAgent。

- `set(agent)` / `remove(name)`: asyncio.Lock 保护
- `get(name)`: 无锁只读
- `all()` / `running_count()`: 遍历

#### miao_runner（`backend/agent_templates/miao_runner.py`）
每个 agent 子进程实际运行的 FastAPI 应用。负责：

1. 加载用户 `agent.py`，调用入口函数
2. 上报 Langfuse trace（input/output/latency）
3. 暴露端点: `/health`, `/invoke`, `/invoke/stream`

支持三种返回类型：
- **普通 dict** → 直接返回 output
- **async generator** → 逐 chunk SSE token 事件
- **sync generator** → 单线程迭代，`await asyncio.sleep(0)` 让出事件循环

#### VenvBuilder（`backend/app/runtime/venv.py`）
按需构建隔离 Python 虚拟环境：
1. `uv venv` 创建 venv
2. `uv pip install -r requirements.txt`（用户依赖）
3. `uv pip install fastapi uvicorn pydantic langfuse socksio`（runner 依赖）
4. SHA-256 哈希缓存：requirements 不变则跳过构建

#### DockerBuilder / DockerRunner（`backend/app/runtime/docker.py`）
Docker 模式支持：
- `build_dockerfile()`: 生成 Dockerfile + `.dockerignore`
- `DockerBuilder.build(no_cache=True)`: 每次激活强制重新构建
- `DockerRunner.start()`: `docker run -d --cpus=1.0 --memory=512m`
- 敏感凭证（Langfuse secret）通过 `-e` 运行时注入，不写入镜像层

#### TaskWorker（`backend/app/services/task_worker.py`）
异步 invoke 任务执行器（ThreadPoolExecutor）。

流程：`invoke → 更新 DB → POST webhook（指数退避重试 3 次）`

### 6.3 进程生命周期

```
用户 activate 版本
       │
       ▼
┌─────────────────┐
│ 1) 下载 zip     │  COS → /tmp/miao/agents/{name}/source.zip
│ 2) 解压代码     │  清空旧代码（保留 .venv / .build_hash）
│ 3) 构建 venv    │  VenvBuilder.needs_build() → uv pip install
│ 4) 启动子进程   │  spawn_agent_process() → Popen
│ 5) 健康检查     │  wait_for_health(port, timeout=30)
│ 6) 注册到内存   │  AgentRegistry.set(managed)
│ 7) 更新 DB      │  is_active=True, status=running
└─────────────────┘
       │
       ▼
   agent 运行中
       │
       ├─ Watchdog 每 15s 检查
       │    ├─ crashed → try_restart()
       │    ├─ 进程死了 → mark crashed
       │    └─ 空闲 > 300s → stop（保留 registry，invoke 时自动唤醒）
       │
       └─ Backend 重启时
            └─ _recover_active_agents()
                 ├─ 从 DB 查 is_active=True
                 ├─ 重新下载 zip
                 ├─ 重新 build + start
                 └─ 失败则回写 is_active=False
```

### 6.4 安全与隔离

| 维度 | 措施 |
|------|------|
| **API 鉴权** | Bearer token（SHA-256 哈希存储，原文不存 DB） |
| **Agent 隔离** | 独立子进程（独立进程组，SIGTERM → SIGKILL）或 Docker 容器 |
| **密钥安全** | Docker 模式 ENV 通过 `-e` 运行时注入，不写入镜像层 |
| **代码隔离** | 每个 agent 独立工作目录 + 独立 venv |
| **资源限制** | Docker 模式 `--cpus=1.0 --memory=512m`；venv 模式无额外限制 |
| **并发控制** | `agent_max_concurrent=10`，超过返回 429 |
| **限流** | per-agent 令牌桶 QPS=10, burst=20 |
| **CORS** | 仅允许 localhost:3000 / 127.0.0.1:3000 |

---

## 7. 前端架构

```
frontend/src/
├── app/
│   ├── layout.tsx              # 根布局（导航栏 + 侧边栏）
│   ├── page.tsx                # 首页（/ → 重定向到 /agents）
│   ├── agents/
│   │   ├── page.tsx            # Agent 列表页（/agents）
│   │   └── [name]/
│   │       └── page.tsx        # Agent 详情页（/agents/{name}）
│   └── traces/
│       └── page.tsx            # Trace 查看页（/traces）
├── components/
│   └── ui/                     # shadcn/ui 组件库
├── lib/
│   ├── api.ts                  # 后端 API 客户端封装
│   └── utils.ts
└── ...
```

### Agent 详情页面功能
| 区域 | 功能 |
|------|------|
| 状态栏 | 实时显示 agent 状态（running/crashed/stopped） |
| 版本管理 | 上传 zip、激活版本、查看版本列表 |
| API Key | 创建/吊销 key，复制新 key |
| 试运行 | 输入 JSON → 调用 invoke/stream，查看输出和 trace |

---

## 8. 完整业务流程

### 8.1 创建并激活 agent

```
前端                          后端                           COS / Agent
 │                             │                              │
 │  POST /agents               │                              │
 │  {name, description}        │                              │
 ├────────────────────────────►│ DB: INSERT agents            │
 │  ← 201 {id, name, status}   │                              │
 │                             │                              │
 │  POST /versions             │                              │
 │  FormData: zip+version      │                              │
 ├────────────────────────────►│ 校验 zip 格式                │
 │                             ├─────────────────────────────►│ upload zip → COS
 │  ← 201 {id, version}        │                              │
 │                             │                              │
 │  POST /activate?version=v1  │                              │
 ├────────────────────────────►│ DB: is_active=true           │
 │                             │ 下载 zip                     │
 │                             │ 解压代码                     │
 │                             │ VenvBuilder.build()          │
 │                             │ spawn_agent_process()         │
 │                             │ ─────────────────────────────┤ Popen
 │                             │     ← health check OK ──────┤ /health
 │                             │ Registry.set(managed)        │
 │  ← 200 {is_active, running} │                              │
```

### 8.2 同步调用

```
前端                          后端                      Agent 子进程          Langfuse / DashScope
 │                             │                              │                    │
 │  POST /invoke               │                              │                    │
 │  Bearer {api_key}           │                              │                    │
 ├────────────────────────────►│ 鉴权 → _auth_agent()        │                    │
 │                             │ 限流 → try_acquire_token()   │                    │
 │                             │ invoke(payload, config)      │                    │
 │                             ├─────────────────────────────►│ POST /invoke       │
 │                             │                              ├───────────────────►│ trace start
 │                             │                              │ invoke_fn(input)     │
 │                             │                              │ ← LLM response ────│ DashScope
 │                             │                              │ ← trace end ────────│
 │                             │ ← {output, trace_id} ────────┤                    │
 │  ← 200 {output, trace_id}   │                              │                    │
```

### 8.3 SSE 流式调用

```
后端 invoke_stream()                     Agent 子进程                   前端
 │                                         │                            │
 │ invoke_stream(payload)                   │                            │
 ├────────────────────────────────────────►│ POST /invoke/stream        │
 │  ← SSE line 1 ────────────────────────┤ yield "event: token\n..."   │
 │  ← SSE line 2 ────────────────────────┤ yield "event: token\n..."   │
 │  ...                                    │                            │
 │  ← SSE output ─────────────────────────┤ yield "event: output\n..." │ (非 generator 时)
 │  ← SSE done  ──────────────────────────┤ yield "event: done\n..."   │
 │                                         │                            │
 │  chunk_queue.put_nowait(...)            │                            │
 │  StreamingResponse → ──────────────────────────────────────────────►│ ReadableStream
 │                                                                     │ 逐 token 渲染 + 光标动画
```

### 8.4 异步调用 + Webhook

```
调用方                              后端 TaskWorker                     Webhook 接收方
 │                                     │                                  │
 │  POST /invoke/async                 │                                  │
 ├─────────────────────────────────────┤                                  │
 │                                     │ DB: INSERT InvokeTask (pending)  │
 │  ← 202 {request_id, status_url}     │                                  │
 │                                     │                                  │
 │  GET /invoke/async/{request_id}    │ Thread: invoke()                 │
 ├─────────────────────────────────────┤  ├─ DB: status=running          │
 │  ← 200 {status: "running"}          │  ├─ managed.invoke()            │
 │                                     │  ├─ DB: status=success          │
 │                                     │  ├─ POST webhook_url ──────────►│
 │                                     │  │   ← 200 OK ──────────────────┤
 │                                     │  └─ DB: webhook_delivered=true  │
 │  GET /invoke/async/{request_id}    │                                  │
 │  ← 200 {status: "success", ...}     │                                  │
```

---

## 9. 配置体系

配置文件: 根目录 `.env`

### 外部服务

```env
# Langfuse 追踪
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com

# Neon PostgreSQL
DATABASE_URL=postgresql+asyncpg://user:pass@host/db?ssl=require

# 腾讯云 COS
TENCENT_SECRET_ID=...
TENCENT_SECRET_KEY=...
TENCENT_REGION=ap-beijing
TENCENT_BUCKET=miao-agent-1355651432
COS_ENDPOINT=https://cos.ap-beijing.myqcloud.com

# DashScope LLM
DASHSCOPE_API_KEY=sk-...
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-plus
```

### Agent 运行时配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `agent_max_concurrent` | 10 | 最大并发运行 agent 数 |
| `agent_max_restarts` | 5 | 最多自动重试次数 |
| `agent_restart_base_delay` | 2.0s | 重启指数退避基数 |
| `agent_idle_timeout` | 300s | 空闲超时自动回收（invoke 时自动唤醒） |
| `agent_watchdog_interval` | 15s | Watchdog 检查间隔 |
| `agent_rate_limit_qps` | 10.0 | per-agent QPS |
| `agent_rate_limit_burst` | 20 | 令牌桶 burst 容量 |
| `agent_runtime_mode` | venv | 运行模式: venv / docker |
| `agent_docker_cpu_limit` | 1.0 | Docker CPU 限制 |
| `agent_docker_memory_limit` | 512m | Docker 内存限制 |

### 异步调用配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `invoke_async_max_workers` | 4 | 线程池大小 |
| `webhook_max_retries` | 3 | webhook 最大重试 |
| `webhook_retry_base_delay` | 1.0s | webhook 重试基数 |

---

## 10. 部署与运维

### 启动流程

```bash
# 一键启动所有服务
bash scripts/start-all.sh

# 这个脚本会:
# 1. 检查 uv / pnpm / node 是否安装
# 2. 检查 .env 文件存在
# 3. 创建后端 venv（如需要）
# 4. uv pip install 后端依赖
# 5. pnpm install 前端依赖
# 6. 检查端口 8000/3000 是否被占用
# 7. 后台启动 uvicorn (8000) + pnpm dev (3000)
```

### 进程管理

```bash
bash scripts/status.sh     # 查看服务状态 + DB 连通性
bash scripts/stop-all.sh   # 停止所有服务
```

### 后端日志

- 主日志: `/tmp/miao-backend.log`
- Agent 日志: `/tmp/miao/agents/{name}/agent.log`

### Agent 工作目录

```
/tmp/miao/agents/{agent_name}/
├── agent.py          # 用户代码
├── requirements.txt  # 用户依赖
├── miao_runner.py     # runner（自动复制）
├── .venv/            # 隔离 Python 虚拟环境
├── .build_hash       # requirements 哈希（缓存）
└── agent.log         # 子进程 stdout/stderr
```

### 已知注意事项

1. **SOCKS 代理兼容**: 如果系统有 `ALL_PROXY=socks5://...`，需要在 backend venv 中安装 `socksio`，agent venv 也通过 `venv.py` runner 依赖自动安装
2. **COS 权限**: 需要腾讯云 CAM 授权 `cos:PutObject` / `cos:GetObject`
3. **重启后需重新激活**: 后端重启会杀掉所有子进程，需重新 activate 版本（lifespan recovery 会自动尝试恢复）
4. **健康检查依赖**: `wait_for_health()` 用 httpx 连 agent 子进程，需确保同 machine localhost 连通

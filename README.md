# Miao AI

自托管、轻量级 AI Agent 平台。核心能力：
- **Trace 可观测**（基于 Langfuse）
- **Agent 上传/部署**（上传 Python 代码包，平台负责运行、版本管理）
- **API 化调用**（把已部署的 agent 暴露为统一 HTTP API）

> 详细需求与路线图见 [`docs/requirements.md`](./docs/requirements.md)。
> 计划文件见 `/Users/wuxiangyi/.claude/plans/langsmith-aiagent-langchain-langgraph-a-melodic-creek.md`。

## 目录结构

```
miao-ai/
├── infra/                  # 配置/连接模板（云服务版本，无 docker-compose）
├── demos/                  # 独立 demo（验证 trace 链路、与 backend 解耦）
├── backend/                # Phase 1+：Miao AI FastAPI 后端
├── frontend/               # Phase 1+：Miao AI Web UI
└── docs/                   # 设计文档
```

## 快速开始

> **新用户？** 先看 [`docs/QUICKSTART.md`](docs/QUICKSTART.md) — 5~10 分钟从零跑通。

```bash
# 第一次：装工具 + 注册云服务 + 填 .env
brew install uv pnpm           # 或参照 QUICKSTART
cp .env.example .env           # 填入凭证

# 启动（自动建 venv、装依赖、起两个服务）
bash scripts/start-all.sh

# 验证
bash scripts/status.sh

# 停服
bash scripts/stop-all.sh
```

启动后访问：
- 前端：<http://localhost:3000/agents>
- 后端 OpenAPI：<http://localhost:8000/docs>

## 当前状态

- ✅ **Phase 0** — 云服务 + hello-trace demo（Langfuse + DashScope + COS 链路全通）
- ✅ **Phase 1** — MVP 后端（12/12 测试通过，端到端跑通：上传 → 激活 → invoke → 看 trace）
- ✅ **Phase 1** — MVP 前端（4 个页面：Agent 列表/详情/试运行/Traces）
- ⏳ **Phase 2** — 多 agent + 健壮性
- ⏳ **Phase 3** — 增强特性（按需）

> 详细规划见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。

## 快速开始

### 1. 注册云服务（一次性）

| 服务 | 用途 | 状态 |
|---|---|---|
| **Langfuse Cloud** | Trace 可观测 | ✅ 已注册 |
| **Neon** | 业务 PG | ✅ 已注册 |
| **DashScope（通义千问）** | LLM provider | ✅ 已注册 |
| **腾讯云 COS** | 存 agent 代码包 | ⏳ 待注册 |

注册完分别拿到：
- Langfuse: Public Key / Secret Key / 区域
- Neon: connection string（`postgres://...`）
- 腾讯云 COS: SecretId / SecretKey / region / bucket 名（带 APPID 后缀）
- DashScope: API Key（OpenAI 兼容模式）

### 2. 跑 hello-trace demo

```bash
cd demos/hello-trace
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 复制 .env.example 为 .env，按需修改
cp .env.example .env
python agent.py
```

执行后到 Langfuse UI 看 trace。

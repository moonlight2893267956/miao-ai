# Miao AI — 项目规划与 Roadmap

> 自托管个人级 AI Agent 平台。Trace 可观测 + Agent 上传/部署 + API 调用。
> 详细需求分析见 `.claude/plans/langsmith-aiagent-langchain-langgraph-a-melodic-creek.md`（plan mode 产出），本文档反映实际推进的 roadmap。

## 愿景

做一个**个人可用的 LangSmith 替代品**——把"自己写的 LangChain / LangGraph agent"暴露成稳定 API，并把每次调用记入可观测系统。

不追求多租户、可视化编排等大平台特性，专注"开发者视角的开箱即用"。

---

## 核心能力

| 能力 | 来源 | 状态 |
|---|---|---|
| Trace 可观测 | Langfuse Cloud（自托管已弃用） | ✅ |
| Agent 上传（zip） | 自建 | ✅ |
| Agent Runtime（venv + 子进程） | 自建 | ✅ |
| 版本管理 + 激活 | 自建 | ✅ |
| API Key 鉴权 | 自建（sha256 哈希） | ✅ |
| 统一 invoke API | 自建 | ✅ |
| Langfuse 自动 trace 注入 | miao_runner 自动上报 | ✅ |
| 业务 PG 存储 | Neon（serverless） | ✅ |
| 对象存储 | 腾讯云 COS | ✅ |
| LLM Provider | DashScope 通义千问（OpenAI 兼容） | ✅ |
| 轻量 Web UI | Next.js + 自写 shadcn 风格组件 | ✅ |

---

## 里程碑

| 里程碑 | 内容 | 状态 | 验证 |
|---|---|---|---|
| **Phase 0** | 云服务账号 + hello-trace demo 跑通 | ✅ | smoke test 通过；Langfuse Cloud 看到 trace |
| **Phase 1a** | 后端骨架（FastAPI + SQLAlchemy async + Neon 连通） | ✅ | `/health/ready` 返回 `db:ok` |
| **Phase 1b** | 实体 + CRUD（Agent / AgentVersion / ApiKey + Alembic） | ✅ | 5/5 测试；3 张表部署到 Neon |
| **Phase 1c** | Agent Runtime（uv venv + COS + 子进程 + AgentRegistry） | ✅ | 端到端：zip → venv 构建 → 子进程 health OK |
| **Phase 1d** | invoke API + API Key 鉴权 + Langfuse 注入 | ✅ | curl 端到端拿到 qwen-plus 回答 + trace_id |
| **Phase 1e** | 集成测试 + 文档 | ✅ | 12/12 pytest；e2e_smoke.sh |
| **Phase 1 前端** | Next.js 管理 UI（3 个页面） | ✅ | 4 页面 200；CORS 修好；asChild warning 修好 |
| **Phase 2** | 多 agent + 健壮性 | ✅ | AgentRegistry 加锁 + 崩溃重启 + lifespan 恢复 + 空闲回收 + 端口重试 + watchdog |
| **Phase 3** | 增强特性 | ⏳ | SSE 流式 ✅ · Webhook ✅ · Docker ✅ → 剩余按需 |

**当前测试覆盖**：12 个 pytest（health 2 + agents 5 + invoke 5），全过，约 90 秒（含 Neon 网络延迟）。

---

## Phase 2 — 多 agent + 健壮性 ✅

**目标**：让后端能放心用在日常，重启 / 崩溃不丢状态。

| 任务 | 价值 | 工作量 | 状态 |
|---|---|---|---|
| 进程崩溃自动重启（指数退避 + 连续 N 次 → crashed） | 高 | 中 | ✅ |
| `main.py` lifespan 启动时从 DB 恢复 is_active 的 agent | 高 | 中 | ✅ |
| 空闲超时回收（长时间没调用的 agent 停掉，省资源） | 中 | 中 | ✅ |
| 健康检查 watchdog（定期探活子进程） | 中 | 小 | ✅ |
| 进程池限制（同时运行的 agent 上限） | 中 | 小 | ✅ |
| 限流（per-agent QPS 令牌桶） | 中 | 小 | ✅ |
| AgentRegistry 并发安全（asyncio.Lock） | 中 | 小 | ✅ |
| 端口冲突自动重试 | 中 | 小 | ✅ |

~~预计 1~2 周。~~ 2026-06-22 全部完成。

---

## Phase 3 — 增强特性（按需选做）

互相独立，按价值优先级排：

| 任务 | 价值 | 状态 |
|---|---|---|
| **SSE 流式输出**（LangChain/LangGraph 的 stream 转发） | 高 | ✅ 2026-06-22 |
| **Webhook 异步回调**（长任务 + 结果推送） | 高 | ✅ 2026-06-23 |
| **Docker per-agent 沙箱**（替代 venv，强隔离 + 资源限额） | 中 | ✅ 2026-06-23 |
| **Langfuse Datasets 评估**（拿生产 trace 做批量打分） | 中 | 待定 |
| **多用户 + 团队协作**（如果届时真的需要） | 中 | 待定 |
| **移动端 App** | 低 | 不做 |

---

## 不做什么（明确边界）

> 这些是 plan 阶段就明确划掉的，避免范围蔓延。

- ❌ **多租户 / 复杂权限 / 计费** — 个人使用，不需要
- ❌ **拖拽式 agent 可视化编排** — 用代码，未来再说
- ❌ **Prompt 版本管理 / 自动化评估** — 暂时用 Langfuse 自带
- ❌ **移动端 App**
- ❌ **Marketplace / 团队协作**
- ❌ **SSO / 多用户登录**（单用户免登录）

---

## 关键技术决策（重要 + 原因）

| 决策 | 选择 | 原因 |
|---|---|---|
| 可观测 | **Langfuse Cloud** | 原计划自托管 docker compose，本机拉镜像弃用；改 Cloud 免运维 |
| 业务 DB | **Neon**（serverless PG） | 免费层够用、HTTP 连接友好 |
| 对象存储 | **腾讯云 COS**（不是 R2） | 国内访问快；S3 兼容 API 通用 |
| LLM Provider | **DashScope**（OpenAI 兼容） | 国内访问快、便宜、qwen-plus 强 |
| Agent Runtime 隔离 | **uv venv**（不是 Docker） | 个人使用信任风险低；Phase 3 升级 Docker |
| 包管理（Python） | **uv** | 替代 pip + venv，快 |
| 包管理（前端） | **pnpm** | 快、disk 占用少 |
| ORM | **SQLAlchemy 2.0 async**（不是同步） | FastAPI 是 async 框架 |
| 连接池 | **NullPool**（不是 QueuePool） | Neon serverless 不需要池化 |
| 日志 | **structlog JSON**（不是 logging） | 方便生产聚合（loki / elasticsearch） |
| 进程模型 | **常驻子进程**（不是 serverless） | 简单、可控；适合个人使用规模 |
| Trace 注入 | **miao_runner 整体 trace**（不是 callback 注入） | 简单 + 80% 价值；用户想细 trace 自己加 callback |
| 前端 UI | **自写 shadcn 风格**（不是 shadcn CLI） | 避免交互 + 装 Radix；自写够用 |

---

## 已知限制 / 后续可优化

### 后端
- ~~**Process 崩溃不会自动重启**~~ → ✅ Phase 2 已修复
- ~~**重启 server 后 is_active 的 agent 不会自动起来**~~ → ✅ Phase 2 已修复
- **venv 缓存粒度**（基于 requirements 哈希）— 增加包后强制重建
- **CORS 只 allow 了 localhost:3000** — 生产需要配置域名
- ~~**没有流式输出**（SSE）~~ → ✅ Phase 3 已实现

### 前端
- **没有用户认证**（单用户）
- **没有 agent 详情的实时 status 轮询**（需要手动刷新）
- **trace 查看走 iframe 链接**（不嵌入）— Langfuse 限制

### DevOps
- **没有 CI/CD**（没 git repo 也没必要）
- **没有 .env 模板加密**（.env 在 .gitignore）
- **没有监控告警**（个人规模不必要）

---

## 资源

- [Langfuse 文档](https://langfuse.com/docs)
- [Neon 控制台](https://console.neon.tech)
- [腾讯云 COS](https://console.cloud.tencent.com/cos)
- [DashScope 控制台](https://dashscope.console.aliyun.com)

---

## 文档清单

| 文档 | 位置 | 用途 |
|---|---|---|
| 本文档（roadmap） | `docs/ROADMAP.md` | 项目规划、当前状态、Phase 计划 |
| 快速开始 | `docs/QUICKSTART.md` | 5~10 分钟从零跑通 |
| 项目总览 | `README.md` | 一句话简介 + 当前状态 |
| 云服务注册 | `infra/CLOUD_SETUP.md` | 4 个云服务的注册步骤 |
| 后端使用 | `backend/README.md` | API 速查 + 完整使用流程 |
| 前端使用 | `frontend/README.md` | 页面功能 + 开发指南 |
| 端到端冒烟 | `backend/scripts/e2e_smoke.sh` | curl 跑完整流程 |
| 启动脚本 | `scripts/start-all.sh` / `stop-all.sh` / `status.sh` | 一键启停 |

---

## 下一步决策

- **优先 Phase 2**（健壮性）— 让后端能放心用
- **优先前端补强**（实时轮询、错误处理、loading 状态）— UX 改进
- **优先 Phase 3 某个特性**（如 SSE 流式）— 取决于实际需求
- **暂停** — 等有真实使用场景再决定

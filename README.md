# Miao AI

> 自托管、轻量级 AI Agent 平台：用户上传 Python agent 代码包 → 平台负责 build / 版本管理 / 容器化运行 → 暴露为统一 HTTP API（含流式响应）。FastAPI + Next.js + Docker，Langfuse 全链路 trace，Fernet 加密 provider 凭证，MySQL 持久化。

## 是什么

- **多 agent 平台**：每个 agent = 独立运行时（venv 子进程 或 docker 容器），互不干扰
- **统一网关**：一个域名（生产 `https://agent.yunmiao.site`）反代 frontend + backend + 内部 agent 端口
- **按需启动**：invoke 来时自动拉起，5 分钟空闲自动回收（watchdog）
- **可观测**：每次 invoke 写到 Langfuse，trace_id 在响应里返回
- **安全**：provider API key Fernet 加密存 DB；API key 明文只创建时返回一次

## 架构速览

```
浏览器 (https://agent.yunmiao.site)
  │
  ├─ /            → nginx / cloudflared → miao-frontend:3000
  └─ /api/        → nginx / cloudflared → miao-backend:8000
                                        │
                                        ├─ invoke 来时启动 agent 容器
                                        │   └─ miao-{name}:8080 (per-agent)
                                        ├─ MySQL
                                        ├─ 腾讯云 COS（agent 代码包）
                                        ├─ Langfuse（trace）
                                        └─ DashScope / OpenAI（LLM）
```

完整架构：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 快速开始（5 分钟）

**新用户**：[`docs/operations/local-dev.md`](docs/operations/local-dev.md) — 装工具 + 配 `.env.local`（本地隔离生产）+ 起服务 + 第一次调通

**新运维**：[`docs/operations/deployment.md`](docs/operations/deployment.md) — 服务器部署 + 升级 + 回滚 SOP

## 文档导航

| 文档 | 用途 |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 完整架构设计（API/DB/runtime/widget 都有） |
| [`docs/operations/local-dev.md`](docs/operations/local-dev.md) | 本地开发：装工具、起服务、第一次用 |
| [`docs/operations/deployment.md`](docs/operations/deployment.md) | 部署 SOP：服务器、nginx、HTTPS |
| [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) | 排错手册：6 类常见问题 |
| [`docs/operations/security.md`](docs/operations/security.md) | 安全规范：凭证 / 加密 / 授权 |
| [`docs/operations/monitoring.md`](docs/operations/monitoring.md) | 监控：日志位置 / 健康探针 / Langfuse |
| [`docs/api-reference.md`](docs/api-reference.md) | API 端点速查 |
| [`docs/design/ui-design-system.md`](docs/design/ui-design-system.md) | 前端设计系统 |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 项目路线图 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 开发规范：commit / PR / 文档同步规则 |
| [`CHANGELOG.md`](CHANGELOG.md) | 变更日志 |
| [`docs/history/`](docs/history/) | 历史归档（已闭环的 code review、任务、部署过程） |

## 子目录速读

- `backend/` — FastAPI 后端，Pytest 测试，看 [`backend/README.md`](backend/README.md)
- `frontend/` — Next.js 前端，看 [`frontend/README.md`](frontend/README.md)
- `scripts/` — `start-all.sh` / `stop-all.sh` / `status.sh` 一键起停
- `infra/CLOUD_SETUP.md` — 4 个云服务怎么注册的供应商侧步骤
- `demos/` — 独立 demo（hello-trace / sample-agent / diff-explainer）

## 仓库

- GitHub: `git@github.com:moonlight2893267956/miao-ai.git`
- 分支：`main`
- 部署：先 push → 服务器 `git pull --ff-only` → `docker compose -f docker-compose.prod.yml up -d --build`

## 许可证

TBD

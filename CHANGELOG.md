# Changelog

> 全部可见变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
> 详细 API 变更看 `docs/api-reference.md` 和 git log。

## [Unreleased]

## [0.4.0] - 2026-06-28

### Changed
- 文档结构整理：根目录 `README.md` 重写为项目门面；`docs/QUICKSTART.md` → `docs/operations/local-dev.md`；`docs/DEPLOYMENT_PLAN.md` 拆为 `docs/operations/deployment.md`（SOP）+ `docs/history/2026-06-deployment-process.md`（过程）；3 份已闭环的 code review / 任务文档归档到 `docs/history/`
- 新增 `CONTRIBUTING.md`（commit 规范、PR 流程、文档同步规则、安全规范）

### Added
- `docs/operations/troubleshooting.md`（9 类常见问题 + 修法）

## [0.3.0] - 2026-06-27

### Added
- `POST /api/v1/agents/{name}/stop` — 停 agent 容器/进程，DB 定义保留
- `POST /api/v1/agents/{name}/activate` — 拉起 stopped agent
- 本地 vs 生产环境隔离：`.env.local` 优先于根 `.env`（pydantic-settings v2 后者覆盖前者）
- 详情页 header toolbar（status badge + 模型选择 + stop/activate/delete）
- 状态名 `stopped` 统一（与 watchdog idle_stop 共享终态）

### Fixed
- `shared_network + container_name DNS` 修复容器跨网络通信（commit `b751320`）
- backend healthcheck URL 引号缺失的 SyntaxError
- CORS 加入 `https://agent.yunmiao.site`
- Neon dev branch `search_path` 默认空的怪行为（`db.py` 加 connect event listener）

## [0.2.0] - 2026-06-22 ~ 2026-06-25

### Added
- 生产 Docker 部署：`Dockerfile` / `docker-compose.prod.yml` / `.dockerignore`
- 阿里云 Debian + PyPI 镜像源（`BUILD_APT_MIRROR` / `BUILD_PIP_INDEX_URL`）
- backend 容器内 apt 代理（`172.17.0.1:7890` 走 bridge 网关）
- backend 容器挂载 `/var/run/docker.sock` 支持 docker runtime
- agent 容器 shared_network + 容器名 DNS
- `miao_runner` 绑 `0.0.0.0`（容器间可访问）
- 鉴权：cookie session + bcrypt + 简易登录页
- `users` / `user_sessions` 表迁移
- nginx 反代：宝塔 extension 配置（`/api/` → backend, `/` → frontend）
- SSE 流式接口 `proxy_buffering off`

### Fixed
- backend 启动异步化（recovery 不阻塞 lifespan）
- idle 唤醒跳过 build（`image_exists` 缓存）
- docker mode health check 配置

## [0.1.0] - 2026-06-15

### Added
- 模型管理系统（`d046e24`）
  - `model_providers` / `llm_models` / `agent_versions` / `api_keys` / `invoke_tasks` 表
  - Fernet 加密 provider API key
  - 三级回退链：Agent 绑定 → 全局默认 → .env
  - API key 创建时返回明文，存 hash
  - 同步 / 流式 invoke 端点
- 架构文档 `docs/ARCHITECTURE.md`（6/17 更新到 v0.2.0）
- 完整 e2e_smoke.sh 测试
- Langfuse 追踪全链路打通

## [0.0.1] - 2026-06-08

### Added
- 初始化仓库（`f259797`）
- demos/hello-trace 独立 demo 验证 trace 链路
- 4 个云服务基础集成：Langfuse / Neon / 腾讯云 COS / 阿里云 DashScope

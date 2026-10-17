# Changelog

> 全部可见变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
> 详细 API 变更看 `docs/api-reference.md` 和 git log。

## [Unreleased]

### Added
- 多轮会话（Conversation）能力 —— 需求文档 P0+P1（平台侧）与 P3（Try Run UI）；
  业务 agent 的追问逻辑（P2）与调用方 SDK 接入不在本次范围：
  - **平台侧（P0+P1）**
    - 会话管理：`POST/GET/DELETE /api/v1/agents/{name}/conversations[/{cid}]`，per-agent API Key 鉴权，会话按 `api_key_id` 隔离（他人的 key 一律 403）
    - 带会话调用：`metadata.conversation_id`（`invoke` / `invoke/stream` / `invoke/async` 语义一致）；响应新增 `conversation_id` / `turn` / `history_truncated` / `injected_turns`，流式补发 `conversation` 事件，异步 webhook 载荷带 `conversation_id` / `turn`
    - 历史注入：仅当生效版本 `agent.yaml` 声明 `conversation.enabled: true` 时才注入 `config.conversation`；两级裁剪（轮数 → 32KB 字节），第 1 轮永不裁剪
    - 轮次记账：先落 `running` 占位再执行，结束回写 `succeeded` / `failed`；同会话并发 1（409）、轮次上限 50（429）、过期 410
    - `agent.yaml` 新增顶层 `conversation` 段（`enabled` / `max_history_turns`），见 `docs/agent-contract.md` §4.4
    - 迁移 `c4e8f1a2b9d7`：`conversations` / `conversation_turns` 两张表
  - **Try Run UI（P3）**
    - 会话开关（未声明 `conversation.enabled` 的 agent 置灰并提示）、开启后首次 Run 惰性建会话、换 API Key 自动重置
    - 输出区改为**轮次列表（最新在上）**，支持 SSE 逐 token 追加；每轮显示 轮次号 / 状态 / **本轮注入给 agent 的历史（`injected_turns`）** / 是否裁剪 / trace / 入参摘要
    - 「重置」按钮（清本地状态并尽力删除服务端会话）
  - **前端 API**：`createConversation` / `getConversation` / `deleteConversation`，`metadata` 支持 `conversation_id`；
    SSE 解析新增 `event: conversation` 分支（此前未知事件会被当 token 拼进正文）
- **执行事件上报（Agent SDK，平台通用能力）** —— 长耗时 agent 的等待期可见：
  `demos/wubi-explainer` 的联网检索（单次 4~7s）与模型调用（单次上限 70s）此前不可见，
  现在每一次外部调用都会上报工具名 + 入参 + 出参 + 耗时 + 成败。**与具体 agent 无关，任何
  agent 三步即可接入**（见 `docs/agent-sdk.md`）：
  - 新增 `backend/agent_templates/miao_sdk.py`（**Agent SDK**）：`tool()` 上下文管理器
    （自动分配 `call_id`、计时、异常也成对收尾且**原样抛出**）、`thinking()`、`log()`、
    `emit()`（自定义 `kind` 逃生通道）、`stream()`（线程化桥接）。sink 存 `threading.local()`
    并发不串味；无 sink 时零开销、异常被吞；`seq` 每条流从 1 单调递增；
    文本/对象有体积上限并**显式标记** `truncated`
  - 协议设计对齐 OpenAI Responses / Anthropic Messages 的流式事件：**配对靠 `call_id`
    而非状态字段**，平台**只认 `kind` 一个字段**、其余原样透传（`kind` 永不设白名单）。
    有意偏离两处：入参出参是**对象**（服务端确定性调用无需流式拼接参数）、
    当前不做 `*.delta` 增量（`call_id` 已预留配对语义，以后可加）
  - `miao_runner.py` 认第四种流式块 `{"exec": {...}}` → **`event: exec`**
    （独立事件名；不当正文、不计入 `done.output`；判断顺序固定为 收尾块 → 事件块 → 正文块，
    老 agent 零影响）；最近 ≤200 条记进 Langfuse span `metadata.exec_events`；
    **`_EventRelay` 兜底**：agent 若手写 generator 而非用 `stream()`，`tool()` 的事件
    由 runner 接住插进流里（否则会静默少几条，极难排查）；每轮 invoke 开始清 SDK 状态
  - SDK 与 runner 同目录，由平台注入：Dockerfile 第一层 `COPY miao_runner.py miao_sdk.py /app/`，
    venv 模式 `sys.path[0]` 即 runner 目录 —— agent **不需要**把 SDK 打进 zip 或写进
    requirements.txt；`sync_runtime_files()` 保证两者成对拷进构建上下文；SDK 缺失时 runner
    仍能启动（软导入 + 一行 warning）
  - 平台 `/invoke/stream` 无需改动即透传（原本对未知事件名 `yield line`），
    现补 `test_invoke_exec_passthrough.py` 把这条隐式契约显式锁住（含「不污染轮次 output」）
  - Try Run 识别 `event: exec` 并渲染「执行过程」：工具调用按 `call_id` 折叠成一行
    （名称 + 耗时 + 成败 + 出参摘要），`log` 按级别着色，未知 `kind` 降级为通用条目
  - 测试：`test_agent_sdk_exec.py`（27）/ `test_runner_stream_exec.py`（10，含 fixture agent
    与 SDK 缺失兜底）/ `test_invoke_exec_passthrough.py`（5）—— 均不依赖具体 agent；
    `demos/wubi-explainer/evals/check_exec_events.py` 离线验证「工具调用成对、入参出参来自
    真实执行、阻塞期间先到、失败如实上报、非流式零事件、事件不引用模型文本」
  - `demos/wubi-explainer` 作为首个消费者接入：`wubi/progress.py` 绑定平台 SDK，
    把三个**真实外部调用**包成工具 —— `tables.lookup`（内置码表）、`tavily.search`（联网检索）、
    `llm.decide`（模型判断，单字 / 词组 / 追问三条路径）。本 agent 的两个取舍：
    **不发 thinking**（模型侧关掉思维链，没有可报的思考就不编）、
    **校验不通过发 `log(warn)`** 而不是伪装成工具事件（工具 = 外部调用，校验是内存计算）

### Fixed
- agent 镜像只 COPY 入口文件，**多文件 agent 起不来**：`backend/agent_templates/Dockerfile.template`
  最后一层由 `COPY agent.py /app/agent.py` 改为 `COPY . /app/`，并让生成的 `.dockerignore` 排除
  `evals/`。agent 拆成包之后（如 `wubi-explainer` 的 `agent.py + wubi/ + data/`）镜像里只有 `agent.py`，
  启动即 `ModuleNotFoundError: No module named 'wubi'` 退出（exit 1）→ 健康检查失败 → 平台反复重建，
  表现为「agent 起不来」（线上 wubi-explainer 即此症状）。修复后多文件 agent 上传即可用。
- 流式输出（SSE）与多轮会话的两处断链：
  - 逐 token 流的 agent 原来无法把结构化结果交给平台 → 该轮在会话历史里 output 为空，
    下一轮追问会报「追问需要上下文」。新增收尾块约定 `yield {"output": {...}}`：runner 不当
    token 转发，而是随 `event: done` 下发，平台据此落库该轮 output（`backend/agent_templates/miao_runner.py`）
  - 前端流式渲染在 `done` 时只尝试 `JSON.parse(拼起来的正文)`，现优先使用 `done.output`
    （`frontend/src/app/agents/[name]/page.tsx`）
- `demos/wubi-explainer` 补齐流式实现（此前只有它和 json-repairer 没做，勾了「流式输出」没有打字机效果）：
  `invoke` 在 `config["stream"]` 为真时返回 generator，按块吐正文 + 收尾块给结构化结果
- 异步 invoke（`/invoke/async`）两处既有缺陷（此前无测试覆盖，链路实际不可用）：
  - 工作线程自建事件循环却复用主循环的连接池 → `Future attached to a different loop`，且失效连接退回池中会污染后续主循环请求；现改为 agent 调用留在工作线程、DB 操作统一用 `run_coroutine_threadsafe` 回主事件循环
  - `invoke_tasks.id` 是 Uuid 列，绑定 `str` 触发 `'str' object has no attribute 'hex'`，任务状态永远停在 `pending` 且 webhook 不投递；现按 UUID 对象绑定

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

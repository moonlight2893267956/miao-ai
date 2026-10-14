# API 速查

> 完整架构 / 数据模型 / runtime 细节见 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
> 部署 / 本地 dev / 排错 见 [`docs/operations/`](operations/)

所有路径前缀：`/api/v1`。鉴权用 session cookie（`POST /auth/login` 拿）或 agent API key（`Authorization: Bearer miao_...`）。写操作（除 login）走 `login_required` 依赖。

## 目录

- [Auth](#auth)
- [Health](#health)
- [Agents](#agents)
- [Conversations（多轮会话）](#conversations多轮会话)
- [Models](#models)
- [Providers](#providers)
- [通用约定](#通用约定)

---

## Auth

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/auth/login` | 无 | 登录拿 session cookie（HttpOnly） |
| POST | `/auth/logout` | session | 登出，删除 session |
| GET | `/auth/me` | session | 当前用户信息 |

**请求体 login**：
```json
{"username": "yunmiao", "password": "..."}
```

**响应**：`200 {"id": 1, "username": "yunmiao", "is_active": true}`

---

## Health

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/health` | 无 | 进程探活（200 OK） |
| GET | `/health/ready` | 无 | 含 DB 探针（200 `{"status":"ready","db":"ok"}`） |

---

## Agents

### Agent CRUD

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/agents` | session | 列表（含运行时 status） |
| GET | `/agents/{name}` | session | 详情 |
| POST | `/agents` | session | 创建（body: `{"name":"my-agent"}`） |
| DELETE | `/agents/{name}` | session | 删除（CASCADE versions + keys + 停容器） |
| POST | `/agents/{name}/stop` | session | 停 agent 容器/进程，DB 定义保留 |
| POST | `/agents/{name}/activate` | session | 拉起 stopped agent |

### Versions

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/agents/{name}/versions` | session | 列表 |
| POST | `/agents/{name}/versions` | session | 上传 zip（multipart: `version`, `file`） |
| POST | `/agents/{name}/versions/{version}/activate` | session | 切到指定 version（重新 build/start） |
| GET | `/agents/{name}/versions/{version}/artifact` | session | 下载 zip |

### API Keys

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/agents/{name}/keys` | session | 列表（**不含明文 token**） |
| POST | `/agents/{name}/keys` | session | 创建（body: `{"label":"my-key"}`），**只这一次返回明文** |
| DELETE | `/agents/{name}/keys/{id}` | session | 撤销（设置 `revoked_at`） |

**创建返回**：
```json
{"id": "uuid", "key": "miao_RgdOmyhxOKfb6frCyVpbbhFXneikjlQwYbqCVieo4so", "label": "my-key"}
```

### Invoke

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/agents/{name}/invoke` | API key | 同步调用（返回完整结果） |
| POST | `/agents/{name}/invoke/stream` | API key | 流式调用（SSE / NDJSON） |
| POST | `/agents/{name}/invoke/async` | API key | 异步调用，立即返回 `request_id`，完成后 POST `webhook_url` |
| GET | `/agents/{name}/invoke/async/{request_id}` | **无** | 查异步任务状态 / 结果 |

**请求体**：
```json
{
  "input": {"question": "..."},
  "metadata": {
    "user_id": "alice",
    "session_id": "session-001",
    "tags": ["dev", "isolation-test"],
    "conversation_id": "conv_1f0c…"
  }
}
```

- `metadata.user_id` / `session_id` / `tags` → 仅用于 Langfuse trace（`session_id` **不是**对话轮次）。
- `metadata.conversation_id` → 可选，多轮会话标识（见下节）。不传时行为与不带会话完全一致。

**响应（同步）**：
```json
{
  "output": {"answer": "..."},
  "trace_id": "7537ec09049bca06e14afc951142c5bf",
  "conversation_id": "conv_1f0c…",
  "turn": 2,
  "history_truncated": false,
  "injected_turns": [1]
}
```

后四个字段**只在带 `conversation_id` 时出现**；不带会话的响应与改造前逐字节一致。
`injected_turns` 是本轮**实际注入给 agent** 的轮次号（裁剪后可能带缺号，如 `[1, 3]`；
agent 未声明 `conversation.enabled` 时为空数组），Try Run 用它显示「本轮 agent 看到了哪几轮」。

**响应（流式）**：`text/event-stream`。带会话时，流的最前面会补发一条会话事件，再原样转发 agent 的事件：

```
event: conversation
data: {"conversation_id": "conv_1f0c…", "turn": 2, "history_truncated": false, "injected_turns": [1]}

event: token
data: {"token": "「照」的五笔"}     ← agent 逐段 yield 的正文（前端做打字机）

event: token
data: {"token": "编码是 jvko…"}

event: done
data: {"trace_id": "...", "latency_ms": 4192,
       "output": {"char": "照", "code": "jvko", ...}}   ← 有收尾块时才有
```

事件形状（agent 侧约定见 `agent_templates/miao_runner.py`）：

| 事件 | 何时出现 | 平台怎么用 |
|---|---|---|
| `token` | agent 逐段 yield `{"token": ...}` | 原样转发，前端逐段渲染（打字机） |
| `output` | agent 返回普通 dict（不是 generator） | 平台取它作为该轮 output；前端一次性展示，**没有打字机** |
| `done` | 流结束 | `trace_id` 建 Langfuse 链接；若带 `output`（agent yield 的收尾块）则作为该轮 output 落库 |
| `error` | agent / 转发出错，或客户端断连 | 该轮记 `failed`，不参与后续历史注入 |

> 带会话时**必须**让 `done.output` 或 `output` 事件能给出结构化结果，否则这一轮在会话历史里
> output 为空，下一轮追问会失去依据（平台会明确报「追问需要上下文」）。

**响应（异步）**：`202 {"request_id": "miao_req_…", "status": "pending", "status_url": "…"}`；
webhook 载荷在 `success` / `failed` 两种情况下都会多带 `conversation_id` 与 `turn`。

---

## Conversations（多轮会话）

会话由**平台**保存历史、agent 保持无状态：每轮调用平台先落一条 `running` 占位轮次，
执行完回写 `succeeded` / `failed`，并把裁剪后的历史注入 agent 的
`config.conversation`（仅当生效版本的 `agent.yaml` 声明了 `conversation.enabled: true`）。

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/agents/{name}/conversations` | API key | 新建（201） |
| GET | `/agents/{name}/conversations` | API key | 列表（仅当前 key 创建的会话，`limit` / `offset`） |
| GET | `/agents/{name}/conversations/{cid}` | API key | 详情（含全部轮次，按 `index` 升序） |
| DELETE | `/agents/{name}/conversations/{cid}` | API key | 删除（204，轮次级联删除） |

**新建 body**（可省略整个 body，用平台默认 TTL）：
```json
{"metadata": {"source": "wubi-toolbox"}, "ttl_seconds": 3600}
```

**新建响应**：
```json
{
  "conversation_id": "conv_1f0c…",
  "agent_name": "wubi-explainer",
  "created_at": "2026-10-11T08:40:00Z",
  "expires_at": "2026-10-11T09:40:00Z",
  "turn_count": 0
}
```

**详情响应**：在概要字段之外多出 `metadata` / `updated_at` / `turns[]`，
每轮形如 `{"index": 1, "input": {...}, "output": {...}, "status": "succeeded", "trace_id": "...", "created_at": "..."}`。

**注入给 agent 的 `config.conversation`**（节选需求文档 §5.1）：
```json
{
  "id": "conv_1f0c…",
  "turn": 2,
  "max_turns_injected": 10,
  "dropped_turns": 0,
  "truncated": false,
  "turns": [{"index": 1, "input": {"char": "看"}, "output": {"answer": "..."}}]
}
```

平台同时把这一轮注入了哪几轮回报给调用方（响应里的 `injected_turns` / 流式 `conversation` 事件），
便于调试「agent 到底看到了什么」。

**裁剪规则**（平台侧，见 `app/services/conversation.py`）：
先按轮数只保留最近 `max_history_turns` 轮，再按字节上限（默认 32KB）继续丢最旧的，
**第 1 轮永不裁剪**（上下文锚点），因此裁剪后可能出现缺号；发生裁剪时
响应里 `history_truncated: true`、注入体里 `truncated: true` 且 `dropped_turns` 给出丢弃轮数。

**隔离与过期**：会话归属到创建它的 API Key，其他 key 读写一律 403；
过期会话不能再追加轮次（invoke 返回 410），但**详情 / 列表 / 删除不拦截过期**，
历史仍可审计。

---

## Models

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/models` | session | 列表 |
| POST | `/models` | session | 创建（绑定 provider + model_id + 默认标志） |
| PATCH | `/models/{id}` | session | 改（model_id / is_default） |
| DELETE | `/models/{id}` | session | 删（最后 1 个禁止删） |
| POST | `/models/{id}/default` | session | 设为全局默认 |

**创建 body**：
```json
{
  "name": "qwen-plus-prod",
  "provider_id": 1,
  "model_id": "qwen-plus",
  "is_default": true
}
```

---

## Providers

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/providers` | session | 列表（**不含明文 api_key**） |
| POST | `/providers` | session | 创建（body: `{"name":"dashscope","base_url":"...","api_key":"sk-..."}`，api_key 自动 Fernet 加密） |
| PATCH | `/providers/{id}` | session | 改（name / base_url / api_key） |
| DELETE | `/providers/{id}` | session | 删（被 model 引用时禁止） |

---

## 通用约定

### 错误响应

| Code | 含义 |
|---:|---|
| 400 | 请求体 schema 不对（Pydantic 校验失败） |
| 401 | 未登录 / API key 错 / 已撤销 |
| 403 | 已登录但权限不够；会话归属不是当前 API key |
| 404 | 资源不存在（含 `conversation_id` 不存在或格式非法） |
| 409 | 资源冲突（如重名 agent）；同一会话已有请求在跑 |
| 410 | 会话已过期，请新建会话 |
| 422 | 业务规则拒绝（如最后 1 个 provider 不能删、`ttl_seconds` 超上限） |
| 429 | 触发限流；或会话已达最大轮次 |
| 500 | 内部错误（看后端日志） |
| 503 | 服务暂不可用（agent 未就绪 / DB 不可达） |

**错误体**：
```json
{"detail": "Agent 'xxx' not found"}
```

### 分页

目前列表端点**不分页**（数据量小）。如果以后要加，统一用 `?page=1&size=20`。

### 时间戳

- API 返回：ISO 8601 字符串（`2026-06-27T15:21:00+08:00`）
- DB 存：UTC `datetime`

### ID 风格

- `Agent.name`：字符串（如 `qwen-chat`），URL 路径直接用
- `Model.id` / `Provider.id` / `Key.id`：整数自增
- `Version.id`：UUID 字符串

### CORS

- 严格白名单（见 `backend/app/main.py`）
- 当前允许：`http://localhost:3000` / `https://agent.yunmiao.site`
- 加新源必须改后端 + 重启

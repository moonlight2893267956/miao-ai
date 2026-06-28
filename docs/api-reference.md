# API 速查

> 完整架构 / 数据模型 / runtime 细节见 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
> 部署 / 本地 dev / 排错 见 [`docs/operations/`](operations/)

所有路径前缀：`/api/v1`。鉴权用 session cookie（`POST /auth/login` 拿）或 agent API key（`Authorization: Bearer miao_...`）。写操作（除 login）走 `login_required` 依赖。

## 目录

- [Auth](#auth)
- [Health](#health)
- [Agents](#agents)
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

**请求体**：
```json
{
  "input": {"question": "..."},
  "metadata": {
    "user_id": "alice",
    "session_id": "session-001",
    "tags": ["dev", "isolation-test"]
  }
}
```

**响应（同步）**：
```json
{
  "answer": "...",
  "trace_id": "7537ec09049bca06e14afc951142c5bf",
  "model": "qwen-plus"
}
```

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
| 403 | 已登录但权限不够（项目目前用得少） |
| 404 | 资源不存在 |
| 409 | 资源冲突（如重名 agent） |
| 422 | 业务规则拒绝（如最后 1 个 provider 不能删） |
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

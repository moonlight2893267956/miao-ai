# Miao AI — 代码审查报告

> 审查日期：2026-06-22 | 审查范围：全项目（backend + frontend）
> 修复日期：2026-06-22 | 6 项已修复 ✅

---

## 修复状态

| # | 问题 | 状态 |
|---|------|------|
| 1 | trace context 不传递到子进程 | ✅ 已修复 |
| 2 | active_version 永远为 None | ✅ 已修复 |
| 3 | 文件句柄泄漏 | ✅ 已修复 |
| 4 | VenvBuilder 错误信息缺失 | ✅ 已修复 |
| 7 | 前端 alert() 错误处理 | ✅ 已修复 |
| 8 | 前端 tryOutput 类型混乱 | ✅ 已修复 |
| 9 | _hash_key / _get_agent_or_404 重复定义 | ✅ 提取到 app/utils.py |
| 5 | find_free_port TOCTOU 竞态 | Phase 2 |
| 6 | AgentRegistry 无锁 | Phase 2 |

---

## 总体评价

**整体代码质量：中上。** 架构设计清晰（ROADMAP 对应能力 → 分层实现）、目标准确、代码量克制（约 1500 行有效代码）。作为 Phase 1 的产物，功能和边界定义得很好。

发现的 **2 个功能 bug** 需要立即修复，另有若干代码健壮性和工程实践问题建议优化。

---

## 🔴 功能 Bug（需立即修复）

### 1. **trace context 未传递到子进程 —— Langfuse trace 上下文功能完全失效**

**文件：** `backend/app/api/invoke.py` 第 84-94 行 + `backend/app/runtime/manager.py` 第 89-99 行

**问题：** invoke API 在第 84-90 行精心组装了 trace context（`langfuse_user_id`, `langfuse_session_id`, `langfuse_tags`），但第 94 行调用 `managed.invoke(body.input)` 时**只传了 `input`，没有传 `config`**。更致命的是 `manager.py` 的 `invoke()` 方法第 96 行写死了 `"config": {}`。

```python
# invoke.py:84-94
config: dict = {}
if md_user := body.metadata.get("user_id"):
    config["langfuse_user_id"] = md_user
# ... 组装了但完全没用！

result_dict = await asyncio.to_thread(managed.invoke, body.input)
# 只传 body.input，config 丢失

# manager.py:89-99
r = client.post(
    f"http://127.0.0.1:{self.port}/invoke",
    json={"input": payload, "config": {}},  # config 写死为 {}
)
```

**影响：** 用户传的 `user_id`、`session_id`、`tags` 永远不会到达 Langfuse，trace 页面上看不到用户/会话维度。

**修复建议：** 修改 `ManagedAgent.invoke()` 签名，接收 `config` 参数并转发给子进程。

---

### 2. **Agent 列表/详情页 `active_version` 永远为 `None`**

**文件：** `backend/app/api/agents.py` 第 17-32 行

**问题：** `_with_status()` 函数在 `if managed:` 分支里只有一个 `pass` 语句：

```python
if managed:
    # 从 work_dir 推断 version（registry 没有存 version 字符串，只存了 version_id）
    # 简单做法：查 DB 的 is_active version
    pass  # ← 什么都没做
```

`active_version` 变量初始化为 `None`，之后从未更新，始终返回 `None`。

**影响：** 前端列表页和详情页都显示 `active: —`，用户无法看到当前激活的版本号。

**修复建议：** 查询 `agent_versions` 表中 `agent_id` 匹配且 `is_active=True` 的记录，取 `version` 字段。

---

## 🟡 重要问题（建议近期修复）

### 3. **子进程日志文件句柄泄漏**

**文件：** `backend/app/runtime/process.py` 第 34 行

```python
log_file = open(log_path, "ab")
return subprocess.Popen(..., stdout=log_file, stderr=subprocess.STDOUT, ...)
```

**问题：** `log_file` 是 Python 文件句柄，传入 `subprocess.Popen` 后所有权转移。但 `ManagedAgent` 的 `stop()` 方法只 kill 进程，不 close 句柄。而且如果 process 被多次 start/stop，会重复打开新句柄。

**影响：** 内存泄漏（文件句柄泄漏），长时间运行可能耗尽 fd。

**修复建议：** 1) 在 `kill_process` 后 close `self.process.stdout`；2) 或改用 `stdout=open(log_path, "ab")` 并注册 atexit close。

### 4. **`find_free_port()` TOCTOU 竞态**

**文件：** `backend/app/runtime/process.py` 第 14-22 行

```python
def find_free_port(start=9101, end=9200) -> int:
    for port in range(start, end):
        with socket.socket(...) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port  # 返回后到实际使用前，端口可能被占用
            except OSError:
                continue
```

**问题：** 检查和绑定之间有时间窗，bind 后立即 close 释放端口，到 `uvicorn` 实际 listen 时可能已被占用。

**修复建议：** Phase 2 引入端口重试机制，子进程启动失败时换一个端口重试。

### 5. **VenvBuilder 构建失败无错误信息**

**文件：** `backend/app/runtime/venv.py` 第 36-53 行

```python
subprocess.run(
    ["uv", "venv", str(self.venv_dir)], check=True, capture_output=True
)
```

**问题：** 设置 `capture_output=True` 但未记录 stdout/stderr。构建失败时，`ManagedAgent` 只知道 "venv build failed"，不知道具体原因（pypi 不可达？依赖冲突？）。用户在前端看到的错误信息毫无帮助。

**修复建议：** 捕获 `CalledProcessError`，记录 stderr；在 `last_error` 中包含具体诊断信息。

### 6. **Hot-reload 不安全：AgentRegistry 单例无锁**

**文件：** `backend/app/runtime/registry.py`

```python
class AgentRegistry:
    _instance: "AgentRegistry | None" = None
    _agents: dict[str, ManagedAgent] = {}
```

**问题：** `dict` 的读写没有同步机制。FastAPI 是 async 框架，多个协程可能同时修改 `_agents`。虽然当前规模不至于出事，但随着 agent 数量增加风险变大。

**修复建议：** 使用 `asyncio.Lock` 保护状态变更操作；或改用 `contextvars` / FastAPI `app.state`。

### 7. **前端错误处理使用 `alert()` 而非 UI 组件**

**文件：** `frontend/src/app/agents/[name]/page.tsx` 多处

```tsx
} catch (e) {
  alert((e as Error).message);  // ← 弹出丑陋的原生对话框
}
```

**问题：** `alert()` 阻断用户操作，在移动端和现代 UI 中体验很差。

**修复建议：** 统一使用 toast 组件或内联错误横幅替代。

### 8. **前端 `tryOutput` 状态类型混乱**

**文件：** `frontend/src/app/agents/[name]/page.tsx` 第 133-160 行

```tsx
const [tryOutput, setTryOutput] = useState<string | null>(null);
// ...success:
setTryOutput(JSON.stringify(r.output, null, 2));
// ...error:
setTryOutput(`❌ ${(e as Error).message}`);
```

**问题：** 成功和失败混合在同一个状态变量里，区分方式靠字符串前缀 `❌`。极脆弱。

**修复建议：** 拆成 `{ type: "success", data } | { type: "error", message }` 联合类型；或用两个独立 state。

---

## 🔵 优化建议（可择机处理）

### 架构/代码组织

| # | 建议 | 文件 | 
|---|------|------|
| 9 | `_hash_key()` 在 `keys.py:25` 和 `invoke.py:23` 重复定义，建议抽取到 `app/utils.py` | keys.py, invoke.py |
| 10 | `_get_agent_or_404()` 在 `versions.py:43` 和 `keys.py:34` 重复定义，建议统一放在 agents router 或 utils | versions.py, keys.py |
| 11 | `agents.py` 中 `_get_agent_or_404` 逻辑内联在 delete 方法里，不如复用 | agents.py:73 |
| 12 | `versions.py:95` `import io` 和 `versions.py:108` `import tempfile` 在函数体内，移到文件顶部 | versions.py |
| 13 | `manager.py` 使用 `logging` 而非项目的 `structlog`，日志不统一 | manager.py:6,21 |
| 14 | `config.py:54` `# type: ignore[call-arg]` 注释可以去掉——在 `model_config` 中设 `env_file` 后代码应正常 | config.py |
| 15 | 缺少 `agent_versions.requirements` 字段的填充逻辑——上传 zip 时只校验了 `agent.py` 存在，但没读取 `requirements.txt` 内容存入 DB | versions.py:67 |

### 前端

| # | 建议 | 文件 |
|---|------|------|
| 16 | `api.ts` 没有请求取消支持（AbortController），页面卸载时可能产生竞态 | api.ts |
| 17 | `onDeleteAgent` 使用 `window.location.href` 硬跳转，应用 `router.push()` | detail page:96 |
| 18 | Agent 列表页的 `useEffect` 缺少 `refresh` 依赖（React 严格模式 lint 会报） | agents/page.tsx:47 |
| 19 | 单个 `busy` 状态锁住所有操作——上传失败也会阻塞激活版本 | detail page:39 |
| 20 | Traces 页 `agentName` 未持久化到 URL query param，无法分享筛选链接 | traces/page.tsx |

### 测试

| # | 建议 | 
|---|------|
| 21 | 测试覆盖只有 12 个 case，**Runtime 层完全没有单元测试**（process/venv/storage/manager 均未测试） |
| 22 | 测试创建 agent 后没有 cleanup（依赖唯一 name+UUID 避免冲突），长期运行可能残留数据 |
| 23 | `test_agents.py:55-68` 创建 key 时传了空 body（`ac.post(f"/api/v1/agents/{name}/keys")`），但 Python `Body(default=None)` 可能导致参数为空——确认行为是否正确 |

---

## ✅ 做得好的地方

1. **代码量克制。** 整个项目约 1500 行代码，每个文件职责清晰、长度合理（最长 214 行），没有过度抽象。
2. **安全设计到位。** API Key 只存 sha256 哈希、明文仅返回一次、revoke 软删除——安全实践很好。
3. **架构选择明智。** AgentRegistry 单例 + ManagedAgent dataclass，直白好用；VenBuilder 基于 requirements hash 缓存，避免重复构建。
4. **Schema 分层清晰。** models (SQLAlchemy) ↔ schemas (Pydantic) ↔ API routes 三层分离，无耦合。
5. **API 设计合理。** RESTful 风格、状态码规范（201/204/401/404/409/503）、`Bearer` 鉴权格式标准。
6. **日志配置专业。** structlog JSON 格式，做好了生产环境聚合的准备。
7. **miao_runner 隔离设计。** 用户代码跑在独立子进程+venv 里，与主服务解耦，安全且易扩展。
8. **自写 UI 组件质量好。** shadcn 风格手工实现，cva variant 系统规范，forwardRef 支持到位。

---

## Phase 2 建议优先级

结合 CR 发现的问题和 ROADMAP 中的 Phase 2 计划：

| 优先级 | 事项 | 类型 |
|--------|------|------|
| **P0** | 修复 trace context 不传递 bug | Bug fix |
| **P0** | 修复 active_version 永远为 None bug | Bug fix |
| **P1** | 修复文件句柄泄漏 | Bug fix |
| **P1** | VenvBuilder 错误信息可观测 | 健壮性 |
| **P1** | 子进程崩溃自动重启 | Phase 2 |
| **P1** | lifespan 恢复 is_active agent | Phase 2 |
| **P2** | 前端错误处理改造（alert → toast） | UX |
| **P2** | 代码去重（hash_key, get_agent_or_404） | 工程 |
| **P2** | Runtime 层单元测试 | 工程 |
| **P3** | SSE 流式输出 | Phase 3 |
| **P3** | Docker 沙箱 | Phase 3 |

---

## 总结

Miao AI 是一个架构清晰、代码质量中上的个人项目。在 Phase 1 阶段达到了"能用"的目标。当前有 2 个需要立即修复的功能 bug（trace context 丢失 + active_version 始终为 null），以及若干健壮性问题建议在 Phase 2 中一并处理。整体而言，项目方向和执行质量都不错。

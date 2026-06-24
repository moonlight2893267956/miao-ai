# Docker 沙箱 + Webhook 异步回调 — Code Review

**审查日期**: 2026-06-23
**审查范围**: `runtime/docker.py`, `services/task_worker.py`, `models/invoke_task.py`, `schemas/invoke_task.py`, `api/invoke.py`（async 部分）, `agent_templates/Dockerfile.template`, `manager.py`（docker mode 相关）

---

## P0 — Critical（必须修复）

### 1. Docker 模式 invoke/invoke_stream 检查 `self.process` 导致必然失败

**文件**: `manager.py:247-248, 269-270`

```python
def invoke(self, payload, timeout=60.0, config=None) -> dict:
    if self.status != "running" or not self.process:
        raise RuntimeError(...)
```

Docker 模式下 `self.process` 为 `None`（Docker 用 `self._docker`），所以 `not self.process` 恒为 True。
即使 agent 状态为 "running"，**所有 Docker agent 的 invoke/invoke_stream 调用都会直接抛 RuntimeError**。

`invoke_stream()` 同样有此问题。

**修复**: 改为检查两种运行时：

```python
def _is_running(self) -> bool:
    if self.runtime_mode == "docker":
        return self.status == "running" and self._docker is not None
    return self.status == "running" and self.process is not None

def invoke(self, payload, timeout=60.0, config=None) -> dict:
    if not self._is_running():
        raise RuntimeError(f"agent {self.name} not running (status={self.status})")
```

---

### 2. Dockerfile 将 LANGFUSE_SECRET_KEY 烘焙进镜像层 — 安全泄露

**文件**: `Dockerfile.template:6-8`, `docker.py:94-98`

```dockerfile
ENV LANGFUSE_PUBLIC_KEY={{ langfuse_public_key }}
ENV LANGFUSE_SECRET_KEY={{ langfuse_secret_key }}  ← Secret key 烘进镜像！
ENV LANGFUSE_BASE_URL={{ langfuse_base_url }}
```

`ENV` 指令写入 Docker 镜像层。任何人用 `docker history <image>` 即可看到 secret key。
这违反了容器安全基本原则（secret 不应进入镜像）。

**修复**: 删除 Dockerfile 中的 ENV 行，改为 `docker run` 时通过 `--env-file` 或 `-e` 传入：

```python
# docker.py DockerRunner.start()
def start(self) -> None:
    env_args = []
    for k, v in self.env_vars.items():
        if v:
            env_args.extend(["-e", f"{k}={v}"])
    cmd = [
        "docker", "run", "-d",
        "--name", self.container_name,
        "--cpus", self.cpu,
        "--memory", self.memory,
        "--network", self.network,
        "-p", f"{self.port}:8080",
        *env_args,
        "--restart", "no",
        self.image_tag,
    ]
    ...
```

同时从 `build_dockerfile()` 和模板中移除 env_vars 逻辑。

---

### 3. `COPY . /app/` 把 .venv / source.zip / agent.log 等垃圾全部塞进镜像

**文件**: `Dockerfile.template:3`

```dockerfile
COPY . /app/
```

`work_dir` 中可能有：
- `.venv/`（数百 MB，在容器里完全无用）
- `source.zip`
- `agent.log`
- `.build_hash`
- 刚生成的 `Dockerfile` 自身
- `miao_runner.py`（已单独 COPY，但又被 `COPY .` 再复制一次）

结果：镜像体积膨胀 + 构建慢 + 语义混乱。

**修复**: 添加 `.dockerignore` 或改用精确 COPY：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY miao_runner.py /app/miao_runner.py
COPY agent.py /app/agent.py
COPY requirements.txt* /app/
RUN pip install --no-cache-dir fastapi uvicorn pydantic langfuse
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
EXPOSE 8080
CMD ["python", "/app/miao_runner.py", "/app", "agent:invoke", "8080"]
```

并在 `build_dockerfile()` 前生成 `.dockerignore`：

```
.venv/
*.zip
*.log
.build_hash
Dockerfile
```

---

## P1 — Important（应该修复）

### 4. `DockerBuilder.image_exists()` 缺内容哈希检查 — 可能用旧镜像

**文件**: `docker.py:60-68, manager.py:103-104`

```python
def image_exists(self) -> bool:
    ...
    return bool(result.stdout.strip())

# manager.py
if not builder.image_exists():
    builder.build()
```

`image_tag = f"miao-agent:{self.name}-{self.version_id[:8]}"` — 标签只取 version_id 前 8 字符。
如果同 version_id 的代码被多次上传（同一 version_id 但 zip 内容变了），旧镜像的 tag 匹配，`image_exists()` 返回 True → **跳过构建，用旧镜像运行新代码**。

**修复方案 A**: tag 加入 content hash（如 zip 文件的 MD5）。
**修复方案 B**: 每次激活时强制 `docker build --no-cache`，不检查 image_exists（简单粗暴但安全）。
**推荐方案 B**（agent 数量少，构建成本可控）。

---

### 5. Docker 容器名冲突 — 旧容器未清理时 `docker run` 必败

**文件**: `manager.py:90`, `docker.py:93-110`

```python
container_name = f"miao-{self.name}"
```

容器名是确定性的。如果旧容器未正常停止（进程崩溃、`stop()` 被跳过），同名 `docker run` 会报 `"The container name ... is already in use"`。

**修复**: `start()` 前先强制清理同名容器：

```python
def start(self) -> None:
    # 先清理同名残留容器
    subprocess.run(["docker", "rm", "-f", self.container_name],
                   capture_output=True, timeout=10)
    result = subprocess.run(
        ["docker", "run", "-d", "--name", self.container_name, ...],
        capture_output=True, text=True, timeout=30,
    )
    ...
```

---

### 6. `try_restart()` 不支持 Docker 模式 — Watchdog 无法重启 Docker agent

**文件**: `manager.py:193-210`

```python
def try_restart(self) -> bool:
    ...
    ok = self._start_process()  # 只处理 venv 模式！
```

Watchdog 检测到 Docker agent 崩溃后调用 `try_restart()`，但 `_start_process()` 只创建子进程，不处理 Docker。Docker agent 崩溃后 watchdog 会反复调用 `try_restart()` → `_start_process()` → 每次失败 → 到 `max_restarts` 后放弃。

**修复**: `try_restart()` 按 runtime_mode 分发：

```python
def try_restart(self) -> bool:
    ...
    self.restart_count += 1
    if self.runtime_mode == "docker":
        ok = self._build_and_start_docker()
    else:
        ok = self._start_process()
    if ok:
        self.restart_count = 0
    return ok
```

---

### 7. `invoke_async` 中 agent_id 查询冗余、混乱、含死变量

**文件**: `invoke.py:189-195`

```python
agent_row = _auth_result = await session.execute(select(Agent).where(Agent.name == name))
agent_id = agent_row.scalar_one_or_none()
if not agent_id:
    agent_id = (await get_agent_or_404(name, session)).id  # 此路径不可达
else:
    agent_id = agent_id.id
```

问题：
1. `_auth_result` 被赋值但从未使用 — 死变量
2. fallback 分支调用 `get_agent_or_404()`，如果 agent 不存在则 404。但 `_auth_agent()` 已经验证了 agent 存在，所以 fallback 不可达
3. 代码意图不清晰，混用 `Agent` ORM 对象和 `agent.id`

**修复**: 简化为一行：

```python
agent_id = (await get_agent_or_404(name, session)).id
```

---

### 8. `get_async_task_status` 不验证任务归属 — 信息泄露

**文件**: `invoke.py:224-245`

```python
@router.get("/invoke/async/{request_id}")
async def get_async_task_status(name: str, request_id: str, session=...):
    result = await session.execute(
        select(InvokeTask).where(InvokeTask.request_id == request_id)
    )
```

URL path 含 `name` 但查询只按 `request_id` 过滤，不检查 `agent_name == name`。
任何人知道 `request_id` 就能查询任意 agent 的任务结果（含 output/error）。

**修复**: 加条件过滤 + 鉴权：

```python
select(InvokeTask).where(
    InvokeTask.request_id == request_id,
    InvokeTask.agent_name == name,
)
```

同时考虑加 auth（至少检查 API key）。

---

### 9. `InvokeTask.created_at` 使用 `datetime.utcnow()` — 已弃用 + 时区不一致

**文件**: `models/invoke_task.py:41-42`

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), default=lambda: datetime.utcnow(), nullable=False
)
```

`datetime.utcnow()` 在 Python 3.12+ 已弃用，返回 **naive datetime**（无时区信息）。
但列定义是 `DateTime(timezone=True)`（PG 存 timestamptz）。
migration 用 `server_default=sa.text('now()')`（PG 生成 aware datetime）。
Python default 和 PG default 产生不一致的时区行为。

**修复**:

```python
from datetime import datetime, timezone

created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
    nullable=False,
)
```

---

### 10. `TaskWorker._execute_task` 在线程中创建 event loop — `set_event_loop` 不必要

**文件**: `task_worker.py:59-61`

```python
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)  # 不必要
```

`asyncio.set_event_loop()` 在线程池中被调用。ThreadPoolExecutor 复用线程，虽然每次创建新 loop 后 `loop.close()` 了，但 `set_event_loop` 在已关闭的 loop 上是无效的，且该调用在 Python 3.12+ 中已弃用。

由于我们直接用 `loop.run_until_complete(_run())`，不需要 set_event_loop。

**修复**: 删除 `asyncio.set_event_loop(loop)` 行。

---

### 11. Webhook 在 DB commit 之后发送 — 状态不一致窗口

**文件**: `task_worker.py:82-102`

```python
# 先 commit DB
await session.execute(update(InvokeTask).values(status="success", ...))
await session.commit()

# 再发 webhook
self._post_webhook(...)
```

DB 先标记 "success"，webhook 再发送。如果 webhook 失败（网络问题），用户通过 `GET /invoke/async/{id}` 看到 "success"，但从未收到 webhook 通知。

**修复方案 A**: 先发 webhook，再更新 DB（webhook 失败则 DB 保持 "running"）。
**修复方案 B**: 加 `webhook_delivered` 字段，记录 webhook 送达状态（更健壮）。

推荐 **方案 B** — 不改变 commit 顺序，但增加字段让用户知道 webhook 是否送达。

---

### 12. `retry_count / max_retries / next_retry_at` DB 字段从未使用 — 死 schema

**文件**: `models/invoke_task.py:37-39`, `migration:a3c5e7f90123`

```python
retry_count: Mapped[int] = mapped_column(Integer, default=0)
max_retries: Mapped[int] = mapped_column(Integer, default=3)
next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

这三个字段是为 "invoke 失败后整个任务重试" 设计的，但当前实现只在 webhook 层面做重试（`_post_webhook` 的指数退避），**不在 DB 层面做任务重试**。

这些字段：
- `retry_count` 从不更新（永远 = 0）
- `next_retry_at` 从不设置（永远 = NULL）
- `max_retries` 只在创建时写入 `settings.webhook_max_retries`，但 webhook 重试逻辑不读它

**修复**: 要么实现 DB 层面的任务重试逻辑，要么删除这三个字段 + 修改 migration。目前建议先删除，避免未来开发者误以为有任务重试功能。

---

### 13. `DockerRunner.stop()` 吞掉所有异常 — 包括 docker daemon 故障

**文件**: `docker.py:112-122`

```python
def stop(self) -> None:
    for cmd in [...]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
        except Exception:
            pass  # 吞掉所有异常
```

`docker stop` 和 `docker rm` 的异常全被吞掉。如果 Docker daemon 挂了、权限不足、或容器名错误，日志里完全看不到。至少应记录异常信息。

**修复**:

```python
def stop(self) -> None:
    for cmd in [...]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
        except Exception as e:
            log.warning("docker.stop.cmd_failed cmd=%s error=%s", cmd, e)
```

---

## P2 — Minor（可选修复）

### 14. `_running` dict 在多线程间读写无显式同步

**文件**: `task_worker.py:23, 42, 127`

`submit()` 在主线程写入 `_running[task_id] = future`，`_execute_task` 在工作线程调用 `self._running.pop(task_id, None)`。
Python GIL 保证 dict 单操作线程安全，但缺少文档说明，且 `shutdown()` 的 `len(self._running)` 可能读到不一致快照。

**建议**: 用 `threading.Lock` 保护或改用 `concurrent.futures` 的内置回调机制。不紧急，但应加注释说明 GIL 依赖。

---

### 15. `request_id` 无碰撞检测

**文件**: `invoke.py:185`

```python
request_id = f"miao_req_{_uuid.uuid4().hex[:12]}"
```

只取 UUID 的 12 个 hex 字符（48 bit）。碰撞概率极低（~2^48 = 281T），但如果碰撞，DB unique constraint 会抛 `IntegrityError` → 500 错误。

**建议**: 用完整 UUID 或加碰撞重试逻辑。碰撞概率极低，优先级不高。

---

### 16. `status_url` 是相对路径

**文件**: `invoke.py:220`

```python
status_url=f"/api/v1/agents/{name}/invoke/async/{request_id}"
```

客户端需拼接 base URL。REST API 中相对路径是常见做法，但如果前端跨域部署，可能需要完整 URL。

**建议**: 低优先级，可在 `InvokeAsyncResponse` 中加 `base_url` 配置项。

---

### 17. Dockerfile 端口 8080 硬编码

**文件**: `Dockerfile.template:9-10`, `docker.py:102`

```dockerfile
EXPOSE 8080
CMD ["python", "/app/miao_runner.py", "/app", "agent:invoke", "8080"]
```

`-p {self.port}:8080` 假设容器内监听 8080。但 miao_runner 的 PORT 从 sys.argv 获取，Dockerfile CMD 里硬编码了 8080。两者一致，但耦合隐式。

**建议**: CMD 里的端口应该可配置，或至少有注释说明 8080 是约定端口。

---

### 18. `docker_available()` 每次调用执行 `docker info` — 较重

**文件**: `docker.py:18-29`

`docker info` 输出大量信息（~20KB），耗时 1-3s。每次 `_build_and_start_docker()` 都调用一次。

**建议**: 缓存结果（startup 时检查一次），或在 `DockerRunner.start()` 中依赖 docker CLI 的自然报错。

---

## 总评

| 类别 | 数量 | 主要风险 |
|------|------|----------|
| P0 Critical | 3 | Docker invoke 必崩；密钥泄露进镜像；垃圾文件入镜像 |
| P1 Important | 10 | 旧镜像复用；容器名冲突；restart 不支持 docker；auth 缺失；utcnow 弃用；webhook 状态窗口；死 schema 等 |
| P2 Minor | 5 | dict 线程安全注释；request_id 碰撞；相对 URL；端口硬编码；docker info 性能 |

**最严重问题**: P0#1（Docker invoke/invoke_stream 必崩）和 P0#2（密钥进镜像）。这两个必须在上线前修复，否则 Docker 模式完全不可用且存在安全漏洞。

**架构好评**:
- Docker 运行时和 venv 运行时共享同一 invoke API 端点 — 用户体验一致
- TaskWorker 的线程池 + 新 event loop 方案是处理 sync invoke 的合理选择
- webhook 指数退避重试逻辑清晰
- 令牌桶限流在线程池中执行，加了 threading.Lock — 正确

**架构建议**:
- Docker 模式和 venv 模式的 `invoke()` 应统一为"向 localhost:port 发 HTTP"的抽象，不依赖 `self.process` 或 `self._docker` 的具体类型。两者都是"某个端口在服务"，区别只在创建/销毁方式
- `InvokeTask` 的 dead schema（retry_count 等）应尽早清理，否则越拖越难改 migration
- `TaskWorker` 应考虑用 `asyncio.to_thread` + 在主事件循环做 DB 更新，而不是在 worker thread 里另起 event loop。这样 DB session 管理、异常处理都更自然

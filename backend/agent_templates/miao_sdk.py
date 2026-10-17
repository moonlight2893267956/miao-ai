"""Miao Agent SDK —— 执行事件上报（平台通用能力）。

定位
----
把 agent 执行过程中发生的事**原样**抛给上游：模型在想什么、调了哪些工具、
入参出参是什么、耗时多久、成功还是失败。平台**只做管道**，不解释、不裁剪语义 ——
怎么展示、要不要展示、怎么分组，是上游自己的事。

这一点和 coding agent（Claude Code / Cursor 那一类）一致：用户能看到「思考 →
调用某工具 → 拿到结果 → 再思考」，而不是只看到一个最终答案。

--------------------------------------------------------------------------------
快速接入
--------------------------------------------------------------------------------

    import miao_sdk

    def invoke(input, config=None):
        if (config or {}).get("stream"):
            return miao_sdk.stream(lambda: _dispatch(input, config))
        return _dispatch(input, config)

    def _dispatch(input, config):
        miao_sdk.thinking("先查内置码表，再决定要不要联网。")

        with miao_sdk.tool("tables.lookup", char=input["char"]) as t:
            row = tables.lookup_code(input["char"])
            t.output({"code": row["code"]})       # 出参：交给上游结构化查看

        try:
            data = tavily.search(input["char"])
        except Exception as exc:
            miao_sdk.log("联网失败，按离线路径继续", level="warn")
        return {"code": ...}

SDK 只是**语法糖**：真正发送的是 ``{"exec": {...}}`` 块。想发 SDK 没封装的事件，
直接 ``miao_sdk.emit({"kind": "任何你自己的类型", ...})`` —— 平台不校验 ``kind``。

--------------------------------------------------------------------------------
事件信封（跨层契约，平台只依赖 ``kind`` 这一个字段做归类，其余原样透传）
--------------------------------------------------------------------------------

```json
{"kind": "thinking",    "text": "先查内置码表…"}
{"kind": "tool_call",   "call_id": "c1", "name": "tavily.search", "input": {"query": "编"}}
{"kind": "tool_result", "call_id": "c1", "name": "tavily.search",
                        "ok": true, "output": {"code": "xyna"}, "ms": 4271}
{"kind": "log",         "level": "warn", "text": "未配置 TAVILY_API_KEY，走离线路径"}
```

| 字段 | 出现在 | 说明 |
|---|---|---|
| ``kind`` | 全部 | 事件类型。**由 agent 决定**，平台只用它归类，不校验取值 |
| ``seq`` | 全部 | SDK 自动加的自增序号（同一流内单调），上游可据此发现缺口 |
| ``text`` | thinking / log | 文本内容 |
| ``level`` | log | info / warn / error（自由取值） |
| ``call_id`` | tool_call / tool_result | **配对键**：一次调用的 call 与 result 用它关联 |
| ``name`` | tool_call / tool_result | 工具名（自由取值，建议 ``域.动作``） |
| ``input`` | tool_call | 工具入参（任意 JSON） |
| ``ok`` | tool_result | 是否成功 |
| ``output`` / ``error`` | tool_result | 出参 / 失败原因 |
| ``ms`` | tool_result | 耗时（毫秒） |

**语义边界（重要）**：``thinking`` 是**模型内容**，可能有错、可能不完整；
``tool_call`` / ``tool_result`` 是**执行事实**，由代码产生。上游应把两者分开呈现
（我们自己的 Try Run 就是这么做的），不要把 thinking 当事实引用。

--------------------------------------------------------------------------------
纪律（SDK 已内建）
--------------------------------------------------------------------------------

1. **永不抛异常**：上报失败绝不影响主流程。
2. **没有 sink 时零开销**：非流式路径不注册 sink，所有上报立刻返回。
3. **线程隔离**：sink 存 ``threading.local()``，同一 agent 的并发 invoke 互不串味。
4. **体积上限**：单条事件的文本 / JSON 都会被截断（见 ``_CAP_*``），并标记
   ``truncated: true`` —— 这是传输保护，不是语义裁剪；上游仍能拿到完整结果。
5. **事件是辅助信息**：上游丢弃全部事件也必须能正常拿到最终结构化结果。

``stream()`` 负责**把同步流水线放到后台线程**：``_dispatch`` 里的模型与网络调用都是
阻塞的，只有挪到后台，主线程才能在阻塞**进行中**把事件交出去 —— 否则即使返回
generator，也只能等算完才开始输出。这是「等待期看得见」的唯一实现方式。
"""
from __future__ import annotations

import json
import queue
import threading
from contextlib import contextmanager

__all__ = [
    "EVENT_KINDS",
    "CHUNK_KEY",
    "emit",
    "thinking",
    "log",
    "tool",
    "tool_call",
    "tool_result",
    "new_call_id",
    "set_sink",
    "get_sink",
    "clear",
    "use_sink",
    "stream",
    "exec_chunk",
    "token_chunk",
    "output_chunk",
]

#: 推荐事件类型（**只是推荐**，agent 可自定义任意 kind；平台不校验）
EVENT_KINDS = ("thinking", "tool_call", "tool_result", "log")

#: 执行事件块在流式 chunk 里的键名（与 token / output 并列）
CHUNK_KEY = "exec"

#: 单次流内最多保留的事件条数（超出后不再缓存，但**仍然照常转发**）
_TRACE_LIMIT = 200

# ── 体积上限：SSE 是一行一条，而且上游可能把它们全存下来 ──
_CAP_TEXT = 2_000          # thinking / log 正文
_CAP_JSON = 4_000          # input / output 序列化后的字节数
_CAP_ERROR = 500           # 失败原因
_CAP_NAME = 120

_local = threading.local()
_SEQ_LOCK = threading.Lock()


def set_sink(fn) -> None:
    """在当前线程注册事件回调；传 ``None`` 表示关闭（等价于 :func:`clear`）。"""
    _local.sink = fn


def get_sink():
    """取当前线程的 sink；未注册返回 ``None``。"""
    return getattr(_local, "sink", None)


def clear() -> None:
    """关闭当前线程的上报（非流式路径 / 请求收尾时调用）。

    同时重置 ``seq`` 与 ``call_id`` 计数器：两者都是**每条流内**的语义。
    不重置的话，进程跑得越久 ``call_id`` 越大，上游就别想按 id 对齐或复现问题。
    """
    _local.sink = None
    _local.seq = 0
    _local.call_seq = 0


@contextmanager
def use_sink(fn):
    """临时在当前线程注册 sink，退出时**恢复**原值（异常路径也恢复）。"""
    previous = get_sink()
    set_sink(fn)
    try:
        yield
    finally:
        set_sink(previous)


def new_call_id() -> str:
    """生成一个调用 id（``c1`` / ``c2`` …，当前线程内自增）。

    ``tool()`` 会自动分配；只有手工配 ``tool_call`` / ``tool_result`` 时才需要它。
    """
    with _SEQ_LOCK:
        n = getattr(_local, "call_seq", 0) + 1
        _local.call_seq = n
    return "c%d" % n


def _next_seq() -> int:
    with _SEQ_LOCK:
        n = getattr(_local, "seq", 0) + 1
        _local.seq = n
    return n


def _clip_text(value, limit: int):
    """把任意值收敛成受长度约束的字符串；返回 ``(text, truncated)``。"""
    if value is None:
        return "", False
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text, False
    return text[: limit - 1] + "…", True


def _clip_json(value, limit: int):
    """把任意值收敛成可 JSON 序列化且受体积约束的值。

    超限时**不丢结构语义**：换成一个带 ``preview`` 的标记对象，并用 ``truncated``
    告知上游「你看到的不是全部」—— 悄悄截断比截断更危险。
    """
    if value is None:
        return None, False
    try:
        dumped = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text, truncated = _clip_text(value, limit)
        return {"preview": text}, True or truncated
    if len(dumped) <= limit:
        return value, False
    return {"preview": dumped[: limit - 16], "omitted_bytes": len(dumped) - limit}, True


def _send(event: dict) -> None:
    """把已构造好的事件交给 sink。永不抛异常。"""
    sink = get_sink()
    if sink is None:
        return
    try:
        event["seq"] = _next_seq()
        sink(event)
    except Exception:  # noqa: BLE001 —— 上报失败绝不能拖垮主流程
        pass


def emit(event: dict) -> None:
    """上报一条**自定义**执行事件（逃生通道）。

    平台不校验 ``kind``：只要是个 dict 就会原样透传给上游。字段约定见模块文档；
    ``seq`` 由 SDK 补。这是「平台不定义语义」的保证 —— SDK 提供的
    :func:`thinking` / :func:`tool` / :func:`log` 只是常见形态的语法糖。
    """
    if get_sink() is None or not isinstance(event, dict):
        return
    try:
        payload = dict(event)
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind:
            return  # 没有 kind 无法归类，丢掉比让上游猜更诚实
        payload["kind"] = _clip_text(kind, 40)[0]
        # 文本字段统一瘦身
        for key in ("text", "detail", "message"):
            if key in payload:
                text, truncated = _clip_text(payload[key], _CAP_TEXT)
                payload[key] = text
                if truncated:
                    payload["truncated"] = True
        if "name" in payload:
            payload["name"] = _clip_text(payload["name"], _CAP_NAME)[0]
        for key in ("input", "output"):
            if key in payload:
                value, truncated = _clip_json(payload[key], _CAP_JSON)
                payload[key] = value
                if truncated:
                    payload["truncated"] = True
        if "error" in payload and payload["error"] is not None:
            text, truncated = _clip_text(payload["error"], _CAP_ERROR)
            payload["error"] = text
            if truncated:
                payload["truncated"] = True
        _send(payload)
    except Exception:  # noqa: BLE001
        pass


def thinking(text) -> None:
    """上报一段**模型思考**（或 agent 的推理草稿）。

    ⚠️ 这是模型内容：可能有错、可能不完整。上游应把它与执行事实分开呈现。
    没有思考内容可报（例如模型不返回 reasoning）时**不要编** —— 不调用即可。
    """
    emit({"kind": "thinking", "text": text})


def log(text, level="info") -> None:
    """上报一条执行日志（人话说明、降级、跳过、重试原因等）。

    ``level`` 自由取值（``info`` / ``warn`` / ``error``），平台不校验。
    """
    emit({"kind": "log", "level": level, "text": text})


def tool_call(name, input=None, call_id=None) -> str:
    """上报「开始调用某工具」，返回 ``call_id``（配对用）。

    一般不需要直接调用 —— 用 :func:`tool` 上下文管理器，它会在退出时自动配对
    ``tool_result`` 并计时。
    """
    cid = call_id or new_call_id()
    emit({"kind": "tool_call", "call_id": cid, "name": name, "input": input})
    return cid


def tool_result(call_id, name, *, ok=True, output=None, error=None, ms=None) -> None:
    """上报「工具调用结束」。``call_id`` 必须与该次 ``tool_call`` 一致。"""
    event = {"kind": "tool_result", "call_id": call_id, "name": name, "ok": bool(ok)}
    if output is not None:
        event["output"] = output
    if error is not None:
        event["error"] = error
    if ms is not None:
        event["ms"] = int(ms)
    emit(event)


class _ToolCall:
    """``with miao_sdk.tool(...)`` 拿到的句柄：用它给出参 / 标失败。"""

    __slots__ = ("name", "call_id", "_started", "_output", "_ok", "_error")

    def __init__(self, name: str, call_id: str, started: float):
        self.name = name
        self.call_id = call_id
        self._started = started
        self._output = None
        self._ok = True
        self._error = None

    def output(self, value) -> None:
        """记录工具出参（交给上游结构化查看）。"""
        self._output = value

    def fail(self, error) -> None:
        """标记本次调用失败（但**不**抛异常；是否继续由调用方决定）。"""
        self._ok = False
        self._error = error

    def _finish(self, exc: BaseException | None) -> None:
        import time

        ok = self._ok and exc is None
        error = self._error
        if exc is not None:
            error = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        tool_result(self.call_id, self.name, ok=ok, output=self._output,
                    error=error, ms=(time.monotonic() - self._started) * 1000)


@contextmanager
def tool(name: str, input=None, **kwargs):
    """工具调用上下文管理器：进入时发 ``tool_call``，退出时发配对的 ``tool_result``。

    自动做三件容易漏的事：**分配 call_id**、**计时**、**异常也成对收尾**
    （异常会先记成 ``ok: false`` 的 result，然后**原样抛出** —— SDK 不吞业务异常，
    是否降级由 agent 自己决定）。

        with miao_sdk.tool("tavily.search", query="编 五笔编码") as t:
            data = tavily.search("编")
            t.output({"results": len(data)})

    :param input: 工具入参（任意 JSON）；也可用 ``**kwargs`` 直接写
    """
    import time

    payload = kwargs if kwargs else (input if isinstance(input, dict) else None)
    started = time.monotonic()
    cid = tool_call(name, payload)
    handle = _ToolCall(name, cid, started)
    try:
        yield handle
    except BaseException as exc:
        handle._finish(exc)   # 先如实记失败
        raise                 # 再原样抛出：SDK 不改变 agent 的错误语义
    else:
        handle._finish(None)


# ─── chunk 构造器（给需要自己拼流的 agent / 测试用） ───────────────────────


def exec_chunk(event: dict) -> dict:
    """把一条执行事件包成流式 chunk：``{"exec": {...}}``。"""
    return {CHUNK_KEY: event}


def token_chunk(text: str) -> dict:
    """正文分片 chunk：``{"token": "..."}``（前端可做打字机）。"""
    return {"token": text}


def output_chunk(result: dict) -> dict:
    """收尾块：``{"output": {...}}``（runner 不当 token，随 ``event: done`` 下发）。"""
    return {"output": result}


def stream(work, *, render=None, fallback=None):
    """把同步流水线 ``work`` 放到后台线程跑，边跑边吐事件，最后吐结构化结果。

    产出顺序：

        {"exec": ...} × N       ← work 内部的上报，**实时**
        {"token": ...} × N      ← ``render(result)`` 的每一段（可选，打字机）
        {"output": result}      ← 收尾块，永远最后一个

    :param work: 无参可调用，返回 dict。返回非 dict 时包成 ``{"result": ...}``。
    :param render: 可选，``render(result) -> iterable[str]``，产出正文分片。
        ``render`` 抛异常只丢打字机效果，不影响最终结果。
    :param fallback: 可选，``fallback(exc) -> dict``，``work`` 抛异常时用它造结果。
    """
    events: "queue.Queue[dict]" = queue.Queue()

    def _worker() -> None:
        # sink 必须在工作线程内注册：threading.local 不跨线程继承
        with use_sink(lambda event: events.put_nowait(exec_chunk(event))):
            try:
                result = work()
            except BaseException as exc:  # noqa: BLE001
                result = (
                    fallback(exc)
                    if fallback is not None
                    else {"error": "内部错误：%s: %s" % (type(exc).__name__, str(exc)[:160])}
                )
            finally:
                # finally 保证收尾块一定到：主线程不会挂死在 q.get() 上
                _put = events.put_nowait
                _put(output_chunk(result if isinstance(result, dict) else {"result": result}))

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception as exc:  # noqa: BLE001 —— 线程起不来也要给出可解释的返回
        message = "无法启动执行线程：%s: %s" % (type(exc).__name__, str(exc)[:120])
        yield output_chunk({"error": message})
        return

    while True:
        item = events.get()
        if CHUNK_KEY in item:
            yield item
            continue

        result = item["output"]
        if render is not None:
            try:
                for piece in render(result):
                    if piece:
                        yield token_chunk(piece)
            except Exception:  # noqa: BLE001 —— 打字机失败不影响结果
                pass
        yield item
        return

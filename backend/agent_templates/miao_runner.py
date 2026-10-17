"""
Miao Runner - 每个 agent 子进程实际跑的 FastAPI app。

启动方式：python miao_runner.py <agent_dir> <entrypoint> <port>

每次 /invoke 自动用 Langfuse 上报整体 trace（input/output/latency/tags）。
子进程从父进程继承环境变量（LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL）。

Phase 3: 支持 /invoke/stream SSE 流式输出。
Phase 4: 支持**执行事件上报**（`event: exec`）—— 平台通用能力，任何 agent 可用。

流式约定（agent 侧，任何 agent 都适用）
----------------------------------------
  * agent 的 `invoke(input, config)` 在 `config["stream"]` 为真时**返回 generator**；
  * 正文逐段 `yield {"token": "..."}` → runner 转成 `event: token`（前端据此做打字机）；
  * 结构化结果可选地在最后 `yield {"output": {...}}` → runner **不当 token 转发**，
    而是随 `event: done` 下发，平台据此记录本轮 output
    （不这么做时，流式轮次在会话历史里没有 output，下一轮追问会失去依据）；
  * 返回普通 dict 的 agent → 单个 `event: output` + `event: done`，没有打字机效果。

执行事件上报（agent 侧用法见同目录的 `miao_sdk.py`，即 agent SDK）
-------------------------------------------------------------------
  * agent 上报 `{"exec": {"kind": ..., ...}}` → runner 转成 `event: exec`；
  * **平台只做管道，不解释语义**：事件里可以有思考（`thinking`）、工具调用
    （`tool_call` / `tool_result`，按 `call_id` 配对）、日志（`log`），
    也可以是 agent 自定义的任何 `kind` —— runner 原样透传，怎么展示是上游的事；
  * 执行事件**不当正文、也不计入 done.output**：上游丢弃全部事件，也必须能正常
    拿到最终结构化结果；
  * 老 agent 只 yield `{"token": ...}` → 行为逐字节不变（纯加法约定）；
  * 判断顺序固定为 **收尾块 → 事件块 → 正文块**（含 token/output 的块优先级更高）。
"""
import asyncio
import importlib.util
import inspect
import json
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if len(sys.argv) != 4:
    print("Usage: python miao_runner.py <agent_dir> <entrypoint> <port>", file=sys.stderr)
    sys.exit(1)

AGENT_DIR = Path(sys.argv[1]).resolve()
ENTRYPOINT = sys.argv[2]
PORT = int(sys.argv[3])

agent_file = AGENT_DIR / "agent.py"
if not agent_file.exists():
    print(f"agent.py not found in {AGENT_DIR}", file=sys.stderr)
    sys.exit(2)

spec = importlib.util.spec_from_file_location("user_agent", agent_file)
if spec is None or spec.loader is None:
    print(f"Failed to load spec for {agent_file}", file=sys.stderr)
    sys.exit(3)

user_module = importlib.util.module_from_spec(spec)
sys.modules["user_agent"] = user_module
spec.loader.exec_module(user_module)

module_name, func_name = ENTRYPOINT.split(":")
invoke_fn = getattr(user_module, func_name, None)
if invoke_fn is None:
    print(f"Entrypoint {ENTRYPOINT} not found in {agent_file}", file=sys.stderr)
    sys.exit(4)

# Langfuse 初始化（从环境变量读凭证）
from langfuse import get_client  # noqa: E402

langfuse = get_client()

# Agent SDK（执行过程上报）：与 runner 同目录，脚本模式下 sys.path[0] 就是这里，
# 因此任何 agent 都能 `import miao_sdk`，不需要打进自己的 zip。
# 用软导入：SDK 文件缺失（旧镜像 / 手工部署漏拷）时 runner 必须照常启动 ——
# 进度是锦上添花，绝不能因为少一个可选文件让整个 agent 起不来。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import miao_sdk  # noqa: E402
except Exception as _sdk_exc:  # noqa: BLE001
    miao_sdk = None
    print(f"[miao_runner] miao_sdk SDK 不可用，执行事件上报将静默降级：{_sdk_exc}",
          file=sys.stderr)

app = FastAPI(title=f"Miao Agent ({AGENT_DIR.name})")


def _reset_sdk_sink() -> None:
    """每个请求开始时清掉上一轮残留的 sink。

    agent 若自己管线程却忘了在收尾清理 sink，残留会让「下一轮」的事件写进
    **已经不存在的队列**（表现为执行事件凭空消失，且极难排查）。这里由 runner 兜底，
    对手写的 agent 同样生效；同时重置 seq / call_id 计数器，让每条流都从 1 开始。
    """
    if miao_sdk is not None:
        try:
            miao_sdk.clear()
        except Exception:  # noqa: BLE001
            pass


class _EventRelay:
    """把 SDK 的 sink 桥接成「可被生成器取出的事件队列」。

    为什么要在 runner 侧兜住：SDK 的 ``stream()`` 会把 ``_dispatch`` 放到后台线程
    并自己接管 sink，那条路径不需要这里。但 agent 也可能**自己手写 generator**：

        def invoke(...):
            def gen():
                yield miao_sdk.exec_chunk(...)      # ← 手写块，直接进流
                with miao_sdk.tool("x"): ...        # ← 走 sink
                yield {"output": result}

    手写 generator 里 ``tool()`` 上报的事件只会进 sink。若 runner 不接住，
    这些事件就静默丢掉了 —— 现象是「事件随机少几条」，极难排查。
    所以这里统一注册一个 sink，由 runner 在每次 yield 之间把积压事件**插进流里**，
    保证「agent 怎么写都能出来」。
    """

    def __init__(self) -> None:
        self._queue: "list[dict]" = []
        self._lock = threading.Lock()

    def sink(self, event: dict) -> None:
        with self._lock:
            self._queue.append(event)

    def attach(self) -> None:
        if miao_sdk is not None:
            try:
                miao_sdk.set_sink(self.sink)
            except Exception:  # noqa: BLE001
                pass

    def drain(self) -> "list[dict]":
        with self._lock:
            pending, self._queue = self._queue, []
        return pending


class InvokeRequest(BaseModel):
    input: dict
    config: dict = {}


class InvokeResponse(BaseModel):
    output: dict
    trace_id: str | None = None


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest) -> InvokeResponse:
    user_id = req.config.get("langfuse_user_id")
    session_id = req.config.get("langfuse_session_id")
    tags = req.config.get("langfuse_tags", [])

    # 非流式路径：进度上报在这里是空操作（没有 sink）。清一次保证上一轮残留不影响本轮。
    _reset_sdk_sink()

    started = time.time()
    with langfuse.start_as_current_observation(
        as_type="span",
        name="agent:invoke",
        input={"input": req.input, "config": req.config},
    ) as span:
        if user_id:
            span.update(user_id=user_id)
        if session_id:
            span.update(session_id=session_id)
        if tags:
            span.update(tags=tags + ["miao-agent", f"agent:{AGENT_DIR.name}"])
        else:
            span.update(tags=["miao-agent", f"agent:{AGENT_DIR.name}"])

        try:
            output = invoke_fn(req.input, req.config)
        except Exception as e:
            span.update(
                status_message=str(e),
                level="ERROR",
                output={"error": str(e)},
            )
            raise HTTPException(status_code=500, detail=f"agent error: {e}")

        elapsed_ms = int((time.time() - started) * 1000)
        span.update(output=output, metadata={"latency_ms": elapsed_ms})
        trace_id = langfuse.get_current_trace_id()
    return InvokeResponse(output=output, trace_id=trace_id)


def _sse_event(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _is_final_output_chunk(chunk) -> bool:
    """流式收尾块约定：agent 最后一个 chunk 用 ``{"output": {...}}`` 给结构化结果。

    收尾块**不当 token 转发**（它不是正文），而是放进 ``done`` 事件，让平台能记录
    这一轮的 output —— 否则流式轮次在会话历史里就没有 output，下一轮追问会失去依据。
    约定是纯新增的：既有 agent 只 yield ``{"token": ...}``，不受影响。
    """
    return (
        isinstance(chunk, dict)
        and "token" not in chunk
        and isinstance(chunk.get("output"), dict)
    )


def _is_exec_chunk(chunk) -> bool:
    """执行事件块约定：``{"exec": {...}}`` → ``event: exec``。

    平台通用能力，任何 agent 都能用（SDK 见同目录 ``miao_sdk.py``）。
    纯加法：老 agent 只 yield ``{"token": ...}``，不含 ``exec`` 键，行为一字不变。
    ``token`` / ``output`` 优先级更高，先判它们（判断顺序：收尾块 → 事件块 → 正文块）。

    **平台不解释事件语义**：``exec`` 里可以是思考、工具调用、日志或 agent 自定义的
    任何结构 —— runner 只负责把它原样交给上游。这里只校验「是个 dict」。
    """
    return (
        isinstance(chunk, dict)
        and "token" not in chunk
        and "output" not in chunk
        and isinstance(chunk.get("exec"), dict)
    )


#: 单次调用最多记进 span metadata 的事件条数（超出的只转发、不记档，
#: 避免长流程把 trace 撑大）
_EXEC_TRACE_LIMIT = 200


@app.post("/invoke/stream")
async def invoke_stream(req: InvokeRequest):
    """流式 invoke，返回 SSE (text/event-stream)。

    如果 agent 函数返回 generator/async generator，逐 chunk 转发；
    否则返回单条 done 事件。
    """
    user_id = req.config.get("langfuse_user_id")
    session_id = req.config.get("langfuse_session_id")
    tags = req.config.get("langfuse_tags", [])

    # 每轮开始先清 sink：防上一轮残留把本轮的进度写进已废弃的队列
    _reset_sdk_sink()

    async def generate():
        started = time.time()
        output_chunks = []
        final_output = None  # 收尾块 {"output": {...}}：结构化结果，随 done 事件下发
        exec_events = []  # 执行事件：只转发 + 记档，不进 output
        with langfuse.start_as_current_observation(
            as_type="span",
            name="agent:invoke/stream",
            input={"input": req.input, "config": req.config},
        ) as span:
            if user_id:
                span.update(user_id=user_id)
            if session_id:
                span.update(session_id=session_id)
            tag_list = tags + ["miao-agent", f"agent:{AGENT_DIR.name}"]
            span.update(tags=tag_list)

            try:
                result = invoke_fn(req.input, req.config)
            except Exception as e:
                span.update(status_message=str(e), level="ERROR")
                span.update(output={"error": str(e)})
                yield _sse_event("error", json.dumps({"message": str(e)}))
                return

            # 接管 SDK 的 sink：手写 generator 里 tool() 上报的事件由 relay 插进流里
            relay = _EventRelay()
            relay.attach()

            # 判断结果类型：async generator / sync generator / 普通值
            # 注意：generator 函数可能根据 config["stream"] 决定是否 yield
            # 首次调用 invoke_fn 时如果是 generator，返回的是 generator 对象而非值
            if inspect.isasyncgen(result):
                try:
                    async for chunk in result:
                        if _is_final_output_chunk(chunk):
                            final_output = chunk["output"]
                            output_chunks.append(chunk["output"])
                            continue
                        if _is_exec_chunk(chunk):
                            # 执行事件：独立事件名，不当正文、也不计入 done.output
                            exec_events.append(chunk["exec"])
                            yield _sse_event(
                                "exec",
                                json.dumps(chunk["exec"], ensure_ascii=False, default=str),
                            )
                            continue
                        chunk_str = json.dumps(chunk, ensure_ascii=False, default=str)
                        output_chunks.append(chunk)
                        yield _sse_event("token", chunk_str)
                        # 手写 async generator 里用 SDK 上报的事件也要出来（见 _EventRelay）
                        for event in relay.drain():
                            exec_events.append(event)
                            yield _sse_event(
                                "exec", json.dumps(event, ensure_ascii=False, default=str))
                except Exception as e:
                    yield _sse_event("error", json.dumps({"message": str(e)}))
                    return
            elif inspect.isgenerator(result):
                # sync generator: 逐 chunk 转发
                try:
                    for chunk in result:
                        if _is_final_output_chunk(chunk):
                            final_output = chunk["output"]
                            output_chunks.append(chunk["output"])
                            continue
                        if _is_exec_chunk(chunk):
                            # 执行事件：独立事件名，不当正文、也不计入 done.output
                            exec_events.append(chunk["exec"])
                            yield _sse_event(
                                "exec",
                                json.dumps(chunk["exec"], ensure_ascii=False, default=str),
                            )
                            await asyncio.sleep(0)  # 让出事件循环，事件要立刻到客户端
                            continue
                        chunk_str = json.dumps(chunk, ensure_ascii=False, default=str)
                        output_chunks.append(chunk)
                        yield _sse_event("token", chunk_str)
                        # 手写 generator 里用 SDK 上报的事件也要出来（见 _EventRelay）
                        for event in relay.drain():
                            exec_events.append(event)
                            yield _sse_event(
                                "exec", json.dumps(event, ensure_ascii=False, default=str))
                        await asyncio.sleep(0)  # 让出事件循环
                except Exception as e:
                    yield _sse_event("error", json.dumps({"message": str(e)}))
                    return
            else:
                # 非 generator：先发 output 事件（让调用方能拿到结果），再发 done
                output_chunks.append(result)
                yield _sse_event("output", json.dumps(result, ensure_ascii=False, default=str))

            # 收尾：取出最后一批积压事件（agent 可能恰好在最后一个 chunk 前上报）
            for event in relay.drain():
                exec_events.append(event)
                yield _sse_event("exec", json.dumps(event, ensure_ascii=False, default=str))

            elapsed_ms = int((time.time() - started) * 1000)
            metadata = {"latency_ms": elapsed_ms}
            if exec_events:
                # 过程事件记进 trace：复盘「这一步为什么慢 / 卡在哪」时不用再翻日志。
                # 只记不转发（转发已在上面逐条完成），且不进 output —— 它不是结果的一部分。
                metadata["exec_events"] = exec_events[:_EXEC_TRACE_LIMIT]
                metadata["exec_events_total"] = len(exec_events)
            span.update(output=output_chunks, metadata=metadata)
            trace_id = langfuse.get_current_trace_id()
            done_payload = {"trace_id": trace_id, "latency_ms": elapsed_ms}
            if final_output is not None:
                # 有收尾块时把结构化结果随 done 下发（平台据此记录该轮 output）
                done_payload["output"] = final_output
            yield _sse_event("done", json.dumps(done_payload, ensure_ascii=False, default=str))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")

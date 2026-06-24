"""
Miao Runner - 每个 agent 子进程实际跑的 FastAPI app。

启动方式：python miao_runner.py <agent_dir> <entrypoint> <port>

每次 /invoke 自动用 Langfuse 上报整体 trace（input/output/latency/tags）。
子进程从父进程继承环境变量（LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL）。

Phase 3: 支持 /invoke/stream SSE 流式输出。
"""
import asyncio
import importlib.util
import inspect
import json
import sys
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

app = FastAPI(title=f"Miao Agent ({AGENT_DIR.name})")


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


@app.post("/invoke/stream")
async def invoke_stream(req: InvokeRequest):
    """流式 invoke，返回 SSE (text/event-stream)。

    如果 agent 函数返回 generator/async generator，逐 chunk 转发；
    否则返回单条 done 事件。
    """
    user_id = req.config.get("langfuse_user_id")
    session_id = req.config.get("langfuse_session_id")
    tags = req.config.get("langfuse_tags", [])

    async def generate():
        started = time.time()
        output_chunks = []
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

            # 判断结果类型：async generator / sync generator / 普通值
            # 注意：generator 函数可能根据 config["stream"] 决定是否 yield
            # 首次调用 invoke_fn 时如果是 generator，返回的是 generator 对象而非值
            if inspect.isasyncgen(result):
                try:
                    async for chunk in result:
                        chunk_str = json.dumps(chunk, ensure_ascii=False, default=str)
                        output_chunks.append(chunk)
                        yield _sse_event("token", chunk_str)
                except Exception as e:
                    yield _sse_event("error", json.dumps({"message": str(e)}))
                    return
            elif inspect.isgenerator(result):
                # sync generator: 逐 chunk 转发
                try:
                    for chunk in result:
                        chunk_str = json.dumps(chunk, ensure_ascii=False, default=str)
                        output_chunks.append(chunk)
                        yield _sse_event("token", chunk_str)
                        await asyncio.sleep(0)  # 让出事件循环
                except Exception as e:
                    yield _sse_event("error", json.dumps({"message": str(e)}))
                    return
            else:
                # 非 generator：先发 output 事件（让调用方能拿到结果），再发 done
                output_chunks.append(result)
                yield _sse_event("output", json.dumps(result, ensure_ascii=False, default=str))

            elapsed_ms = int((time.time() - started) * 1000)
            span.update(output=output_chunks, metadata={"latency_ms": elapsed_ms})
            trace_id = langfuse.get_current_trace_id()
            yield _sse_event(
                "done",
                json.dumps({"trace_id": trace_id, "latency_ms": elapsed_ms}),
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

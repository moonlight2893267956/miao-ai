"""
Invoke API：用户调用已激活 agent 的统一入口。

鉴权：Bearer <api_key>（per-agent 粒度，存 sha256 哈希）。
Trace 上下文从 metadata 提取，转发给 agent 子进程（miao_runner 会上报到 Langfuse）。

Phase 3: 支持 SSE 流式输出（/invoke/stream）+ 异步调用 + webhook 回调（/invoke/async）。
"""
import asyncio
import json
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import AsyncSessionLocal, get_session
from ..models.api_key import ApiKey
from ..models.invoke_task import InvokeTask
from ..runtime.manager import ManagedAgent
from ..runtime.registry import AgentRegistry
from ..schemas.invoke_task import (
    InvokeAsyncRequest,
    InvokeAsyncResponse,
    InvokeTaskStatus,
)
from ..utils import get_agent_or_404, hash_key

router = APIRouter(prefix="/agents/{name}", tags=["invoke"])


class InvokeRequest(BaseModel):
    input: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class InvokeResponse(BaseModel):
    output: dict
    trace_id: str | None = None


async def _auth_agent(name: str, request: Request, session: AsyncSession) -> ManagedAgent:
    """鉴权 + 查 agent + 验证 key + 检查运行状态。

    返回 running 状态的 ManagedAgent，否则抛 HTTPException。
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid Authorization header (expected: Bearer <api_key>)",
        )
    raw_key = auth[7:].strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail="empty API key")

    agent = await get_agent_or_404(name, session)

    key_hash = hash_key(raw_key)
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.agent_id == agent.id,
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(401, "invalid or revoked API key")

    managed = AgentRegistry.instance().get(name)
    if not managed or managed.status != "running":
        # Agent 未运行 → 尝试自动激活（lazily start）
        managed = await _try_auto_activate(name, session)
        if managed is None:
            cur_status = AgentRegistry.instance().get(name)
            cur_status = cur_status.status if cur_status else "stopped"
            raise HTTPException(
                status_code=503,
                detail=f"agent '{name}' not running and auto-activate failed (status={cur_status})",
            )

    return managed


async def _try_auto_activate(name: str, session: AsyncSession) -> ManagedAgent | None:
    """如果 agent 有 active version 但进程未运行，自动启动。

    支持两种场景：
    1. registry 中已有 stopped/idle/crashed 的 agent → 直接重新启动进程
    2. registry 中不存在 → 新建 ManagedAgent 并启动
    """
    from ..models.agent_version import AgentVersion
    from ..runtime.llm_env import resolve_llm_env
    from ..runtime.manager import ManagedAgent
    from ..runtime.registry import AgentRegistry
    from ..config import settings
    from pathlib import Path

    registry = AgentRegistry.instance()

    # 进程池限制
    running_count = registry.running_count()
    if isinstance(running_count, int) and running_count >= settings.agent_max_concurrent:
        return None

    # 查找 active version
    agent_row = await get_agent_or_404(name, session)
    result = await session.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_row.id,
            AgentVersion.is_active.is_(True),
        )
    )
    av = result.scalar_one_or_none()
    if not av:
        return None

    # 场景1：registry 中已有该 agent（可能是 idle/stopped/crashed 状态）
    existing = registry.get(name)
    if existing:
        # 更新环境变量（模型可能已更换）
        existing.llm_env = await resolve_llm_env(agent_row.id, session)
        # Docker 模式：如果有已构建镜像，只启动容器（跳过 build）
        # venv 模式：build_and_start 内部会检查 needs_build
        if existing.runtime_mode == "docker" and existing._image_tag:
            ok = await asyncio.to_thread(existing._start_docker_container)
        else:
            ok = await asyncio.to_thread(existing.build_and_start)
        if ok:
            return existing
        return None

    # 场景2：registry 中不存在，新建
    work_dir = Path(f"/tmp/miao/agents/{name}")
    work_dir.mkdir(parents=True, exist_ok=True)
    runner_path = Path(__file__).parents[2] / "agent_templates" / "miao_runner.py"
    llm_env = await resolve_llm_env(agent_row.id, session)

    managed = ManagedAgent(
        name=name,
        version_id=str(av.id),
        work_dir=work_dir,
        runner_path=runner_path,
        entrypoint=av.entrypoint,
        runtime_mode=settings.agent_runtime_mode,
        max_restarts=settings.agent_max_restarts,
        restart_base_delay=settings.agent_restart_base_delay,
        llm_env=llm_env,
    )
    ok = await asyncio.to_thread(managed.build_and_start)
    if ok:
        await registry.set(managed)
        return managed
    return None


def _build_trace_config(metadata: dict) -> dict:
    """从 metadata 提取 Langfuse trace 配置。"""
    config: dict = {}
    if md_user := metadata.get("user_id"):
        config["langfuse_user_id"] = md_user
    if md_session := metadata.get("session_id"):
        config["langfuse_session_id"] = md_session
    if md_tags := metadata.get("tags"):
        config["langfuse_tags"] = md_tags
    return config


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(
    name: str,
    body: InvokeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InvokeResponse:
    # 鉴权 + 验证
    managed = await _auth_agent(name, request, session)
    # 限流检查
    ok = await asyncio.to_thread(managed.try_acquire_token)
    if not ok:
        raise HTTPException(status_code=429, detail=f"rate limit exceeded for agent '{name}'")
    config = _build_trace_config(body.metadata)
    try:
        result_dict = await asyncio.to_thread(managed.invoke, body.input, 60.0, config)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"agent invoke failed: {e}")
    return InvokeResponse(
        output=result_dict.get("output", {}),
        trace_id=result_dict.get("trace_id"),
    )


@router.post("/invoke/stream")
async def invoke_stream(
    name: str,
    body: InvokeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """SSE 流式调用 agent，返回 text/event-stream。

    agent 函数需返回 generator/async generator 才会产生 token 事件，
    否则只会返回单个 done 事件。
    """
    managed = await _auth_agent(name, request, session)
    ok = await asyncio.to_thread(managed.try_acquire_token)
    if not ok:
        raise HTTPException(status_code=429, detail=f"rate limit exceeded for agent '{name}'")
    config = _build_trace_config(body.metadata)
    # SSE 流式模式：通知 agent 函数使用 generator 逐 token 输出
    config["stream"] = True

    async def event_generator():
        try:
            async for line in managed.invoke_stream(body.input, config):
                yield line + "\n"
                # 强制让出事件循环，使 uvicorn transport 的写缓冲数据真正发到 socket。
                # asyncio.sleep(0) 只让给 ready callbacks 不回 selector；
                # sleep(0.001) 能回到 selector.select() 使 pending writes flush 出去，
                # 且 1ms 延迟对 SSE 流式体验几乎无感知。
                await asyncio.sleep(0.001)
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===== 异步调用 + Webhook =====

@router.post("/invoke/async", response_model=InvokeAsyncResponse, status_code=status.HTTP_202_ACCEPTED)
async def invoke_async(
    name: str,
    body: InvokeAsyncRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """异步调用 agent。立即返回 request_id，完成后 POST webhook_url 推送结果。"""
    managed = await _auth_agent(name, request, session)
    ok = await asyncio.to_thread(managed.try_acquire_token)
    if not ok:
        raise HTTPException(status_code=429, detail=f"rate limit exceeded for agent '{name}'")

    request_id = f"miao_req_{_uuid.uuid4().hex[:12]}"
    config = _build_trace_config(body.metadata)

    # 查 agent_id
    agent = await get_agent_or_404(name, session)
    agent_id = agent.id

    task = InvokeTask(
        agent_id=agent_id,
        agent_name=name,
        request_id=request_id,
        webhook_url=body.webhook_url,
        status="pending",
        input_payload=body.input,
    )
    session.add(task)
    await session.commit()

    # 提交到后台 worker
    worker = request.app.state.task_worker
    worker.submit(
        str(task.id), name, managed, body.input, config,
        body.webhook_url, body.timeout,
        AsyncSessionLocal, settings,
    )

    return InvokeAsyncResponse(
        request_id=request_id,
        status="pending",
        status_url=f"/api/v1/agents/{name}/invoke/async/{request_id}",
    )


@router.get("/invoke/async/{request_id}", response_model=InvokeTaskStatus)
async def get_async_task_status(
    name: str,
    request_id: str,
    session: AsyncSession = Depends(get_session),
) -> InvokeTaskStatus:
    """查询异步任务状态。"""
    result = await session.execute(
        select(InvokeTask).where(
            InvokeTask.request_id == request_id,
            InvokeTask.agent_name == name,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return InvokeTaskStatus(
        request_id=task.request_id,
        status=task.status,
        output=task.output_payload,
        error=task.error_message,
        trace_id=task.trace_id,
        webhook_delivered=task.webhook_delivered,
        created_at=task.created_at,
        completed_at=task.completed_at,
    )

"""
Invoke API：用户调用已激活 agent 的统一入口。

鉴权：Bearer <api_key>（per-agent 粒度，存 sha256 哈希）。
Trace 上下文从 metadata 提取，转发给 agent 子进程（miao_runner 会上报到 Langfuse）。

Phase 3: 支持 SSE 流式输出（/invoke/stream）+ 异步调用 + webhook 回调（/invoke/async）。

多轮会话（P1）：`metadata.conversation_id` 存在时，平台先落一条 running 轮次占位，
再把裁剪后的历史注入 `config.conversation`（仅当生效版本的契约声明
`conversation.enabled: true`），执行完回写 succeeded / failed。
**不传 conversation_id 时这条链路完全不触发**，config 与响应保持原样。
"""
import asyncio
import json
import threading
import uuid as _uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import AsyncSessionLocal, get_session
from ..logging import get_logger
from ..models.agent import Agent
from ..models.api_key import ApiKey
from ..models.conversation import Conversation, ConversationTurn
from ..models.invoke_task import InvokeTask
from ..runtime.manager import ManagedAgent
from ..runtime.registry import AgentRegistry
from ..schemas.invoke_task import (
    InvokeAsyncRequest,
    InvokeAsyncResponse,
    InvokeTaskStatus,
)
from ..services import conversation as conversation_service
from ..services.conversation import TurnAccounting
from ..utils import get_agent_or_404, hash_key

router = APIRouter(prefix="/agents/{name}", tags=["invoke"])

log = get_logger(__name__)


class InvokeRequest(BaseModel):
    input: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class InvokeResponse(BaseModel):
    output: dict
    trace_id: str | None = None


class ConversationInvokeResponse(InvokeResponse):
    """带会话调用的响应（需求文档 §4.3 的新增可选字段）。

    单独建模型而不是给 `InvokeResponse` 加可选字段：不传会话时响应体与改造前
    逐字节一致，老调用方拿不到任何新键。
    """

    conversation_id: str
    turn: int
    history_truncated: bool = False
    # 本轮实际注入给 agent 的轮次号（未声明 conversation.enabled 时为空数组）
    injected_turns: list[int] = Field(default_factory=list)


@dataclass
class ApiKeyAuth:
    """Bearer API Key 鉴权结果（invoke 与会话 CRUD 共用）。"""

    agent: Agent
    api_key: ApiKey


async def authenticate_api_key(
    name: str, request: Request, session: AsyncSession
) -> ApiKeyAuth:
    """校验 ``Bearer <api_key>``，返回 agent 行与命中的 key 行。

    只做鉴权（401 / 404），**不**关心 agent 进程是否在跑——会话 CRUD 不需要
    拉起子进程，因此把「运行状态检查」留给 invoke 路径的 :func:`_auth_agent`。
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid Authorization header (expected: Bearer <api_key>)",
        )
    raw_key = auth_header[7:].strip()
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
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(401, "invalid or revoked API key")
    return ApiKeyAuth(agent=agent, api_key=api_key)


async def _auth_agent(
    name: str, request: Request, session: AsyncSession
) -> tuple[ManagedAgent, ApiKeyAuth]:
    """鉴权 + 检查 agent 运行状态（必要时自动激活）。

    返回 ``(managed, auth)``，否则抛 HTTPException。
    """
    auth = await authenticate_api_key(name, request, session)

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

    return managed, auth


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


# ===== 多轮会话：轮次开启 / 收尾 =====


@dataclass
class ConversationContext:
    """一次带会话调用所需的全部状态（不传 conversation_id 时为 None）。"""

    conversation: Conversation
    turn: ConversationTurn
    accounting: TurnAccounting
    # 生效的 config.conversation；None = 该 agent 未声明 conversation.enabled，不注入
    injected: dict | None
    turn_index: int
    history_truncated: bool
    # 本轮实际注入的轮次号（未声明 conversation.enabled 时为空）
    injected_turns: list[int]

    @property
    def public_id(self) -> str:
        return conversation_service.format_conversation_id(self.conversation.id)


def _conversation_event_line(ctx: ConversationContext) -> str:
    """流式调用时先发一条 conversation 事件，让调用方拿到轮次号与注入明细。"""
    payload = {
        "conversation_id": ctx.public_id,
        "turn": ctx.turn_index,
        "history_truncated": ctx.history_truncated,
        "injected_turns": ctx.injected_turns,
    }
    return f"event: conversation\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _open_conversation_turn(
    *,
    metadata: dict,
    auth: ApiKeyAuth,
    session: AsyncSession,
    payload: dict,
) -> ConversationContext | None:
    """带会话调用前：加闸门 → 校验会话 → 占位轮次 → 组装注入历史。

    ``metadata`` 上没有 ``conversation_id`` 时直接返回 None，调用方走原路径
    （不查会话表、不注入、响应体不变）。

    :raises HTTPException: 404 不存在 / 403 归属不符 / 410 已过期 /
        429 轮次超上限 / 409 同会话并发
    """
    raw_id = metadata.get("conversation_id")
    if raw_id is None:
        return None
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conversation '{raw_id}' not found",
        )
    raw_id = raw_id.strip()
    internal_id = conversation_service.parse_conversation_id(raw_id)
    if internal_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conversation '{raw_id}' not found",
        )

    # 闸门按规范化后的 id 键控：`conv_<hex>` 与裸 UUID 指向同一把锁
    gate_key = str(internal_id)
    gate = conversation_service.acquire_conversation_gate(gate_key)
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"conversation '{raw_id}' has a concurrent turn in flight, "
                "retry after it finishes"
            ),
        )

    try:
        conversation = await conversation_service.resolve_conversation(
            session,
            raw_id,
            agent_id=auth.agent.id,
            api_key_id=auth.api_key.id,
        )
        turn = await conversation_service.begin_turn(
            session,
            conversation=conversation,
            payload=payload,
            max_turns=settings.conversation_max_turns,
        )
        accounting = TurnAccounting(
            conversation_id=conversation_service.format_conversation_id(conversation.id),
            turn_index=turn.turn_index,
            turn_id=turn.id,
            gate_key=gate_key,
            gate=gate,
        )

        # 是否注入历史由**生效版本的契约**决定（opt-in，不声明就不注入）
        declaration = await conversation_service.get_conversation_declaration(
            session, agent_id=auth.agent.id
        )
        injected: dict | None = None
        injected_turns: list[int] = []
        if declaration is not None:
            max_injected = conversation_service.resolve_max_history_turns(
                declaration,
                settings.conversation_default_max_history_turns,
                settings.conversation_max_turns,
            )
            history = await conversation_service.load_injected_history(
                session,
                conversation_id=conversation.id,
                before_turn_index=turn.turn_index,
                max_turns=max_injected,
                max_bytes=settings.conversation_max_history_bytes,
            )
            injected = conversation_service.build_injected_config(
                conversation_id=conversation.id,
                turn_index=turn.turn_index,
                max_turns_injected=max_injected,
                history=history,
            )
            injected_turns = history.turn_indexes
    except BaseException:
        # 任一环节失败都不能留着闸门（否则该会话后续请求会一直 409）
        conversation_service.release_conversation_gate(gate_key, gate)
        raise

    log.info(
        "conversation.turn.opened",
        conversation_id=accounting.conversation_id,
        turn=turn.turn_index,
        injected=injected is not None,
    )
    return ConversationContext(
        conversation=conversation,
        turn=turn,
        accounting=accounting,
        injected=injected,
        turn_index=turn.turn_index,
        history_truncated=bool(injected and injected["truncated"]),
        injected_turns=injected_turns,
    )


async def _close_conversation_turn(
    ctx: ConversationContext | None,
    session: AsyncSession,
    *,
    succeeded: bool,
    output: dict | None = None,
    trace_id: str | None = None,
) -> None:
    """收尾：回写轮次结果并释放闸门（无会话时是空操作）。"""
    if ctx is None:
        return
    try:
        await conversation_service.finish_turn(
            session,
            turn=ctx.turn,
            conversation=ctx.conversation,
            succeeded=succeeded,
            output=output,
            trace_id=trace_id,
        )
    except Exception:
        log.exception(
            "conversation.turn.close_failed",
            conversation_id=ctx.public_id,
            turn=ctx.turn_index,
        )
    finally:
        conversation_service.release_conversation_gate(
            ctx.accounting.gate_key, ctx.accounting.gate
        )


def _apply_conversation_config(config: dict, ctx: ConversationContext | None) -> None:
    """有会话且 agent 声明开启时，把历史塞进 config（否则 config 一字不改）。"""
    if ctx is not None and ctx.injected is not None:
        config["conversation"] = ctx.injected


@router.post("/invoke", response_model=InvokeResponse | ConversationInvokeResponse)
async def invoke(
    name: str,
    body: InvokeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InvokeResponse:
    # 鉴权 + 验证
    managed, auth = await _auth_agent(name, request, session)
    # 限流检查
    ok = await asyncio.to_thread(managed.try_acquire_token)
    if not ok:
        raise HTTPException(status_code=429, detail=f"rate limit exceeded for agent '{name}'")
    config = _build_trace_config(body.metadata)
    ctx = await _open_conversation_turn(
        metadata=body.metadata, auth=auth, session=session, payload=body.input
    )
    _apply_conversation_config(config, ctx)
    try:
        result_dict = await asyncio.to_thread(managed.invoke, body.input, settings.invoke_sync_timeout, config)
    except asyncio.CancelledError:
        # 客户端断连：轮次记 failed，闸门必须释放（否则该会话会一直 409）
        await _close_conversation_turn(ctx, session, succeeded=False)
        raise
    except Exception as e:
        await _close_conversation_turn(ctx, session, succeeded=False)
        raise HTTPException(status_code=502, detail=f"agent invoke failed: {e}")
    output = result_dict.get("output", {})
    trace_id = result_dict.get("trace_id")
    await _close_conversation_turn(
        ctx, session, succeeded=True, output=output, trace_id=trace_id
    )
    if ctx is not None:
        return ConversationInvokeResponse(
            output=output,
            trace_id=trace_id,
            conversation_id=ctx.public_id,
            turn=ctx.turn_index,
            history_truncated=ctx.history_truncated,
            injected_turns=ctx.injected_turns,
        )
    return InvokeResponse(output=output, trace_id=trace_id)


def _parse_json_or_none(raw: str) -> object | None:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


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

    带会话时：先补发一条 ``conversation`` 事件（会话 ID / 轮次号 / 裁剪标记），
    流正常结束记该轮 succeeded（纯 token 流的 output 记为 null，因为 runner 的
    ``done`` 事件只带 trace_id；只有非 generator 的 agent 会给出完整 ``output``
    事件），中断或收到 error 事件记 failed。
    """
    managed, auth = await _auth_agent(name, request, session)
    ok = await asyncio.to_thread(managed.try_acquire_token)
    if not ok:
        raise HTTPException(status_code=429, detail=f"rate limit exceeded for agent '{name}'")
    config = _build_trace_config(body.metadata)
    ctx = await _open_conversation_turn(
        metadata=body.metadata, auth=auth, session=session, payload=body.input
    )
    _apply_conversation_config(config, ctx)
    # SSE 流式模式：通知 agent 函数使用 generator 逐 token 输出
    config["stream"] = True

    async def event_generator():
        succeeded = False
        errored = False
        output: dict | None = None
        trace_id: str | None = None
        current_event = ""
        try:
            if ctx is not None:
                yield _conversation_event_line(ctx)
            async for line in managed.invoke_stream(body.input, config, timeout=settings.invoke_stream_timeout):
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    parsed = _parse_json_or_none(line[5:].strip())
                    if current_event == "done" and isinstance(parsed, dict):
                        trace_id = parsed.get("trace_id") or trace_id
                        # 逐 token 流的 agent 可以把结构化结果随 done 下发
                        # （约定见 miao_runner 的流式说明），这里据此记录本轮 output
                        if output is None and isinstance(parsed.get("output"), dict):
                            output = parsed["output"]
                    elif current_event == "output" and isinstance(parsed, dict):
                        output = parsed
                    elif current_event == "error":
                        errored = True
                yield line + "\n"
                # 强制让出事件循环，使 uvicorn transport 的写缓冲数据真正发到 socket。
                # asyncio.sleep(0) 只让给 ready callbacks 不回 selector；
                # sleep(0.001) 能回到 selector.select() 使 pending writes flush 出去，
                # 且 1ms 延迟对 SSE 流式体验几乎无感知。
                await asyncio.sleep(0.001)
            succeeded = not errored
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
        finally:
            # 客户端断连 / 取消时也会走到这里：轮次记 failed，闸门必定释放
            await _close_conversation_turn(
                ctx, session, succeeded=succeeded, output=output, trace_id=trace_id
            )

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
    managed, auth = await _auth_agent(name, request, session)
    ok = await asyncio.to_thread(managed.try_acquire_token)
    if not ok:
        raise HTTPException(status_code=429, detail=f"rate limit exceeded for agent '{name}'")

    request_id = f"miao_req_{_uuid.uuid4().hex[:12]}"
    config = _build_trace_config(body.metadata)
    ctx = await _open_conversation_turn(
        metadata=body.metadata, auth=auth, session=session, payload=body.input
    )
    _apply_conversation_config(config, ctx)

    task = InvokeTask(
        agent_id=auth.agent.id,
        agent_name=name,
        request_id=request_id,
        webhook_url=body.webhook_url,
        status="pending",
        input_payload=body.input,
    )
    session.add(task)
    handed_off = False
    try:
        await session.commit()
        # 提交到后台 worker（会话轮次的收尾由 worker 线程用独立 session 完成）
        worker = request.app.state.task_worker
        worker.submit(
            str(task.id), name, managed, body.input, config,
            body.webhook_url, body.timeout,
            AsyncSessionLocal, settings,
            ctx.accounting if ctx else None,
        )
        handed_off = True
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"failed to submit async task: {e}"
        )
    finally:
        # 未成功交接（含取消 / 断连）时把轮次记 failed 并释放闸门
        if not handed_off:
            await _close_conversation_turn(ctx, session, succeeded=False)

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

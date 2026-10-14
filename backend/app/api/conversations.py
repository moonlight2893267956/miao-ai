"""
会话（Conversation）管理 API：per-agent API Key 鉴权，会话按调用方隔离。

    POST   /agents/{name}/conversations              新建（201）
    GET    /agents/{name}/conversations/{cid}        详情（含全部轮次）
    GET    /agents/{name}/conversations              列表（仅当前 key 的会话）
    DELETE /agents/{name}/conversations/{cid}        删除（204，轮次级联删除）

隔离规则：会话归属到创建它的 API Key（``api_key_id``），A 的 key 读不到 B 的会话
（归属不符统一 403，不泄露会话是否存在）。

过期语义：新建轮次（invoke）遇到过期会话返回 410；但**读详情 / 列表 / 删除**
不做过期拦截——会话过期只是不能再追加轮次，历史仍应可审计（需求 §2 目标 4），
真正清理走 DELETE 或按 TTL 的惰性判断。
"""
import uuid as _uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models.conversation import Conversation, ConversationTurn
from ..schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationListResponse,
    ConversationRead,
    ConversationTurnRead,
)
from ..services import conversation as conversation_service
from .invoke import authenticate_api_key

router = APIRouter(prefix="/agents/{name}/conversations", tags=["conversations"])


def _to_read(conversation: Conversation, agent_name: str, turn_count: int) -> ConversationRead:
    return ConversationRead(
        conversation_id=conversation_service.format_conversation_id(conversation.id),
        agent_name=agent_name,
        created_at=conversation.created_at,
        expires_at=conversation.expires_at,
        turn_count=turn_count,
    )


async def _count_turns(session: AsyncSession, conversation_ids: list[_uuid.UUID]) -> dict[_uuid.UUID, int]:
    """一次聚合查询取回多个会话的轮次数（避免列表接口 N+1）。"""
    if not conversation_ids:
        return {}
    result = await session.execute(
        select(ConversationTurn.conversation_id, func.count())
        .where(ConversationTurn.conversation_id.in_(conversation_ids))
        .group_by(ConversationTurn.conversation_id)
    )
    return {row[0]: int(row[1]) for row in result.all()}


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    name: str,
    request: Request,
    payload: ConversationCreate | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> ConversationRead:
    """新建会话（请求体可省略，等价于用平台默认 TTL 且无 metadata）。"""
    auth = await authenticate_api_key(name, request, session)
    conversation = await conversation_service.create_conversation(
        session,
        agent_id=auth.agent.id,
        api_key_id=auth.api_key.id,
        metadata=payload.metadata if payload else None,
        ttl_seconds=payload.ttl_seconds if payload else None,
        default_ttl_seconds=settings.conversation_default_ttl_seconds,
        max_ttl_seconds=settings.conversation_max_ttl_seconds,
    )
    return _to_read(conversation, auth.agent.name, turn_count=0)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    name: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ConversationListResponse:
    """列出当前 API Key 在本 agent 下创建的会话（按创建时间倒序）。"""
    auth = await authenticate_api_key(name, request, session)

    total_result = await session.execute(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.agent_id == auth.agent.id,
            Conversation.api_key_id == auth.api_key.id,
        )
    )
    total = int(total_result.scalar_one() or 0)

    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.agent_id == auth.agent.id,
            Conversation.api_key_id == auth.api_key.id,
        )
        .order_by(Conversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    conversations = list(result.scalars().all())
    counts = await _count_turns(session, [c.id for c in conversations])
    return ConversationListResponse(
        items=[_to_read(c, auth.agent.name, counts.get(c.id, 0)) for c in conversations],
        total=total,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    name: str,
    conversation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ConversationDetail:
    """读会话详情：概要 + 全部轮次（含 running / failed，按 index 升序）。"""
    auth = await authenticate_api_key(name, request, session)
    internal_id = conversation_service.parse_conversation_id(conversation_id)
    if internal_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conversation '{conversation_id}' not found",
        )
    conversation = await conversation_service.get_conversation_or_403(
        session, internal_id, agent_id=auth.agent.id, api_key_id=auth.api_key.id
    )
    result = await session.execute(
        select(ConversationTurn)
        .where(ConversationTurn.conversation_id == conversation.id)
        .order_by(ConversationTurn.turn_index.asc())
    )
    turns = list(result.scalars().all())
    return ConversationDetail(
        conversation_id=conversation_service.format_conversation_id(conversation.id),
        agent_name=auth.agent.name,
        created_at=conversation.created_at,
        expires_at=conversation.expires_at,
        turn_count=len(turns),
        metadata=conversation.metadata_,
        updated_at=conversation.updated_at,
        turns=[
            ConversationTurnRead(
                index=turn.turn_index,
                input=turn.input,
                output=turn.output,
                status=turn.status,
                trace_id=turn.trace_id,
                created_at=turn.created_at,
            )
            for turn in turns
        ],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    name: str,
    conversation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """删除会话；轮次由数据库外键 ON DELETE CASCADE 一并清理。"""
    auth = await authenticate_api_key(name, request, session)
    internal_id = conversation_service.parse_conversation_id(conversation_id)
    if internal_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conversation '{conversation_id}' not found",
        )
    conversation = await conversation_service.get_conversation_or_403(
        session, internal_id, agent_id=auth.agent.id, api_key_id=auth.api_key.id
    )
    await session.execute(
        delete(Conversation).where(Conversation.id == conversation.id)
    )
    await session.commit()

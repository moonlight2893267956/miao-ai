"""conversation Pydantic schemas（需求文档 §4.1）。"""
from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    """新建会话请求体。``ttl_seconds`` 缺省用平台默认（1 小时），上限 24 小时。"""

    metadata: dict = Field(default_factory=dict)
    ttl_seconds: int | None = Field(default=None, ge=1)


class ConversationTurnRead(BaseModel):
    """单轮记录。``index`` 从 1 开始，status ∈ running / succeeded / failed。"""

    index: int
    input: dict
    output: dict | None = None
    status: str
    trace_id: str | None = None
    created_at: datetime | None = None


class ConversationRead(BaseModel):
    """会话概要（新建响应 / 列表项）。"""

    conversation_id: str
    agent_name: str
    created_at: datetime
    expires_at: datetime | None = None
    turn_count: int = 0


class ConversationDetail(ConversationRead):
    """会话详情：概要 + 全部轮次（按 index 升序）。"""

    metadata: dict | None = None
    updated_at: datetime | None = None
    turns: list[ConversationTurnRead] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    items: list[ConversationRead]
    total: int

"""
Conversation / ConversationTurn：平台侧多轮会话与逐轮记录。

平台存历史、agent 无状态（需求文档方案 A）：每轮调用由平台分配轮次号、
先落 status='running' 占位，执行完回写 succeeded / failed；下轮调用时把
裁剪后的历史注入 ``config.conversation``，agent 自行决定怎么用。

会话归属到创建它的 API Key（``api_key_id``）做隔离，A 的 key 读不到 B 的会话。
"""
import uuid as _uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .agent import Agent
    from .api_key import ApiKey


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_conv_agent_created", "agent_id", "created_at"),
    )

    id: Mapped[_uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid.uuid4)
    agent_id: Mapped[_uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 归属：创建会话时用的 API Key。key 被删除后置 NULL（会话本身保留可审计）
    api_key_id: Mapped[_uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 注意：属性名不能叫 metadata（与 Declarative 的 Base.metadata 冲突），
    # 因此用 metadata_ + 显式列名 "metadata" 对齐需求文档 §6 的表结构。
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    # NULL = 不过期（新建时指定 ttl_seconds 才写入）
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    agent: Mapped["Agent"] = relationship("Agent")
    api_key: Mapped["ApiKey | None"] = relationship("ApiKey")
    turns: Mapped[list["ConversationTurn"]] = relationship(
        "ConversationTurn",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "turn_index", name="uq_conv_turn"),
        Index("idx_turn_conv", "conversation_id", "turn_index"),
    )

    id: Mapped[_uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid.uuid4)
    conversation_id: Mapped[_uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 会话内序号，从 1 开始；与 conversation_id 组成唯一约束，防并发撞号
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # running / succeeded / failed
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="turns"
    )

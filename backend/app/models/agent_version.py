"""
AgentVersion：Agent 的一个具体版本（上传的代码包 + 入口配置）。
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .agent import Agent


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_url: Mapped[str] = mapped_column(Text, nullable=False)  # COS object key / URL
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    entrypoint: Mapped[str] = mapped_column(String(64), nullable=False, default="agent:invoke")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    # building / running / crashed / idle
    build_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    agent: Mapped["Agent"] = relationship("Agent", back_populates="versions")

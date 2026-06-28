"""
Agent 主表：每个 Agent 是一个有版本的 Python 代码包 + 入口配置。
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .agent_version import AgentVersion
    from .api_key import ApiKey
    from .llm_model import LlmModel


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    # 只允许小写字母、数字、连字符（URL/文件名友好）
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    versions: Mapped[list["AgentVersion"]] = relationship(
        "AgentVersion", back_populates="agent", cascade="all, delete-orphan"
    )
    keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey", back_populates="agent", cascade="all, delete-orphan"
    )
    model: Mapped["LlmModel | None"] = relationship("LlmModel", back_populates="agents")

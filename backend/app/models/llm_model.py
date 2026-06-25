"""LLM model configuration under a provider."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .agent import Agent
    from .model_provider import ModelProvider


class LlmModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    temperature_default: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    provider: Mapped["ModelProvider"] = relationship("ModelProvider", back_populates="models")
    agents: Mapped[list["Agent"]] = relationship("Agent", back_populates="model")

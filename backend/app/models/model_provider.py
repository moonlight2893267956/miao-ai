"""OpenAI-compatible LLM provider configuration."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .llm_model import LlmModel


class ModelProvider(Base):
    __tablename__ = "model_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    models: Mapped[list["LlmModel"]] = relationship(
        "LlmModel", back_populates="provider", cascade="all, delete-orphan"
    )

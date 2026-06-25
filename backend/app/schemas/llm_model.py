"""LLM model schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LlmModelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    provider_id: uuid.UUID
    model_id: str = Field(..., min_length=1, max_length=128)
    max_tokens: int = Field(default=4096, ge=1)
    temperature_default: float = Field(default=0.7, ge=0.0, le=2.0)
    is_default: bool = False


class LlmModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    max_tokens: int | None = Field(default=None, ge=1)
    temperature_default: float | None = Field(default=None, ge=0.0, le=2.0)
    is_default: bool | None = None


class LlmModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider_id: uuid.UUID
    model_id: str
    max_tokens: int
    temperature_default: float
    is_default: bool
    created_at: datetime
    provider_name: str | None = None

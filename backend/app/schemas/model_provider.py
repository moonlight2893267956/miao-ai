"""Provider schemas for LLM configuration."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    api_key: str = Field(..., min_length=1)
    base_url: HttpUrl


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: str | None = Field(default=None, min_length=1)
    base_url: HttpUrl | None = None


class ProviderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    base_url: str
    created_at: datetime

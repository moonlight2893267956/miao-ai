"""ApiKey Pydantic schemas。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    label: str | None = Field(default=None, max_length=64)


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    label: str | None
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyWithSecret(ApiKeyRead):
    """创建 key 时返回（含明文 key，只显示一次）。"""
    key: str

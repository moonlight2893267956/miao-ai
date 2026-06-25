"""Agent Pydantic schemas。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str | None = None


class AgentCreate(AgentBase):
    model_id: uuid.UUID | None = None


class AgentRead(AgentBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    model_id: uuid.UUID | None = None
    created_at: datetime
    # 实时从 Runtime Registry 查：stopped / building / running / crashed / idle
    status: str = "stopped"
    active_version: str | None = None


class AgentModelUpdate(BaseModel):
    model_id: uuid.UUID | None = None

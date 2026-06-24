"""AgentVersion Pydantic schemas。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version: str
    artifact_url: str
    entrypoint: str
    is_active: bool
    status: str
    created_at: datetime

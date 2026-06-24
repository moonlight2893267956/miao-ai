"""invoke_task Pydantic schemas。"""
from datetime import datetime

from pydantic import BaseModel, Field


class InvokeAsyncRequest(BaseModel):
    input: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    webhook_url: str = Field(..., pattern=r"^https?://")
    timeout: float = Field(default=300.0, ge=10.0, le=3600.0)


class InvokeAsyncResponse(BaseModel):
    request_id: str
    status: str
    status_url: str


class InvokeTaskStatus(BaseModel):
    request_id: str
    status: str
    output: dict | None = None
    error: str | None = None
    trace_id: str | None = None
    webhook_delivered: bool = False
    created_at: datetime | None = None
    completed_at: datetime | None = None

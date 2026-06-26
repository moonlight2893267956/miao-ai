"""Auth Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=255)


class AuthUser(BaseModel):
    id: uuid.UUID
    username: str
    created_at: datetime


class AuthStatus(BaseModel):
    user: AuthUser

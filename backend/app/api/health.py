"""
健康检查端点。

- GET /api/v1/health：基础 ping
- GET /api/v1/health/ready：包含 DB 连通性检查
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    # 探活时跑个 SELECT 1 验证 DB 连通
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "db": "ok"}

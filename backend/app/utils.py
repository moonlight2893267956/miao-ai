"""项目内共享工具函数。"""

import hashlib

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models.agent import Agent


def hash_key(key: str) -> str:
    """API Key sha256 哈希（多处复用）。"""
    return hashlib.sha256(key.encode()).hexdigest()


async def get_agent_or_404(name: str, session: AsyncSession) -> Agent:
    """按 name 查 Agent，找不到 raise 404。"""
    result = await session.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found"
        )
    return agent

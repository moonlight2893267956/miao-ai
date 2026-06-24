"""
SQLAlchemy 2.0 async 引擎与会话工厂。

Neon 是 serverless PG，用 asyncpg 驱动 + NullPool 避免连接浪费。
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from .config import settings

# Neon 推荐用 NullPool（每次请求新建连接，结束后归还）
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用的 session（直接用 async generator，不要 @asynccontextmanager 装饰）。"""
    async with AsyncSessionLocal() as session:
        yield session

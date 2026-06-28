"""
SQLAlchemy 2.0 async 引擎与会话工厂。

使用 aiomysql 异步驱动连接 MySQL。
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

# MySQL 使用默认连接池（与 Neon serverless PG 的 NullPool 策略不同）
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
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

"""
SQLAlchemy 2.0 async 引擎与会话工厂。

Neon 是 serverless PG，用 asyncpg 驱动 + NullPool 避免连接浪费。
"""
from collections.abc import AsyncIterator

from sqlalchemy import event
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


# Neon 部分 branch（特别是 dev/auto-suspend 的）新建 connection 时 search_path 为空，
# 导致 SELECT users 报 "relation does not exist"。prod branch 默认是 "$user", public。
# Neon 不支持 startup packet 传 search_path（unsupported startup parameter），
# 必须在物理连接建立后用 SET 命令设置。
# 对 prod 来说这条 SET 也无害（即使 search_path 已经是 "$user", public，
# 改成 public 不会破坏任何查询）。
@event.listens_for(engine.sync_engine, "connect")
def _set_search_path(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET search_path TO public")
    finally:
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用的 session（直接用 async generator，不要 @asynccontextmanager 装饰）。"""
    async with AsyncSessionLocal() as session:
        yield session

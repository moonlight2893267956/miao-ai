"""
SQLAlchemy 2.0 async 引擎与会话工厂。

使用 aiomysql 异步驱动连接 MySQL。
"""
from collections.abc import AsyncIterator
import ssl
from urllib.parse import parse_qs, urlparse, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings


def build_engine(url: str | None = None):
    """根据 DATABASE_URL 构造异步引擎。

    aiomysql 期望 `connect_args["ssl"]` 是一个 `ssl.SSLContext` 对象，而不是 URL
    里的 `ssl=true` 字符串（传 True 会被当成默认上下文做证书校验，自签证书会握手
    失败并包成 "2003 Can't connect"）。这里把 `ssl` 标记转成真正的 SSLContext：
    关闭主机名/证书校验（远端 MySQL 使用自签证书），但仍走 TLS 加密，避免凭据与
    数据在公网上明文传输。未来若换用 CA 签发证书，可改为
    `ssl.create_default_context(cafile=...)` 做完整校验。
    """
    url = url or settings.database_url
    connect_args: dict = {}
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if qs.get("ssl") and str(qs["ssl"][0]).lower() in ("true", "1", "yes"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ctx
        qs.pop("ssl")
        parsed = parsed._replace(query="&".join(f"{k}={v[0]}" for k, v in qs.items()))
        url = urlunparse(parsed)
    return create_async_engine(
        url,
        echo=settings.debug,
        pool_size=5,
        max_overflow=10,
        pool_recycle=300,  # 远程公网连接：低于 NAT/防火墙空闲超时，避免 "MySQL server has gone away"
        pool_pre_ping=True,  # 取连接前探活，自动剔除死连接
        connect_args=connect_args,  # 空 dict 也是合法值；传 None 会让 SQLAlchemy 的 immutabledict.union 报错
    )


engine = build_engine()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用的 session（直接用 async generator，不要 @asynccontextmanager 装饰）。"""
    async with AsyncSessionLocal() as session:
        yield session

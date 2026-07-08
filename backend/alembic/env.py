"""
Alembic env：async 模式，从 app.config 读 DATABASE_URL。
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection

from alembic import context

# 导入 settings 和所有模型（让 Base.metadata 认识它们）
from app.config import settings
from app.db import build_engine  # 复用同一套 SSL 感知的引擎构造逻辑
from app.models import (  # noqa: F401
    Agent,
    AgentVersion,
    ApiKey,
    InvokeTask,
    LlmModel,
    ModelProvider,
    User,
    UserSession,
)
from app.models.base import Base

config = context.config
# 把 sqlalchemy.url 设成 settings.database_url（从 .env 读）
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # 复用 app.db.build_engine：与运行期使用完全相同的 URL 解析与 SSL 处理方式，
    # 避免 alembic 直接读含 `&ssl=true` 的 DATABASE_URL 而把字符串传给 aiomysql 导致失败。
    connectable = build_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

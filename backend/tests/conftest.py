"""
Pytest fixtures。
"""
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import AsyncSessionLocal
from app.models.user import User

# 测试启动时也加载根 .env
_ROOT_ENV = Path(__file__).parents[2] / ".env"
if _ROOT_ENV.exists():
    load_dotenv(_ROOT_ENV)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def logged_in_client() -> AsyncClient:
    username = f"test-user-{uuid.uuid4().hex[:8]}"
    password = f"pw-{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as session:
        user = User(username=username, password=password)
        session.add(user)
        await session.commit()

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        r = await ac.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert r.status_code == 200, r.text
        try:
            yield ac
        finally:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("DELETE FROM users WHERE username = :username"),
                    {"username": username},
                )
                await session.commit()


@pytest.fixture(autouse=True)
async def cleanup_generated_rows():
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                """
                DELETE FROM agents
                WHERE name LIKE 'test-%' OR name LIKE 'invoke-%' OR name LIKE 'agent-%'
                """
            )
        )
        await session.execute(text("DELETE FROM model_providers WHERE name LIKE 'provider-%'"))
        await session.execute(
            text("DELETE FROM users WHERE username LIKE 'test-user-%' OR username LIKE 'auth-%'")
        )
        await session.commit()

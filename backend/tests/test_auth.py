"""Login session integration tests."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import AsyncSessionLocal
from app.main import app
from app.models.user import User


@pytest.mark.asyncio
async def test_login_me_and_logout() -> None:
    username = f"auth-{uuid.uuid4().hex[:8]}"
    password = "plain-password"
    async with AsyncSessionLocal() as session:
        session.add(User(username=username, password=password))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert r.status_code == 200, r.text
        assert r.json()["user"]["username"] == username

        r = await ac.get("/api/v1/auth/me")
        assert r.status_code == 200
        assert r.json()["user"]["username"] == username

        r = await ac.post("/api/v1/auth/logout")
        assert r.status_code == 204

        r = await ac.get("/api/v1/auth/me")
        assert r.status_code == 401

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM users WHERE username = :username"),
            {"username": username},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_login_rejects_bad_password() -> None:
    username = f"auth-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        session.add(User(username=username, password="correct"))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong"},
        )
        assert r.status_code == 401

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM users WHERE username = :username"),
            {"username": username},
        )
        await session.commit()

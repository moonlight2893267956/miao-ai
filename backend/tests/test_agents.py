"""
Agent / Key CRUD 集成测试。

每个 test 用唯一 name 避免测试间数据污染。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _unique_name() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_create_and_get_agent() -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 初始列表可能非空（其他测试残留），只验证创建后能取到
        r = await ac.post("/api/v1/agents", json={"name": name, "description": "x"})
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["name"] == name
        assert "id" in created

        r = await ac.get(f"/api/v1/agents/{name}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_duplicate_agent_409() -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/api/v1/agents", json={"name": name})
        assert r.status_code == 201
        # 第二次同名 → 409
        r = await ac.post("/api/v1/agents", json={"name": name})
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_nonexistent_agent_404() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get(f"/api/v1/agents/{_unique_name()}")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_key_returns_plain_once() -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/agents", json={"name": name})
        # 无 body 创建
        r = await ac.post(f"/api/v1/agents/{name}/keys")
        assert r.status_code == 201
        k = r.json()
        assert k["key"].startswith("miao_")
        assert k["label"] is None

        # list 应该能看到，但不返回明文
        r = await ac.get(f"/api/v1/agents/{name}/keys")
        assert r.status_code == 200
        keys = r.json()
        assert len(keys) == 1
        assert "key" not in keys[0]
        assert keys[0]["id"] == k["id"]


@pytest.mark.asyncio
async def test_revoke_key() -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/agents", json={"name": name})
        r = await ac.post(f"/api/v1/agents/{name}/keys", json={"label": "to-revoke"})
        key_id = r.json()["id"]

        r = await ac.delete(f"/api/v1/agents/{name}/keys/{key_id}")
        assert r.status_code == 204

        # 撤销后 list 不再返回
        r = await ac.get(f"/api/v1/agents/{name}/keys")
        assert r.status_code == 200
        assert r.json() == []

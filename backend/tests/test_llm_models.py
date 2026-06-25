"""LLM provider/model management integration tests."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.crypto import encrypt_secret
from app.db import AsyncSessionLocal
from app.main import app
from app.runtime.llm_env import resolve_llm_env


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _create_provider(ac: AsyncClient, name: str | None = None) -> dict:
    r = await ac.post(
        "/api/v1/providers",
        json={
            "name": name or _unique_name("provider"),
            "api_key": "test-secret",
            "base_url": "https://example.com/v1",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_provider_crud_never_returns_plain_api_key() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        provider = await _create_provider(ac)
        assert "api_key" not in provider
        assert "api_key_encrypted" not in provider

        r = await ac.get("/api/v1/providers")
        assert r.status_code == 200
        match = [p for p in r.json() if p["id"] == provider["id"]]
        assert match
        assert "api_key" not in match[0]
        assert "api_key_encrypted" not in match[0]


@pytest.mark.asyncio
async def test_model_default_is_exclusive_and_agent_binding_updates_model_id() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        provider = await _create_provider(ac)
        r = await ac.post(
            "/api/v1/models",
            json={
                "name": _unique_name("model-a"),
                "provider_id": provider["id"],
                "model_id": "model-a",
                "is_default": True,
            },
        )
        assert r.status_code == 201, r.text
        model_a = r.json()

        r = await ac.post(
            "/api/v1/models",
            json={
                "name": _unique_name("model-b"),
                "provider_id": provider["id"],
                "model_id": "model-b",
                "is_default": True,
            },
        )
        assert r.status_code == 201, r.text
        model_b = r.json()

        r = await ac.get("/api/v1/models")
        assert r.status_code == 200
        models = {m["id"]: m for m in r.json() if m["id"] in {model_a["id"], model_b["id"]}}
        assert models[model_a["id"]]["is_default"] is False
        assert models[model_b["id"]]["is_default"] is True
        assert models[model_b["id"]]["provider_name"] == provider["name"]

        agent_name = _unique_name("agent")
        r = await ac.post("/api/v1/agents", json={"name": agent_name})
        assert r.status_code == 201
        assert r.json()["model_id"] is None

        r = await ac.put(f"/api/v1/agents/{agent_name}/model", json={"model_id": model_b["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["model_id"] == model_b["id"]

        r = await ac.put(f"/api/v1/agents/{agent_name}/model", json={"model_id": None})
        assert r.status_code == 200, r.text
        assert r.json()["model_id"] is None


@pytest.mark.asyncio
async def test_delete_model_unbinds_agents_to_default_fallback() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        provider = await _create_provider(ac)
        r = await ac.post(
            "/api/v1/models",
            json={
                "name": _unique_name("delete-me"),
                "provider_id": provider["id"],
                "model_id": "delete-me",
            },
        )
        assert r.status_code == 201, r.text
        model = r.json()

        agent_name = _unique_name("agent")
        r = await ac.post("/api/v1/agents", json={"name": agent_name, "model_id": model["id"]})
        assert r.status_code == 201, r.text
        assert r.json()["model_id"] == model["id"]

        r = await ac.delete(f"/api/v1/models/{model['id']}")
        assert r.status_code == 204, r.text

        r = await ac.get(f"/api/v1/agents/{agent_name}")
        assert r.status_code == 200
        assert r.json()["model_id"] is None


@pytest.mark.asyncio
async def test_resolve_llm_env_prefers_agent_model_then_default() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        provider = await _create_provider(ac)
        r = await ac.post(
            "/api/v1/models",
            json={
                "name": _unique_name("default"),
                "provider_id": provider["id"],
                "model_id": "default-model",
                "is_default": True,
            },
        )
        assert r.status_code == 201, r.text
        default_model = r.json()

        r = await ac.post(
            "/api/v1/models",
            json={
                "name": _unique_name("bound"),
                "provider_id": provider["id"],
                "model_id": "bound-model",
            },
        )
        assert r.status_code == 201, r.text
        bound_model = r.json()

        r = await ac.post("/api/v1/agents", json={"name": _unique_name("agent")})
        assert r.status_code == 201
        agent = r.json()

        async with AsyncSessionLocal() as session:
            env = await resolve_llm_env(uuid.UUID(agent["id"]), session)
        assert env["LLM_MODEL"] == default_model["model_id"]
        assert env["DASHSCOPE_MODEL"] == default_model["model_id"]
        assert env["LLM_API_KEY"] == "test-secret"

        r = await ac.put(
            f"/api/v1/agents/{agent['name']}/model",
            json={"model_id": bound_model["id"]},
        )
        assert r.status_code == 200

        async with AsyncSessionLocal() as session:
            env = await resolve_llm_env(uuid.UUID(agent["id"]), session)
        assert env["LLM_MODEL"] == bound_model["model_id"]
        assert env["DASHSCOPE_MODEL"] == bound_model["model_id"]


@pytest.mark.asyncio
async def test_resolve_llm_env_falls_back_to_global_settings() -> None:
    missing_agent_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        env = await resolve_llm_env(missing_agent_id, session)
    assert env["LLM_MODEL"]
    assert env["DASHSCOPE_MODEL"] == env["LLM_MODEL"]


def test_encrypt_secret_requires_configured_fernet_key() -> None:
    encrypted = encrypt_secret("round-trip")
    assert encrypted != "round-trip"

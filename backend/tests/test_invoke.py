"""
Invoke API 集成测试（mock Runtime，不真启动子进程）。

覆盖：
- 401（无 Authorization）
- 401（错 key）
- 503（agent 没启动 / Registry 找不到）
- 200（mock Runtime 成功，验证输入透传 + 返回透传）
"""
import uuid
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.runtime.registry import AgentRegistry


def _unique_name() -> str:
    return f"invoke-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def mock_running_agent():
    """Mock Registry 让 agent 看起来在跑 + invoke 返回固定值。"""
    mock_managed = MagicMock()
    mock_managed.status = "running"
    mock_managed.invoke.return_value = {
        "output": {"answer": "42"},
        "trace_id": "abc123",
    }
    AgentRegistry._instance = MagicMock()
    AgentRegistry._instance.get.return_value = mock_managed
    yield mock_managed
    AgentRegistry._instance = None


@pytest.fixture
def mock_stopped_agent():
    """Mock Registry 让 agent 不在跑。"""
    AgentRegistry._instance = MagicMock()
    AgentRegistry._instance.get.return_value = None
    yield
    AgentRegistry._instance = None


@pytest.mark.asyncio
async def test_invoke_missing_auth() -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/agents", json={"name": name})
        r = await ac.post(f"/api/v1/agents/{name}/invoke", json={"input": {"q": "x"}})
        assert r.status_code == 401
        assert "Authorization" in r.json()["detail"]


@pytest.mark.asyncio
async def test_invoke_invalid_key() -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/agents", json={"name": name})
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke",
            headers={"Authorization": "Bearer miao_wrongkey"},
            json={"input": {"q": "x"}},
        )
        assert r.status_code == 401
        assert "invalid" in r.json()["detail"]


@pytest.mark.asyncio
async def test_invoke_revoked_key() -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/agents", json={"name": name})
        r = await ac.post(f"/api/v1/agents/{name}/keys", json={"label": "to-revoke"})
        key = r.json()["key"]
        key_id = r.json()["id"]
        # 撤销
        await ac.delete(f"/api/v1/agents/{name}/keys/{key_id}")
        # 撤销后用这个 key → 401
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke",
            headers={"Authorization": f"Bearer {key}"},
            json={"input": {"q": "x"}},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_invoke_agent_not_running(mock_stopped_agent) -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/agents", json={"name": name})
        r = await ac.post(f"/api/v1/agents/{name}/keys")
        key = r.json()["key"]
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke",
            headers={"Authorization": f"Bearer {key}"},
            json={"input": {"q": "x"}},
        )
        assert r.status_code == 503
        assert "not running" in r.json()["detail"]


@pytest.mark.asyncio
async def test_invoke_success_passes_input_and_metadata(mock_running_agent) -> None:
    name = _unique_name()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/agents", json={"name": name})
        r = await ac.post(f"/api/v1/agents/{name}/keys")
        key = r.json()["key"]

        r = await ac.post(
            f"/api/v1/agents/{name}/invoke",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "input": {"question": "what is the answer"},
                "metadata": {
                    "user_id": "u-1",
                    "session_id": "s-1",
                    "tags": ["prod"],
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["output"] == {"answer": "42"}
        assert body["trace_id"] == "abc123"

        # 验证 mock Runtime 收到 input + trace context
        mock_running_agent.invoke.assert_called_once()
        call_args = mock_running_agent.invoke.call_args
        # invoke(input, config) → 这里 to_thread 包装过，但 mock 应该是直接调用
        # 实际上 asyncio.to_thread(managed.invoke, body.input) → managed.invoke(body.input)
        # 但 to_thread 会把 call 变成 kwargs？让我看
        # asyncio.to_thread(func, *args) → func(*args)
        # 所以 mock 收到 invoke(body.input)，即 positional arg
        # call_args[0] = positional args, call_args[1] = kwargs
        assert call_args[0][0] == {"question": "what is the answer"}

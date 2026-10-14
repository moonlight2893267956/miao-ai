"""
多轮会话（Conversation）集成测试（mock Runtime，不真启动子进程）。

覆盖：
- 会话 CRUD、API Key 隔离、401 / 403 / 404 / 410 / 422 / 429 / 409 语义
- 轮次记账：先落 running 占位，执行完回写 succeeded / failed
- 历史注入：契约 opt-in（conversation.enabled）、轮次裁剪、字节裁剪、永不裁首轮
- 兼容底线：不传 conversation_id 时 config 与响应体与改造前逐字一致
- 流式：补发 conversation 事件 + 轮次收尾
- 异步：worker 线程回写轮次 + webhook 载荷带 conversation_id / turn
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.main import app
from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.conversation import Conversation, ConversationTurn
from app.runtime.registry import AgentRegistry
from app.services import conversation as conversation_service
from app.services.task_worker import TaskWorker

MOCK_OUTPUT = {"answer": "42"}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def mock_running_agent():
    mock_managed = MagicMock()
    mock_managed.status = "running"
    mock_managed.invoke.return_value = {"output": MOCK_OUTPUT, "trace_id": "abc123"}
    AgentRegistry._instance = MagicMock()
    AgentRegistry._instance.get.return_value = mock_managed
    yield mock_managed
    AgentRegistry._instance = None


@pytest.fixture(autouse=True)
def _reset_gates():
    """进程内会话闸门是模块级状态，用例之间必须清干净。"""
    conversation_service.reset_gates_for_tests()
    yield
    conversation_service.reset_gates_for_tests()


@pytest.fixture
def async_worker():
    """ASGITransport 不跑 lifespan，这里手动挂上异步任务 worker。"""
    worker = TaskWorker(max_workers=1)
    app.state.task_worker = worker
    yield worker
    worker.shutdown()


async def _new_agent_with_key(client: AsyncClient) -> tuple[str, str, uuid.UUID]:
    """建 agent + 建 key，返回 (name, 明文 key, key_id)。"""
    name = f"test-conv-{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/v1/agents", json={"name": name})
    assert r.status_code in (200, 201), r.text
    r = await client.post(f"/api/v1/agents/{name}/keys")
    assert r.status_code == 201, r.text
    return name, r.json()["key"], uuid.UUID(r.json()["id"])


async def _create_conversation(
    client: AsyncClient, name: str, key: str, **payload
) -> str:
    r = await client.post(
        f"/api/v1/agents/{name}/conversations", headers=_auth(key), json=payload or None
    )
    assert r.status_code == 201, r.text
    return r.json()["conversation_id"]


async def _invoke(
    client: AsyncClient,
    name: str,
    key: str,
    payload: dict,
    conversation_id: str | None = None,
):
    body: dict = {"input": payload}
    if conversation_id is not None:
        body["metadata"] = {"conversation_id": conversation_id}
    return await client.post(
        f"/api/v1/agents/{name}/invoke", headers=_auth(key), json=body
    )


async def _activate_contract(agent_name: str, contract: dict) -> None:
    """直接落一条 active 版本（带契约），避免走上传 ⇒ COS ⇒ 激活的完整链路。"""
    async with AsyncSessionLocal() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.name == agent_name))
        ).scalar_one()
        session.add(
            AgentVersion(
                agent_id=agent.id,
                version=f"v-{uuid.uuid4().hex[:6]}",
                artifact_url="local://test",
                entrypoint="agent:invoke",
                contract=contract,
                is_active=True,
                status="running",
            )
        )
        await session.commit()


async def _load_turns(conversation_id: str) -> list[ConversationTurn]:
    internal_id = conversation_service.parse_conversation_id(conversation_id)
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(ConversationTurn)
            .where(ConversationTurn.conversation_id == internal_id)
            .order_by(ConversationTurn.turn_index)
        )
        return list(rows.scalars().all())


ENABLED_CONTRACT = {"api_version": 1, "conversation": {"enabled": True, "max_history_turns": 10}}


# ===== 会话 CRUD =====


@pytest.mark.asyncio
async def test_conversation_crud_roundtrip(logged_in_client: AsyncClient) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(
            f"/api/v1/agents/{name}/conversations",
            headers=_auth(key),
            json={"metadata": {"source": "pytest"}, "ttl_seconds": 600},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["agent_name"] == name
        assert body["turn_count"] == 0
        assert body["conversation_id"].startswith("conv_")
        assert body["expires_at"] is not None
        cid = body["conversation_id"]

        # 详情（含全部轮次）
        r = await ac.get(f"/api/v1/agents/{name}/conversations/{cid}", headers=_auth(key))
        assert r.status_code == 200
        assert r.json()["metadata"] == {"source": "pytest"}
        assert r.json()["turns"] == []

        # 列表：total + items
        r = await ac.get(f"/api/v1/agents/{name}/conversations", headers=_auth(key))
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["conversation_id"] == cid

        # 省略请求体也能建（平台默认 TTL）
        r = await ac.post(f"/api/v1/agents/{name}/conversations", headers=_auth(key))
        assert r.status_code == 201
        assert r.json()["expires_at"] is not None


@pytest.mark.asyncio
async def test_conversation_requires_valid_key(logged_in_client: AsyncClient) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(f"/api/v1/agents/{name}/conversations")
        assert r.status_code == 401
        r = await ac.post(
            f"/api/v1/agents/{name}/conversations", headers=_auth("miao_wrong")
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_conversation_isolated_between_api_keys(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    name, key1, _ = await _new_agent_with_key(logged_in_client)
    r = await logged_in_client.post(f"/api/v1/agents/{name}/keys")
    key2 = r.json()["key"]
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key1)
        # 另一个 key：详情 / 删除 一律 403，列表里看不到
        r = await ac.get(f"/api/v1/agents/{name}/conversations/{cid}", headers=_auth(key2))
        assert r.status_code == 403
        r = await ac.delete(f"/api/v1/agents/{name}/conversations/{cid}", headers=_auth(key2))
        assert r.status_code == 403
        r = await ac.get(f"/api/v1/agents/{name}/conversations", headers=_auth(key2))
        assert r.json()["total"] == 0
        # invoke 也不能借用别人的会话
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke",
            headers=_auth(key2),
            json={"input": {}, "metadata": {"conversation_id": cid}},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_conversation_ttl_limit(logged_in_client: AsyncClient) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(
            f"/api/v1/agents/{name}/conversations",
            headers=_auth(key),
            json={"ttl_seconds": settings.conversation_max_ttl_seconds + 1},
        )
        assert r.status_code == 422
        assert "ttl_seconds" in r.json()["detail"]


@pytest.mark.asyncio
async def test_conversation_detail_404_and_delete_cascades(
    logged_in_client: AsyncClient,
) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        # 格式非法 / 不存在 都是 404（不泄露内部信息）
        r = await ac.get(f"/api/v1/agents/{name}/conversations/conv_zzz", headers=_auth(key))
        assert r.status_code == 404
        r = await ac.get(
            f"/api/v1/agents/{name}/conversations/conv_{uuid.uuid4().hex}", headers=_auth(key)
        )
        assert r.status_code == 404

        cid = await _create_conversation(ac, name, key)
        internal_id = conversation_service.parse_conversation_id(cid)
        async with AsyncSessionLocal() as session:
            session.add(
                ConversationTurn(
                    conversation_id=internal_id,
                    turn_index=1,
                    input={"q": "hi"},
                    status="succeeded",
                    output={"a": "hello"},
                )
            )
            await session.commit()

        r = await ac.delete(f"/api/v1/agents/{name}/conversations/{cid}", headers=_auth(key))
        assert r.status_code == 204
        r = await ac.get(f"/api/v1/agents/{name}/conversations/{cid}", headers=_auth(key))
        assert r.status_code == 404
        # 轮次随会话级联删除
        async with AsyncSessionLocal() as session:
            left = await session.execute(
                select(ConversationTurn).where(
                    ConversationTurn.conversation_id == internal_id
                )
            )
            assert list(left.scalars().all()) == []


# ===== 兼容底线 =====


@pytest.mark.asyncio
async def test_invoke_without_conversation_is_unchanged(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await _invoke(ac, name, key, {"question": "x"})
        assert r.status_code == 200
        body = r.json()
        # 响应体与改造前完全一致：没有 conversation_id / turn / history_truncated
        assert set(body) == {"output", "trace_id"}
        assert body["output"] == MOCK_OUTPUT
        # config 里也不得出现 conversation（等价于逐字未变）
        config = mock_running_agent.invoke.call_args[0][2]
        assert set(config) == set()


@pytest.mark.asyncio
async def test_invoke_without_conversation_keeps_trace_config(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke",
            headers=_auth(key),
            json={
                "input": {"q": "x"},
                "metadata": {"user_id": "u-1", "session_id": "s-1", "tags": ["prod"]},
            },
        )
        assert r.status_code == 200
        assert set(r.json()) == {"output", "trace_id"}
        config = mock_running_agent.invoke.call_args[0][2]
        assert config == {
            "langfuse_user_id": "u-1",
            "langfuse_session_id": "s-1",
            "langfuse_tags": ["prod"],
        }


# ===== 轮次记账与历史注入 =====


@pytest.mark.asyncio
async def test_invoke_conversation_records_turns_and_injects_history(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    await _activate_contract(name, ENABLED_CONTRACT)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)

        # 第 1 轮：没有历史可注入
        r = await _invoke(ac, name, key, {"char": "看"}, cid)
        assert r.status_code == 200, r.text
        assert set(r.json()) == {
            "output",
            "trace_id",
            "conversation_id",
            "turn",
            "history_truncated",
            "injected_turns",
        }
        assert r.json()["conversation_id"] == cid
        assert r.json()["turn"] == 1
        assert r.json()["history_truncated"] is False
        assert r.json()["injected_turns"] == []
        injected = mock_running_agent.invoke.call_args[0][2]["conversation"]
        assert injected["id"] == cid
        assert injected["turn"] == 1
        assert injected["turns"] == []
        assert injected["dropped_turns"] == 0

        # 第 2 轮：注入第 1 轮的 input / output
        r = await _invoke(ac, name, key, {"question": "为什么第 2 位是目"}, cid)
        assert r.status_code == 200, r.text
        assert r.json()["turn"] == 2
        assert r.json()["injected_turns"] == [1]
        injected = mock_running_agent.invoke.call_args[0][2]["conversation"]
        assert injected["turn"] == 2
        assert injected["max_turns_injected"] == 10
        assert injected["turns"] == [
            {"index": 1, "input": {"char": "看"}, "output": MOCK_OUTPUT}
        ]

        # 落库：两轮都是 succeeded，且带 output / trace_id
        turns = await _load_turns(cid)
        assert [t.turn_index for t in turns] == [1, 2]
        assert all(t.status == "succeeded" for t in turns)
        assert all(t.output == MOCK_OUTPUT for t in turns)
        assert all(t.trace_id == "abc123" for t in turns)


@pytest.mark.asyncio
async def test_invoke_conversation_without_declaration_records_only(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    """未声明 conversation.enabled 的 agent：只记账、不注入（历史零开销）。"""
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        r = await _invoke(ac, name, key, {"char": "看"}, cid)
        assert r.status_code == 200
        assert r.json()["turn"] == 1
        assert r.json()["history_truncated"] is False
        assert r.json()["injected_turns"] == []   # 未声明能力 → 没有注入
        assert "conversation" not in mock_running_agent.invoke.call_args[0][2]
        turns = await _load_turns(cid)
        assert len(turns) == 1 and turns[0].status == "succeeded"


@pytest.mark.asyncio
async def test_invoke_failure_records_failed_turn(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    mock_running_agent.invoke.side_effect = RuntimeError("boom")
    name, key, _ = await _new_agent_with_key(logged_in_client)
    await _activate_contract(name, ENABLED_CONTRACT)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        r = await _invoke(ac, name, key, {"char": "看"}, cid)
        assert r.status_code == 502
        turns = await _load_turns(cid)
        assert len(turns) == 1
        assert turns[0].status == "failed"
        assert turns[0].output is None
        # 失败轮次不计入后续注入（没有可用 output）
        mock_running_agent.invoke.side_effect = None
        r = await _invoke(ac, name, key, {"char": "牡"}, cid)
        assert r.status_code == 200
        assert mock_running_agent.invoke.call_args[0][2]["conversation"]["turns"] == []


@pytest.mark.asyncio
async def test_conversation_turn_limit_429(
    mock_running_agent, logged_in_client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "conversation_max_turns", 1)
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        assert (await _invoke(ac, name, key, {"char": "看"}, cid)).status_code == 200
        r = await _invoke(ac, name, key, {"char": "牡"}, cid)
        assert r.status_code == 429
        assert "max turns" in r.json()["detail"]


@pytest.mark.asyncio
async def test_conversation_concurrent_turn_409(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        gate_key = str(conversation_service.parse_conversation_id(cid))
        gate = conversation_service.acquire_conversation_gate(gate_key)
        assert gate is not None
        try:
            r = await _invoke(ac, name, key, {"char": "看"}, cid)
            assert r.status_code == 409
            assert "concurrent" in r.json()["detail"]
        finally:
            conversation_service.release_conversation_gate(gate_key, gate)
        # 闸门释放后恢复正常
        assert (await _invoke(ac, name, key, {"char": "看"}, cid)).status_code == 200


@pytest.mark.asyncio
async def test_expired_conversation_410(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    name, key, key_id = await _new_agent_with_key(logged_in_client)
    async with AsyncSessionLocal() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.name == name))
        ).scalar_one()
        expired = Conversation(
            agent_id=agent.id,
            api_key_id=key_id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(expired)
        await session.commit()
        cid = conversation_service.format_conversation_id(expired.id)

    async with _client() as ac:
        r = await _invoke(ac, name, key, {"char": "看"}, cid)
        assert r.status_code == 410
        assert "expired" in r.json()["detail"]
        # 读详情不做过期拦截：历史仍可审计
        r = await ac.get(f"/api/v1/agents/{name}/conversations/{cid}", headers=_auth(key))
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_unknown_conversation_id_404(mock_running_agent, logged_in_client: AsyncClient) -> None:
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        for bad in ("conv_zzz", f"conv_{uuid.uuid4().hex}", ""):
            r = await _invoke(ac, name, key, {"char": "看"}, bad)
            assert r.status_code == 404, f"bad={bad!r} got {r.status_code}"
        # 非字符串同样按 404 处理
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke",
            headers=_auth(key),
            json={"input": {}, "metadata": {"conversation_id": 123}},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_history_truncated_by_turns_keeps_first_turn(
    mock_running_agent, logged_in_client: AsyncClient, monkeypatch
) -> None:
    """轮数裁剪：丢最旧的，但第 1 轮（锚点）永远保留。"""
    monkeypatch.setattr(settings, "conversation_default_max_history_turns", 2)
    name, key, _ = await _new_agent_with_key(logged_in_client)
    await _activate_contract(name, {"conversation": {"enabled": True}})
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        for index in range(1, 5):
            r = await _invoke(ac, name, key, {"char": f"c{index}"}, cid)
            assert r.status_code == 200, r.text

        # 第 4 轮的候选历史是 1/2/3 轮，上限 2 → 保留 [1, 3]（首轮是锚点）
        injected = mock_running_agent.invoke.call_args[0][2]["conversation"]
        assert injected["turn"] == 4
        assert [t["index"] for t in injected["turns"]] == [1, 3]
        assert injected["dropped_turns"] == 1
        assert injected["truncated"] is True
        assert r.json()["history_truncated"] is True
        # 注入明细（裁剪后带缺号）：UI 据此显示「本轮 agent 实际看到哪几轮」
        assert r.json()["injected_turns"] == [1, 3]
        assert injected["turns"][0]["input"] == {"char": "c1"}


@pytest.mark.asyncio
async def test_history_truncated_by_bytes(
    mock_running_agent, logged_in_client: AsyncClient, monkeypatch
) -> None:
    """字节裁剪：超出上限时继续丢旧轮次，只留第 1 轮。"""
    monkeypatch.setattr(settings, "conversation_max_history_bytes", 1)
    name, key, _ = await _new_agent_with_key(logged_in_client)
    await _activate_contract(name, ENABLED_CONTRACT)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        for index in range(1, 4):
            assert (await _invoke(ac, name, key, {"char": f"c{index}"}, cid)).status_code == 200
        # 第 3 轮的候选历史是 1/2 轮，字节上限 1 字节 → 只剩锚点
        injected = mock_running_agent.invoke.call_args[0][2]["conversation"]
        assert [t["index"] for t in injected["turns"]] == [1]
        assert injected["dropped_turns"] == 1
        assert injected["truncated"] is True


# ===== 流式 / 异步 =====


@pytest.mark.asyncio
async def test_invoke_stream_with_conversation(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    async def _agent_stream():
        for line in (
            "event: output",
            'data: {"answer": "42"}',
            "",
            "event: done",
            'data: {"trace_id": "t-stream", "latency_ms": 5}',
            "",
        ):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()

    name, key, _ = await _new_agent_with_key(logged_in_client)
    await _activate_contract(name, ENABLED_CONTRACT)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke/stream",
            headers=_auth(key),
            json={"input": {"char": "看"}, "metadata": {"conversation_id": cid}},
        )
        assert r.status_code == 200, r.text
        assert "event: conversation" in r.text
        assert f'"conversation_id": "{cid}"' in r.text
        # 流式也要回报注入明细（首轮为空）
        assert '"injected_turns": []' in r.text
        # 原始事件照常转发
        assert "event: done" in r.text

        turns = await _load_turns(cid)
        assert len(turns) == 1
        assert turns[0].status == "succeeded"
        assert turns[0].output == MOCK_OUTPUT
        assert turns[0].trace_id == "t-stream"

        # config 里同样注入了会话（第 2 轮）
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke/stream",
            headers=_auth(key),
            json={"input": {"char": "牡"}, "metadata": {"conversation_id": cid}},
        )
        assert r.status_code == 200
        # manager.invoke_stream(payload, config, timeout=...) → config 是第 2 个位置参数
        config = mock_running_agent.invoke_stream.call_args[0][1]
        assert config["conversation"]["turn"] == 2
        assert config["stream"] is True


@pytest.mark.asyncio
async def test_invoke_stream_token_agent_records_structured_output(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    """逐 token 流的 agent：结构化结果随 done 事件下发，轮次照样记录完整 output。

    否则流式跑的轮次在会话历史里 output 为空，下一轮追问会失去依据。
    """

    async def _agent_stream():
        for line in (
            "event: token",
            'data: {"token": "第一段"}',
            "",
            "event: token",
            'data: {"token": "第二段"}',
            "",
            "event: done",
            'data: {"trace_id": "t-token", "latency_ms": 9, '
            '"output": {"answer": "拼好的结构化结果", "positions": [1, 2]}}',
            "",
        ):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke/stream",
            headers=_auth(key),
            json={"input": {"char": "看"}, "metadata": {"conversation_id": cid}},
        )
        assert r.status_code == 200, r.text
        # 正文逐段转发（前端据此做打字机）
        assert r.text.count("event: token") == 2
        assert "第一段" in r.text and "第二段" in r.text

        turns = await _load_turns(cid)
        assert len(turns) == 1
        assert turns[0].status == "succeeded"
        assert turns[0].output == {"answer": "拼好的结构化结果", "positions": [1, 2]}
        assert turns[0].trace_id == "t-token"


@pytest.mark.asyncio
async def test_invoke_stream_without_conversation_unchanged(
    mock_running_agent, logged_in_client: AsyncClient
) -> None:
    async def _agent_stream():
        for line in ("event: token", 'data: "hi"', "", "event: done", "data: {}", ""):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke/stream",
            headers=_auth(key),
            json={"input": {"char": "看"}},
        )
        assert r.status_code == 200
        assert "conversation" not in r.text
        assert mock_running_agent.invoke_stream.call_args[0][1] == {"stream": True}


@pytest.mark.asyncio
async def test_invoke_async_with_conversation(
    mock_running_agent, logged_in_client: AsyncClient, async_worker, monkeypatch
) -> None:
    monkeypatch.setattr(TaskWorker, "_post_webhook", lambda *a, **k: True)
    name, key, _ = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        cid = await _create_conversation(ac, name, key)
        r = await ac.post(
            f"/api/v1/agents/{name}/invoke/async",
            headers=_auth(key),
            json={
                "input": {"char": "看"},
                "metadata": {"conversation_id": cid},
                "webhook_url": "http://example.invalid/hook",
                "timeout": 30,
            },
        )
        assert r.status_code == 202, r.text
        request_id = r.json()["request_id"]

        # 等 worker 线程回写（DB 步骤回主循环执行，见 task_worker 模块注释）
        turns = []
        for _ in range(100):
            turns = await _load_turns(cid)
            if turns and turns[0].status != "running":
                break
            await asyncio.sleep(0.05)
        assert len(turns) == 1, "异步任务的轮次没有落库"
        assert turns[0].status == "succeeded"
        assert turns[0].output == MOCK_OUTPUT
        # 闸门必须已释放，否则该会话会一直被 409 卡住
        assert conversation_service.acquire_conversation_gate(
            str(conversation_service.parse_conversation_id(cid))
        ) is not None

        # InvokeTask 也走到了终态（顺带覆盖 Uuid 列不能绑定 str 的老坑）
        r = await ac.get(
            f"/api/v1/agents/{name}/invoke/async/{request_id}", headers=_auth(key)
        )
        assert r.status_code == 200
        assert r.json()["status"] == "success"
        assert r.json()["output"] == MOCK_OUTPUT

"""平台侧执行事件透传测试 —— 把「隐式契约」变成**有测试的契约**。

现状：``backend/app/api/invoke.py`` 对未知事件名是 ``yield line + "\\n"`` 全量转发，
所以 ``event: exec`` 今天能通是**侥幸成立**的。将来若有人给事件名加白名单，
执行事件会静默断掉，线上只表现为「上游看不到工具调用」——很难归因。

本文件刻意**不依赖任何具体 agent**：mock Runtime 的 ``invoke_stream`` 直接产出
SSE 行。被测的是平台行为：

* ``event: exec`` 逐行原样透传给调用方（含中文、含对象入参出参）；
* 平台**不解释语义**：自定义 kind 照常透传；
* 事件不影响轮次记账：该轮 output 仍来自 ``done.output``；
* 不带会话时 config 仍是 ``{"stream": True}``（不引入新键）。
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.runtime.registry import AgentRegistry

pytestmark = pytest.mark.asyncio


def _unique_name() -> str:
    return f"invoke-{uuid.uuid4().hex[:8]}"


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def mock_running_agent():
    from unittest.mock import MagicMock

    mock_managed = MagicMock()
    mock_managed.status = "running"
    mock_managed.invoke.return_value = {"output": {"answer": "42"}, "trace_id": "abc123"}
    AgentRegistry._instance = MagicMock()
    AgentRegistry._instance.get.return_value = mock_managed
    yield mock_managed
    AgentRegistry._instance = None


async def _new_agent_with_key(client: AsyncClient) -> tuple[str, str]:
    name = _unique_name()
    await client.post("/api/v1/agents", json={"name": name})
    r = await client.post(f"/api/v1/agents/{name}/keys", json={})
    assert r.status_code in (200, 201), r.text
    return name, r.json()["key"]


async def test_exec_events_are_passed_through(mock_running_agent, logged_in_client):
    """agent 上报的执行事件必须原样到达调用方（平台只转发、不解释）。"""
    tool_call = ('data: {"seq": 1, "kind": "tool_call", "call_id": "c1", '
                 '"name": "tavily.search", "input": {"query": "编 五笔编码"}}')
    tool_result = ('data: {"seq": 2, "kind": "tool_result", "call_id": "c1", '
                   '"name": "tavily.search", "ok": true, "ms": 4271, '
                   '"output": {"results": 5}}')

    async def _agent_stream():
        for line in (
            "event: exec", tool_call, "",
            "event: exec", tool_result, "",
            "event: token", 'data: {"token": "正文"}', "",
            "event: done",
            'data: {"trace_id": "t-exec", "latency_ms": 12, "output": {"code": "xyna"}}',
            "",
        ):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()

    name, key = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(f"/api/v1/agents/{name}/invoke/stream",
                          headers=_auth(key), json={"input": {"char": "编"}})

    assert r.status_code == 200, r.text
    assert r.text.count("event: exec") == 2
    # 中文不转义、结构不被改写（平台不解释事件内容）
    assert '"kind": "tool_call"' in r.text
    assert '"query": "编 五笔编码"' in r.text
    assert '"call_id": "c1"' in r.text
    assert '"ms": 4271' in r.text
    # 正文与 done 不受影响
    assert "event: token" in r.text
    assert '"code": "xyna"' in r.text


async def test_custom_kinds_are_forwarded(mock_running_agent, logged_in_client):
    """平台不定义 kind 白名单：agent 自定义类型照常透传。"""

    async def _agent_stream():
        for line in (
            "event: exec",
            'data: {"kind": "retrieval", "hits": [{"url": "u1"}]}',
            "",
            "event: done", "data: {}", "",
        ):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()

    name, key = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(f"/api/v1/agents/{name}/invoke/stream",
                          headers=_auth(key), json={"input": {}})

    assert '"kind": "retrieval"' in r.text
    assert '"hits": [{"url": "u1"}]' in r.text


async def test_exec_does_not_change_stream_config(mock_running_agent, logged_in_client):
    """不带会话时 config 仍是 {"stream": True} —— 执行事件能力不往 config 加新键。

    这条守着「老调用方零影响」：agent 收到什么，改造前后必须一致。
    """

    async def _agent_stream():
        for line in ("event: exec", 'data: {"kind": "log", "text": "x"}', "",
                     "event: done", "data: {}", ""):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()

    name, key = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        await ac.post(f"/api/v1/agents/{name}/invoke/stream",
                      headers=_auth(key), json={"input": {}})

    assert mock_running_agent.invoke_stream.call_args[0][1] == {"stream": True}


async def test_exec_does_not_pollute_turn_output(mock_running_agent, logged_in_client):
    """执行事件是辅助信息：会话轮次的 output 仍然只来自 done.output。"""

    async def _agent_stream():
        for line in (
            "event: exec",
            'data: {"kind": "tool_call", "call_id": "c1", "name": "db.query"}',
            "",
            "event: done",
            ('data: {"trace_id": "t-p", "latency_ms": 1, '
             '"output": {"code": "xyna", "positions": []}}'),
            "",
        ):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()

    name, key = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        cid_r = await ac.post(f"/api/v1/agents/{name}/conversations", headers=_auth(key), json={})
        assert cid_r.status_code == 201, cid_r.text
        cid = cid_r.json()["conversation_id"]
        r = await ac.post(f"/api/v1/agents/{name}/invoke/stream",
                          headers=_auth(key),
                          json={"input": {"char": "编"}, "metadata": {"conversation_id": cid}})
        assert r.status_code == 200, r.text

        turns_r = await ac.get(f"/api/v1/agents/{name}/conversations/{cid}", headers=_auth(key))

    assert turns_r.status_code == 200, turns_r.text
    turns = turns_r.json()["turns"]
    assert len(turns) == 1
    assert turns[0]["output"] == {"code": "xyna", "positions": []}
    assert "call_id" not in str(turns[0]["output"])


async def test_unknown_events_are_forwarded_not_swallowed(mock_running_agent, logged_in_client):
    """未知事件名一律透传（客户端自己决定忽略）。

    守卫的是「将来给事件加白名单」这类改动：一旦平台开始过滤，上游会静默丢失
    它能处理的新事件。
    """

    async def _agent_stream():
        for line in ("event: something-new", 'data: {"x": 1}', "", "event: done", "data: {}", ""):
            yield line

    mock_running_agent.invoke_stream.side_effect = lambda *a, **k: _agent_stream()

    name, key = await _new_agent_with_key(logged_in_client)
    async with _client() as ac:
        r = await ac.post(f"/api/v1/agents/{name}/invoke/stream",
                          headers=_auth(key), json={"input": {}})

    assert "event: something-new" in r.text
    assert 'data: {"x": 1}' in r.text

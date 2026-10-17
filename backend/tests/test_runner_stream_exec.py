"""runner 的**通用**流式协议测试：``{"token"}`` / ``{"exec"}`` / ``{"output"}`` 三种块。

这里刻意**不用任何真实 agent**：临时目录里生成一个最小的 agent.py，用
``importlib`` + 打补丁的 ``sys.argv`` 加载 runner（与平台真实启动方式一致：
``python miao_runner.py <agent_dir> agent:invoke <port>``）。
被测的是**平台协议**，不是某个 agent 的业务 —— 任何 agent 接入后都受这套约定保护。

覆盖：
* 执行事件块 → ``event: exec``（独立事件名，字段原样透传）；
* 平台**不解释语义**：自定义 kind / 自定义字段照常透传；
* 事件**不进** ``done.output``（上游丢弃事件也能拿到结果）；
* 只 yield ``{"token"}`` 的老 agent → 输出里没有 exec，正文逐条转发（零回归）；
* 判断顺序：``{"token","exec"}`` / ``{"output","exec"}`` 按 token / 收尾块处理；
* 每轮开始重置 SDK 状态（上一轮残留不影响本轮）；
* sync 与 async generator 两个分支行为一致；
* SDK 文件缺失时 runner 仍能启动（软导入兜底）。
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.no_db

RUNNER_PATH = Path(__file__).parents[1] / "agent_templates" / "miao_runner.py"
SDK_DIR = RUNNER_PATH.parent

#: macOS 系统代理常含 `[::1]` 条目，httpx 解析 proxy mount 时会抛
#: `InvalidURL: Invalid port: ':1]'`。runner 加载时会初始化 Langfuse（内部建
#: httpx client），于是**模块导入即失败**。这是本机环境问题而非被测逻辑。
_PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


@pytest.fixture(autouse=True)
def _no_broken_system_proxy(monkeypatch):
    for name in _PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _load_runner(agent_dir: Path, monkeypatch, port: int = 9911):
    """按平台的方式加载 runner 模块（``sys.argv`` 三个位置参数 + 真实 importlib 流程）。"""
    monkeypatch.setattr(sys, "argv",
                        ["miao_runner.py", str(agent_dir), "agent:invoke", str(port)])
    spec = importlib.util.spec_from_file_location(f"runner_under_test_{port}", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_agent(tmp_path: Path, monkeypatch, body: str):
    agent_dir = tmp_path / "fixture-agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return _load_runner(agent_dir, monkeypatch)


STREAMING_AGENT = '''
    def invoke(input, config=None):
        """逐块流式 agent：执行事件 + 正文 + 收尾块。"""
        def gen():
            yield {"exec": {"seq": 1, "kind": "tool_call", "call_id": "c1",
                            "name": "db.query", "input": {"table": "codes"}}}
            yield {"exec": {"seq": 2, "kind": "tool_result", "call_id": "c1",
                            "name": "db.query", "ok": True, "ms": 42,
                            "output": {"rows": 3}}}
            yield {"token": "正文一"}
            yield {"token": "正文二"}
            yield {"output": {"answer": "结构化结果"}}
        if (config or {}).get("stream"):
            return gen()
        return {"answer": "结构化结果"}
'''


def _stream(client: TestClient):
    return client.post("/invoke/stream", json={"input": {}, "config": {"stream": True}})


def test_exec_chunks_become_exec_events(tmp_path, monkeypatch):
    module = _make_agent(tmp_path, monkeypatch, STREAMING_AGENT)

    with TestClient(module.app) as client:
        r = _stream(client)

    assert r.status_code == 200
    assert r.text.count("event: exec") == 2
    # 字段原样透传（含对象入参 / 出参、中文不转义）
    assert '"kind": "tool_call"' in r.text
    assert '"input": {"table": "codes"}' in r.text
    assert '"call_id": "c1"' in r.text
    assert '"ok": true' in r.text
    assert '"ms": 42' in r.text
    # 正文与收尾块不受影响
    assert r.text.count("event: token") == 2
    assert '"answer": "结构化结果"' in r.text


def test_exec_does_not_pollute_done_output(tmp_path, monkeypatch):
    """上游丢弃全部执行事件，也必须能正常拿到结构化结果。"""
    module = _make_agent(tmp_path, monkeypatch, STREAMING_AGENT)

    with TestClient(module.app) as client:
        r = _stream(client)

    done_line = [ln for ln in r.text.splitlines()
                 if ln.startswith("data:") and '"latency_ms"' in ln]
    assert len(done_line) == 1
    assert '"exec"' not in done_line[0]
    assert '"call_id"' not in done_line[0]


def test_platform_does_not_interpret_exec_semantics(tmp_path, monkeypatch):
    """**通用性保证**：任何 agent、任何自定义 kind 与字段都原样透传。

    平台只认「这是个 dict」，不校验 kind 白名单 —— 否则「通用能力」就变成
    「只服务某个 agent 的定制功能」。
    """
    module = _make_agent(tmp_path, monkeypatch, '''
        def invoke(input, config=None):
            def gen():
                yield {"exec": {"kind": "retrieval", "query": "五笔 编码",
                                "hits": [{"url": "u1", "score": 0.9}]}}
                yield {"exec": {"kind": "my-own-thing", "whatever": {"nested": [1, 2]}}}
                yield {"output": {"ok": True}}
            if (config or {}).get("stream"):
                return gen()
            return {"ok": True}
    ''')

    with TestClient(module.app) as client:
        r = _stream(client)

    assert r.text.count("event: exec") == 2
    assert '"kind": "retrieval"' in r.text
    assert '"kind": "my-own-thing"' in r.text
    assert '"whatever": {"nested": [1, 2]}' in r.text


def test_legacy_token_only_agent_unchanged(tmp_path, monkeypatch):
    """零回归：只 yield token 的老 agent，输出里不许多出任何一条 exec。"""
    module = _make_agent(tmp_path, monkeypatch, '''
        def invoke(input, config=None):
            def gen():
                yield {"token": "hi"}
                yield {"token": "there"}
            if (config or {}).get("stream"):
                return gen()
            return {"ok": True}
    ''')

    with TestClient(module.app) as client:
        r = _stream(client)

    assert "event: exec" not in r.text
    assert r.text.count("event: token") == 2


def test_chunk_with_token_and_exec_is_token(tmp_path, monkeypatch):
    """判断顺序：含 token / output 的块优先，不当执行事件。"""
    module = _make_agent(tmp_path, monkeypatch, '''
        def invoke(input, config=None):
            def gen():
                yield {"token": "正文", "exec": {"kind": "x"}}
                yield {"output": {"ok": True}, "exec": {"kind": "y"}}
            if (config or {}).get("stream"):
                return gen()
            return {"ok": True}
    ''')

    with TestClient(module.app) as client:
        r = _stream(client)

    assert "event: exec" not in r.text
    assert '"token": "正文"' in r.text
    assert '"ok": true' in r.text


def test_async_generator_branch_also_forwards_exec(tmp_path, monkeypatch):
    module = _make_agent(tmp_path, monkeypatch, '''
        async def _gen():
            yield {"exec": {"kind": "thinking", "text": "异步思考"}}
            yield {"token": "异步正文"}
            yield {"output": {"answer": "async"}}

        def invoke(input, config=None):
            if (config or {}).get("stream"):
                return _gen()
            return {"answer": "async"}
    ''')

    with TestClient(module.app) as client:
        r = _stream(client)

    assert r.text.count("event: exec") == 1
    assert '"kind": "thinking"' in r.text
    assert '"token": "异步正文"' in r.text


def test_non_generator_agent_unchanged(tmp_path, monkeypatch):
    module = _make_agent(tmp_path, monkeypatch, '''
        def invoke(input, config=None):
            return {"answer": "42"}
    ''')

    with TestClient(module.app) as client:
        r = _stream(client)

    assert "event: exec" not in r.text
    assert "event: output" in r.text
    assert "event: done" in r.text


def test_sdk_state_is_reset_between_requests(tmp_path, monkeypatch):
    """上一轮残留不影响本轮：runner 每轮开始清 SDK（含 seq / call_id 计数器）。"""
    module = _make_agent(tmp_path, monkeypatch, '''
        import miao_sdk

        def invoke(input, config=None):
            def gen():
                with miao_sdk.tool("probe"):
                    pass
                yield {"output": {"ok": True}}
            if (config or {}).get("stream"):
                return gen()
            return {"ok": True}
    ''')

    with TestClient(module.app) as client:
        first = _stream(client)
        second = _stream(client)

    assert first.status_code == 200 and second.status_code == 200
    # 两轮的 call_id 都从 c1 开始（不是 c1 / c2 一路涨上去）
    assert '"call_id": "c1"' in first.text
    assert '"call_id": "c1"' in second.text


def test_sdk_is_importable_from_agent_without_packaging(tmp_path, monkeypatch):
    """任何 agent 都能 `import miao_sdk` —— SDK 由平台放在 runner 同目录。

    这是「通用基建」的关键前提：agent 不需要把 SDK 打进 zip，也不必写进 requirements.txt。
    """
    module = _make_agent(tmp_path, monkeypatch, '''
        import miao_sdk

        def invoke(input, config=None):
            return {"kinds": list(miao_sdk.EVENT_KINDS),
                    "chunk_key": miao_sdk.CHUNK_KEY}
    ''')

    with TestClient(module.app) as client:
        r = client.post("/invoke", json={"input": {}, "config": {}})

    assert r.status_code == 200
    out = r.json()["output"]
    assert out["kinds"] == ["thinking", "tool_call", "tool_result", "log"]
    assert out["chunk_key"] == "exec"


def test_runner_starts_without_sdk(tmp_path, monkeypatch):
    """SDK 文件缺失（旧镜像 / 手工部署漏拷）时 runner 必须照常启动。

    执行事件是锦上添花：少一个可选文件绝不能让 agent 起不来。
    """
    agent_dir = tmp_path / "no-sdk-agent"
    agent_dir.mkdir()
    isolated = tmp_path / "runner_only"
    isolated.mkdir()
    runner_copy = isolated / "miao_runner.py"
    runner_copy.write_text(RUNNER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (agent_dir / "agent.py").write_text(
        "def invoke(input, config=None):\n    return {'ok': True}\n", encoding="utf-8")

    # 本测试模块自身把 SDK 目录加进了 sys.path，会让「文件缺失」测不出来 —— 临时摘掉
    sdk_dir_resolved = SDK_DIR.resolve()
    monkeypatch.setattr(
        sys, "path",
        [p for p in sys.path if p and Path(p).resolve() != sdk_dir_resolved],
    )
    monkeypatch.delitem(sys.modules, "miao_sdk", raising=False)

    monkeypatch.setattr(sys, "argv", ["miao_runner.py", str(agent_dir), "agent:invoke", "9912"])
    spec = importlib.util.spec_from_file_location("runner_no_sdk", runner_copy)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # 不得抛

    assert module.miao_sdk is None
    with TestClient(module.app) as client:
        r = client.post("/invoke", json={"input": {}, "config": {}})
    assert r.status_code == 200
    assert r.json()["output"] == {"ok": True}

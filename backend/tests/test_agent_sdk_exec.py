"""Agent SDK（执行事件上报）单测 —— **平台通用能力**，与任何具体 agent 无关。

覆盖协议纪律本身：

* 没有 sink 时零开销、永不抛异常（非流式路径不许被污染）；
* sink 抛异常不拖垮主流程；
* sink 是**线程隔离**的（并发 invoke 不串味）；
* ``tool()`` 自动配对 ``tool_call`` / ``tool_result``（同 call_id）、自动计时、
  异常路径也成对收尾且**原样抛出**；
* 入参出参是**对象**（不是字符串化 JSON），按约定原样透传；
* ``seq`` 单调递增；``kind`` 不设白名单（自定义类型照常放行）；
* 体积上限生效且**显式标记** ``truncated``（悄悄截断比截断更危险）；
* ``stream()`` 能在阻塞动作**进行中**就把事件吐出来；
* 收尾块一定到达（含 work 抛异常的情况）。

用 ``-m no_db``：纯函数 + 线程，不碰数据库。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

# 与 agent 的视角一致：SDK 与 runner 同目录，脚本模式下 sys.path[0] 就是它
SDK_DIR = Path(__file__).parents[1] / "agent_templates"
sys.path.insert(0, str(SDK_DIR))

import miao_sdk  # noqa: E402

pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def _clean_sink():
    miao_sdk.clear()
    yield
    miao_sdk.clear()


def _capture() -> list[dict]:
    events: list[dict] = []
    miao_sdk.set_sink(events.append)
    return events


# ─── 纪律：零开销 / 永不抛 / 线程隔离 ──────────────────────────


def test_emit_without_sink_is_noop():
    """未注册 sink：不抛异常、无副作用（非流式路径的核心纪律）。"""
    assert miao_sdk.get_sink() is None
    miao_sdk.thinking("不该出现")
    miao_sdk.log("也不该出现")
    with miao_sdk.tool("tables.lookup", char="编"):
        pass
    assert miao_sdk.get_sink() is None


def test_emit_swallows_sink_errors():
    def boom(event):
        raise RuntimeError("sink 炸了")

    miao_sdk.set_sink(boom)
    miao_sdk.thinking("不得抛")          # 不抛
    miao_sdk.log("不得抛", level="warn")

    events = _capture()
    miao_sdk.thinking("恢复后仍可用")
    assert len(events) == 1


def test_sink_is_thread_isolated():
    """并发 invoke 各在自己的线程里注册 sink —— 不串味。"""
    got: dict[str, list] = {"a": [], "b": []}
    main_events: list[dict] = []

    def worker(name: str, started: threading.Event, release: threading.Event):
        miao_sdk.set_sink(got[name].append)
        started.set()
        release.wait(timeout=5)
        miao_sdk.thinking(name)

    miao_sdk.set_sink(main_events.append)
    a_started, b_started = threading.Event(), threading.Event()
    release = threading.Event()
    threads = [
        threading.Thread(target=worker, args=("a", a_started, release)),
        threading.Thread(target=worker, args=("b", b_started, release)),
    ]
    for t in threads:
        t.start()
    assert a_started.wait(timeout=5) and b_started.wait(timeout=5)
    release.set()
    for t in threads:
        t.join(timeout=5)

    assert [e["text"] for e in got["a"]] == ["a"]
    assert [e["text"] for e in got["b"]] == ["b"]
    assert main_events == []


def test_use_sink_restores_previous():
    outer, inner = [], []
    miao_sdk.set_sink(outer.append)

    with miao_sdk.use_sink(inner.append):
        miao_sdk.thinking("内层")
    miao_sdk.thinking("外层")

    assert [e["text"] for e in inner] == ["内层"]
    assert [e["text"] for e in outer] == ["外层"]


# ─── 事件形状：thinking / log / 自定义 kind ────────────────────


def test_thinking_event():
    events = _capture()
    miao_sdk.thinking("先查内置码表，再决定要不要联网")
    assert events[0]["kind"] == "thinking"
    assert events[0]["text"] == "先查内置码表，再决定要不要联网"
    assert events[0]["seq"] == 1


def test_log_event_levels_are_free_form():
    events = _capture()
    miao_sdk.log("未配置 key，走离线路径", level="warn")
    miao_sdk.log("自定义级别", level="trace-verbose")   # 平台不校验取值
    assert [e["level"] for e in events] == ["warn", "trace-verbose"]


def test_custom_kind_passes_through():
    """平台不定义语义的保证：任何自定义 kind 都原样透传。"""
    events = _capture()
    miao_sdk.emit({"kind": "retrieval", "query": "编 五笔", "hits": 3})
    assert events[0]["kind"] == "retrieval"
    assert events[0]["query"] == "编 五笔"
    assert events[0]["hits"] == 3


def test_event_without_kind_is_dropped():
    events = _capture()
    miao_sdk.emit({"text": "没有 kind 无法归类"})
    assert events == []


# ─── 工具调用：配对 / 计时 / 对象入参出参 ──────────────────────


def test_tool_call_and_result_are_paired():
    events = _capture()

    with miao_sdk.tool("tables.lookup", char="编") as t:
        t.output({"code": "xyna", "level": 1})

    assert len(events) == 2
    call, result = events
    assert call["kind"] == "tool_call"
    assert call["name"] == "tables.lookup"
    assert call["input"] == {"char": "编"}          # 对象，不是字符串化 JSON
    assert result["kind"] == "tool_result"
    assert result["call_id"] == call["call_id"]      # 配对键
    assert result["name"] == "tables.lookup"
    assert result["ok"] is True
    assert result["output"] == {"code": "xyna", "level": 1}   # 对象
    assert isinstance(result["ms"], int)


def test_tool_input_as_positional_dict():
    events = _capture()
    with miao_sdk.tool("tavily.search", {"query": "编 五笔编码", "depth": "advanced"}) as t:
        t.output({"results": 5})
    assert events[0]["input"] == {"query": "编 五笔编码", "depth": "advanced"}


def test_tool_call_ids_are_unique_and_monotonic():
    events = _capture()
    for name in ("a", "b", "c"):
        with miao_sdk.tool(name):
            pass
    calls = [e for e in events if e["kind"] == "tool_call"]
    ids = [e["call_id"] for e in calls]
    assert ids == ["c1", "c2", "c3"]
    assert len(set(ids)) == 3


def test_tool_failure_is_recorded_and_reraised():
    """工具抛异常：先如实记 ok=false 的 result，再**原样抛出**（SDK 不改错误语义）。"""
    events = _capture()

    with pytest.raises(ValueError, match="上游炸了"):
        with miao_sdk.tool("tavily.search", query="编"):
            raise ValueError("上游炸了")

    call, result = events
    assert call["kind"] == "tool_call"
    assert result["kind"] == "tool_result"
    assert result["call_id"] == call["call_id"]   # 异常路径也成对
    assert result["ok"] is False
    assert "上游炸了" in result["error"]


def test_tool_manual_fail_marks_result_not_ok():
    events = _capture()
    with miao_sdk.tool("tavily.search", query="编") as t:
        t.fail("HTTP 429")
    assert events[1]["ok"] is False
    assert events[1]["error"] == "HTTP 429"


def test_tool_result_measures_elapsed():
    events = _capture()
    with miao_sdk.tool("slow.tool"):
        time.sleep(0.05)
    assert events[1]["ms"] >= 40


# ─── seq / 体积上限 ───────────────────────────────────────────


def test_seq_is_monotonic_across_kinds():
    events = _capture()
    miao_sdk.thinking("a")
    with miao_sdk.tool("x"):
        pass
    miao_sdk.log("b", level="info")
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_long_text_is_capped_and_flagged():
    events = _capture()
    miao_sdk.thinking("长" * (miao_sdk._CAP_TEXT + 500))
    event = events[0]
    assert len(event["text"]) <= miao_sdk._CAP_TEXT
    assert event["text"].endswith("…")
    assert event["truncated"] is True


def test_large_output_is_capped_and_flagged():
    events = _capture()
    with miao_sdk.tool("tavily.search", query="编") as t:
        t.output({"raw": "x" * (miao_sdk._CAP_JSON + 5_000)})
    result = events[1]
    assert result["truncated"] is True
    # 不悄悄丢：给出可读的 preview 与省略字节数
    assert "preview" in result["output"]
    assert "omitted_bytes" in result["output"]


def test_long_error_is_capped():
    events = _capture()
    with miao_sdk.tool("x") as t:
        t.fail("错" * (miao_sdk._CAP_ERROR + 100))
    assert len(events[1]["error"]) <= miao_sdk._CAP_ERROR


def test_events_are_json_serializable():
    events = _capture()
    miao_sdk.thinking("思考")
    with miao_sdk.tool("tables.lookup", char="编") as t:
        t.output({"code": "xyna"})
    for event in events:
        json.dumps(event, ensure_ascii=False)   # 不得抛


# ─── stream()：线程化桥接 ─────────────────────────────────────


def test_stream_emits_events_while_work_is_still_blocking():
    """核心断言：事件在阻塞动作**期间**到达，而不是算完一次性到齐。"""
    blocking = 0.6

    def work() -> dict:
        with miao_sdk.tool("llm.decide", char="编") as t:
            time.sleep(blocking)          # 模拟真实的模型等待
            t.output({"positions": 4})
        return {"ok": True}

    started = time.monotonic()
    arrivals: list[tuple[str, float]] = []
    final = None
    for chunk in miao_sdk.stream(work):
        if "exec" in chunk:
            arrivals.append((chunk["exec"]["kind"], time.monotonic() - started))
        else:
            final = chunk["output"]

    assert final == {"ok": True}
    assert arrivals and arrivals[0][0] == "tool_call"
    # tool_call 在阻塞结束前就到了（留足 CI 抖动余量）
    assert arrivals[0][1] < blocking * 0.75, arrivals
    assert [kind for kind, _ in arrivals] == ["tool_call", "tool_result"]


def test_stream_renders_tokens_then_result():
    def work() -> dict:
        miao_sdk.thinking("写讲解")
        return {"explanation": "abc"}

    chunks = list(miao_sdk.stream(work, render=lambda r: list(r["explanation"])))

    assert chunks[0]["exec"]["kind"] == "thinking"
    assert [c["token"] for c in chunks[1:-1]] == ["a", "b", "c"]
    assert chunks[-1] == {"output": {"explanation": "abc"}}   # 收尾块永远最后


def test_stream_render_failure_does_not_lose_result():
    chunks = list(miao_sdk.stream(lambda: {"explanation": "abc"},
                                  render=lambda r: (_ for _ in ()).throw(RuntimeError("x"))))
    assert chunks == [{"output": {"explanation": "abc"}}]


def test_stream_wraps_work_exception_into_output():
    def work() -> dict:
        raise ValueError("上游炸了")

    chunks = list(miao_sdk.stream(work))

    assert len(chunks) == 1
    assert "ValueError" in chunks[0]["output"]["error"]


def test_stream_uses_custom_fallback():
    chunks = list(miao_sdk.stream(lambda: (_ for _ in ()).throw(ValueError("x")),
                                  fallback=lambda exc: {"error": "自定义 %s" % exc}))
    assert chunks[0]["output"]["error"] == "自定义 x"


def test_stream_wraps_non_dict_result():
    assert list(miao_sdk.stream(lambda: "纯文本")) == [{"output": {"result": "纯文本"}}]


def test_stream_clears_sink_after_finish():
    list(miao_sdk.stream(lambda: {"ok": True}))
    assert miao_sdk.get_sink() is None


def test_chunk_helpers_match_runner_contract():
    assert miao_sdk.token_chunk("x") == {"token": "x"}
    assert miao_sdk.output_chunk({"a": 1}) == {"output": {"a": 1}}
    assert miao_sdk.exec_chunk({"kind": "k"}) == {"exec": {"kind": "k"}}
    assert miao_sdk.CHUNK_KEY == "exec"

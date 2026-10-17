#!/usr/bin/env python3
"""
执行事件上报的离线自检 —— **不依赖模型、不联网**。

为什么需要它：这条链路的正确性靠「时序」与「不干扰主流程」两条纪律撑着，
而它们恰恰在线上最难观察 —— 表现只是「上游看不到工具调用」或「事件少了几条」。

覆盖（全部确定性输入，跑一遍秒钟级）：

  1. SDK 可用性：平台注入的 ``miao_sdk`` 能被 agent 导入；缺失时功能不丢；
  2. **配对与完整性**：每次 ``tool_call`` 都有同 ``call_id`` 的 ``tool_result``；
     ``seq`` 单调整；
  3. **是真实工具调用**：工具名与入参出参来自真实执行（码表 / 联网 / 模型），
     不是编的；三个工具都要出现；
  4. **阻塞期间到达**：模型调用阻塞时 ``tool_call`` 必须先到（不是算完才到齐）；
  5. **失败如实上报**：联网失败 → ``ok=false``；模型没配 → 回退路径有 log；
     校验不通过 → ``log(warn)``（不伪装成工具事件）；
  6. 非流式路径零事件、不注册 sink（不许因为上报改动而变慢或变错）；
  7. 事件里不引用模型返回的文本（工具事件来自代码，不来自模型）。

用法（在 agent 目录内）：
    python3 evals/check_exec_events.py
退出码：全部通过 0，否则 1。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

# 复刻运行时的模块布局：平台把 SDK（miao_sdk.py）放在 **runner 同目录**，
# 脚本模式下 sys.path[0] 正是那里，所以 agent 能直接 import 它。
_SDK_DIR = AGENT_DIR.parents[1] / "backend" / "agent_templates"
if _SDK_DIR.is_dir():
    sys.path.insert(0, str(_SDK_DIR))

from wubi import facts as facts_mod  # noqa: E402
from wubi import llm, progress  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def _collect(run) -> tuple[list[dict], object]:
    """跑一条流式路径，回收 (执行事件, 最终结果)。"""
    events: list[dict] = []
    final = None
    for chunk in run():
        if "exec" in chunk:
            events.append(chunk["exec"])
        elif "output" in chunk:
            final = chunk["output"]
    return events, final


class _Stubs:
    """把模型换成确定性桩，退出时复原（自检不许改动真实环境）。

    ``build_client`` 也要替换：本机（以及 CI）不装 langchain，真实实现会抛
    ModuleNotFoundError → 流水线在模型阶段就失败，于是「阶段是否完整」这类断言
    测的其实是依赖缺失，而不是被测逻辑。
    """

    def __init__(self, *, call=None, api_key="stub-key-offline-check"):
        self.call = call
        self.api_key = api_key
        self._saved: dict = {}

    def __enter__(self):
        self._saved = {"call": llm.call, "api_key": llm.API_KEY,
                       "build_client": llm.build_client}
        if self.call is not None:
            llm.call = self.call
        if self.api_key is not None:
            llm.API_KEY = self.api_key
        llm.build_client = lambda: object()
        return self

    def __exit__(self, *exc):
        llm.call = self._saved["call"]
        llm.API_KEY = self._saved["api_key"]
        llm.build_client = self._saved["build_client"]
        progress.clear()
        return False


def _fake_tavily_network(*, answer: str = "五笔编码 xyna"):
    """假网络：保留**真实的** tavily_lookup（它才上报工具事件），只换底层 HTTP。"""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        saved_urlopen = facts_mod.urllib.request.urlopen
        saved_key = os.environ.get("TAVILY_API_KEY")
        payload = json.dumps({"answer": answer,
                              "results": [{"content": "五笔 %s" % answer}]}).encode()

        class _Resp:
            def read(self, *a):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        try:
            os.environ["TAVILY_API_KEY"] = "tvly-fake-offline-check"
            facts_mod.urllib.request.urlopen = lambda *a, **k: _Resp()
            yield
        finally:
            facts_mod.urllib.request.urlopen = saved_urlopen
            if saved_key is None:
                os.environ.pop("TAVILY_API_KEY", None)
            else:
                os.environ["TAVILY_API_KEY"] = saved_key

    return _ctx()


def _stub_call(blocking: float = 0.0):
    """确定性模型桩：从提示词里读回编码与候选，每位挑第一个候选。"""

    def call(client, system_prompt, user_prompt):
        if blocking:
            time.sleep(blocking)
        code = ""
        m = re.search(r"-\s*(?:local|caller|tavily)\s*[：:]\s*([a-y]{1,4})", user_prompt)
        if m:
            code = m.group(1)
        positions = []
        for i, km in enumerate(re.finditer(
                r"第\s*(\d+)\s*位\s+键\s+([a-y])\s+候选[：:]\s*([^\n]*)", user_prompt)):
            glyphs = [g.strip() for g in km.group(3).split("/") if g.strip()]
            positions.append({"index": i, "key": km.group(2),
                              "radical": glyphs[0] if glyphs else "",
                              "reason": "离线自检桩：取该键候选集里的第一个字形"})
        return json.dumps({"code": code, "positions": positions,
                           "suggestions": []}, ensure_ascii=False)

    return call


SAMPLE_INPUT = {
    "char": "编",
    "code": "xyna",
    "online": True,
    "positions": [
        {"index": 0, "key": "x", "candidates": [{"glyph": "纟"}]},
        {"index": 1, "key": "y", "candidates": [{"glyph": "讠"}]},
        {"index": 2, "key": "n", "candidates": [{"glyph": "尸"}]},
        {"index": 3, "key": "a", "candidates": [{"glyph": "艹"}]},
    ],
}

TOOLS_EXPECTED = {"tables.lookup", "tavily.search", "llm.decide"}


# ── 0. SDK 可用性 ──────────────────────────────────────────────


def check_sdk():
    print("0. 平台 SDK 接入")
    check("agent 能导入平台 SDK miao_sdk", progress.SDK_AVAILABLE,
          "SDK 缺失时事件静默降级、功能不受影响，但上游看不到过程 → 检查 runner 同目录")


# ── 1. 配对 / seq / 工具真实性 ─────────────────────────────────


def check_pairing_and_payloads():
    print("1. 工具调用成对、seq 单调、入参出参来自真实执行")
    import agent

    try:
        with _fake_tavily_network(), _Stubs(call=_stub_call()):
            events, result = _collect(lambda: agent.invoke(dict(SAMPLE_INPUT), {"stream": True}))
    except Exception as exc:  # noqa: BLE001
        check("单字流式路径可完整跑通", False, f"{type(exc).__name__}: {exc}")
        return

    calls = [e for e in events if e.get("kind") == "tool_call"]
    results = [e for e in events if e.get("kind") == "tool_result"]

    check("有工具调用", bool(calls), f"{len(calls)} 次")
    check("call 与 result 数量一致", len(calls) == len(results),
          f"call={len(calls)} result={len(results)}")

    call_ids = {c["call_id"] for c in calls}
    result_ids = {r["call_id"] for r in results}
    check("每个 call 都有同 call_id 的 result（配对完整）",
          call_ids == result_ids, f"差集={call_ids ^ result_ids}")

    seqs = [e["seq"] for e in events]
    check("seq 严格递增（上游可据此发现缺口）",
          seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"{seqs}")

    names = {c["name"] for c in calls}
    check("三个真实工具都出现", TOOLS_EXPECTED <= names, f"实际 {sorted(names)}")

    # 入参出参必须是对象，且来自真实执行
    tables_call = next((c for c in calls if c["name"] == "tables.lookup"), None)
    check("tables.lookup 入参是对象且带 char",
          tables_call is not None and isinstance(tables_call.get("input"), dict)
          and tables_call["input"].get("char") == "编")
    tables_result = next((r for r in results if r["name"] == "tables.lookup"), None)
    check("tables.lookup 出参是真实命中的编码",
          tables_result is not None and tables_result.get("output", {}).get("code") == "xyna",
          str(tables_result and tables_result.get("output")))

    tavily_call = next((c for c in calls if c["name"] == "tavily.search"), None)
    check("tavily.search 入参带真实查询串",
          tavily_call is not None and "编" in str(tavily_call.get("input", {}).get("query", "")))
    tavily_result = next((r for r in results if r["name"] == "tavily.search"), None)
    check("tavily.search 出参是对象（非字符串化 JSON）",
          tavily_result is not None and isinstance(tavily_result.get("output"), dict),
          str(tavily_result and tavily_result.get("output")))
    check("tavily.search 有耗时且是数字",
          tavily_result is not None and isinstance(tavily_result.get("ms"), int))

    llm_call = next((c for c in calls if c["name"] == "llm.decide"), None)
    check("llm.decide 入参带模型名与尝试次数",
          llm_call is not None and isinstance(llm_call.get("input"), dict)
          and "model" in llm_call["input"] and llm_call["input"].get("attempt") == 1,
          str(llm_call and llm_call.get("input")))

    check("最终结果照旧完整（事件不参与校验门槛）",
          isinstance(result, dict) and bool(result.get("positions")),
          f"error={result.get('error') if isinstance(result, dict) else result!r}")
    return events


# ── 2. 阻塞期间到达 ────────────────────────────────────────────


def check_timing():
    print("2. 模型调用阻塞期间，工具调用事件先到")
    import agent

    blocking = 0.5
    timed: list[tuple[str, float]] = []
    try:
        with _fake_tavily_network(), _Stubs(call=_stub_call(blocking)):
            t0 = time.monotonic()
            for chunk in agent.invoke(dict(SAMPLE_INPUT), {"stream": True}):
                if "exec" in chunk:
                    ev = chunk["exec"]
                    if ev.get("kind") == "tool_call":
                        timed.append((ev["name"], time.monotonic() - t0))
    except Exception as exc:  # noqa: BLE001
        check("时序检查可跑通", False, f"{type(exc).__name__}: {exc}")
        return

    llm_times = [t for name, t in timed if name == "llm.decide"]
    check("llm.decide 的 tool_call 在阻塞结束前到达（不是算完才到齐）",
          bool(llm_times) and llm_times[0] < blocking * 0.8,
          f"首个在 {llm_times[0]:.2f}s，阻塞 {blocking}s" if llm_times else "没收到 llm.decide")


# ── 3. 失败路径如实上报 ────────────────────────────────────────


def check_failures():
    print("3. 失败如实上报（不隐藏、不伪装）")
    import agent

    # 3a. 联网失败 → tool_result ok=false
    events: list[dict] = []
    saved_urlopen = facts_mod.urllib.request.urlopen
    saved_key = os.environ.get("TAVILY_API_KEY")
    saved_sink = None
    try:
        os.environ["TAVILY_API_KEY"] = "tvly-invalid-offline-check"
        facts_mod.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            OSError("模拟断网"))
        if progress.SDK_AVAILABLE:
            import miao_sdk
            saved_sink = miao_sdk.get_sink()
            miao_sdk.set_sink(events.append)
        facts_mod.tavily_lookup("编", queries=["q1"], local_code="xyna")
    finally:
        facts_mod.urllib.request.urlopen = saved_urlopen
        if saved_key is None:
            os.environ.pop("TAVILY_API_KEY", None)
        else:
            os.environ["TAVILY_API_KEY"] = saved_key
        if progress.SDK_AVAILABLE:
            import miao_sdk
            miao_sdk.set_sink(saved_sink)
        progress.clear()

    failed = [e for e in events if e.get("kind") == "tool_result" and e.get("ok") is False]
    check("联网失败上报 tool_result ok=false", bool(failed),
          str([(e.get("name"), e.get("error")) for e in failed[:1]]))

    # 3b. 模型未配置 → 有 log，且不产出伪造的工具调用
    with _Stubs(call=None, api_key=""):
        events, result = _collect(lambda: agent.invoke({"char": "编"}, {"stream": True}))
    logs = [e for e in events if e.get("kind") == "log"]
    check("模型未配置时如实记 log(error)",
          any(e.get("level") == "error" for e in logs), str(logs[:1]))
    check("模型未配置时仍返回可解释的 error",
          isinstance(result, dict) and "LLM_API_KEY" in (result.get("error") or ""))

    # 3c. 校验不通过 → log(warn)，而不是伪装成工具事件
    def always_wrong(client, sp, up):
        return json.dumps({"code": "zzzz", "positions": [], "suggestions": []})

    with _Stubs(call=always_wrong):
        events, _ = _collect(lambda: agent.invoke(
            {"char": "编", "code": "xyna", "online": False,
             "positions": [{"index": 0, "key": "x", "candidates": [{"glyph": "纟"}]}]},
            {"stream": True}))
    warns = [e for e in events if e.get("kind") == "log" and e.get("level") == "warn"]
    check("校验不通过记 log(warn)", bool(warns), str(warns[:1]))
    verify_like = [c for c in events
                   if c.get("kind") == "tool_call" and "verify" in str(c.get("name"))]
    check("校验**没有**被伪装成工具调用", verify_like == [], str(verify_like))


# ── 4. 非流式零事件 ────────────────────────────────────────────


def check_non_stream():
    print("4. 非流式路径零影响")
    import agent

    events: list[dict] = []
    with _Stubs(call=_stub_call()):
        if progress.SDK_AVAILABLE:
            import miao_sdk
            miao_sdk.set_sink(events.append)
        result = agent.invoke(dict(SAMPLE_INPUT), {})

    check("非流式返回完整 dict", isinstance(result, dict))
    check("非流式路径不发出任何事件（sink 被清掉）", events == [], f"实际 {len(events)} 条")
    if progress.SDK_AVAILABLE:
        import miao_sdk
        check("非流式结束后 sink 为 None（不残留给下一轮）",
              miao_sdk.get_sink() is None)


# ── 5. 事件不引用模型文本 ──────────────────────────────────────


def check_no_model_text():
    print("5. 工具事件来自代码，不来自模型")
    import agent

    marker = "模型编造ABCXYZ"
    with _Stubs(call=lambda client, sp, up: marker):
        events, _ = _collect(lambda: agent.invoke(
            {"char": "编", "code": "xyna", "online": False}, {"stream": True}))

    leaked = [e for e in events if marker in json.dumps(e, ensure_ascii=False)]
    check("事件里不含模型返回的文本", not leaked, f"泄漏 {leaked[:1]}")
    check("确实收到了事件（否则本项是空跑）", len(events) > 0, f"{len(events)} 条")


def main() -> int:
    print("五笔 agent 执行事件自检（离线，不需要模型）\n")
    check_sdk()
    check_pairing_and_payloads()
    check_timing()
    check_failures()
    check_non_stream()
    check_no_model_text()

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项未通过：")
        for name in FAILED:
            print(f"   - {name}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

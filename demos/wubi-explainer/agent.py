#!/usr/bin/env python3
"""
五笔拆字解释 agent（平台入口：agent:invoke）。

定位变更（相对早期版本）
------------------------
旧版是「候选集抽取器」：调用方给 code 和每位候选，agent 只能在候选里挑。
于是错了既发现不了、也纠正不了；而它为了把话讲圆，会编造事实上不存在的信息
（最典型的是在没有笔顺数据时断言"首笔为竖"），自校验还只查结构、查不出这类错。

新版是「决策者」：
  1. 事实采集：内置码表 + Tavily 联网字典，多源并列，分歧原样上报（wubi/facts.py）
  2. 决策    ：编码由事实定；模型只挑字根并给**字形**上的理由（wubi/decide.py）
  3. 校验    ：纯规则校验 + 可解释置信度，模型不得覆盖权威事实（wubi/verify.py）
  4. 组句    ：讲解由服务端拼装，模型不碰整段散文（wubi/compose.py）
  5. 追问    ：同一会话内的后续提问 —— 历史当事实块交给模型回答，引用可核对
               （wubi/followup.py 编排 + wubi/answer.py 提示与校验）

词级（词组）模式
----------------
输入带 ``task=explain-phrase``（或锚点是词组、逐位材料带 ``fromChar``）时走
``wubi/phrase.py``：事实**全部由调用方给全** —— 取码规则（每字取几码）、每一位
取自哪个字的第几码、该位的候选字根。agent 不重新推导取码规则，模型只做
「从候选里挑字根 + 说字形理由」，来源与规则由服务端写进讲解。
校验四条：编码未被改写、位数一致、键位与来源字一致、字根落在该位候选内。

多轮会话（平台侧）
------------------
平台把本会话既往轮次的 input/output 注回 ``config["conversation"]``，
agent 自身仍然无状态。两种用法：

  * 追问轮（input 带 ``question``，且没有重新给 code+positions）
    → wubi/followup.py：**每一轮都推理**，但输入受控 ——
      历史里的编码/字根/理由作为「已核实事实块」注入，模型必须为每句陈述给出
      可核对的引用（citations，指不到真实字段就打回重试），
      需要新判断的内容单独放 new_claims 并标注「未经交叉核验」；
      「用户纠正某一位」是否在候选集内**由代码核对后告知模型**（日/曰 这类形近
      字根模型会看串），落在候选内的纠正必须落到 position_revisions。
      模型不可用（没 key / 调用失败）时降级为基于历史的确定性回答，并在讲解里说明。
  * 完整拆字轮（给了 char+code/positions）
    → 走正常流程；此时若也带了 ``question``，把它作为背景说明喂给决策层，
      不去猜「用户在问哪一位」。

流式（config["stream"]）
------------------------
``invoke`` 在 ``config["stream"]`` 为真时返回 generator：先按块吐正文（前端打字机），
最后 ``yield {"output": result}`` 给出结构化结果（平台随 done 事件记录该轮 output）。
非流式时返回完整 dict，行为与以前完全一致。

!! 绝不向外抛异常 !!
抛出会被平台变成 500/502，调用方只看到「服务异常」，拿不到任何可解释信息。
所有失败路径都归一成 output 里的一个 dict（含 error 字段）。
"""
from __future__ import annotations

import os
import sys
import time

# 保证无论以哪种方式启动（docker / venv / 直接 python）都能 import 到同目录的 wubi 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wubi import compose, decide, followup, llm, phrase, tables, verify  # noqa: E402
from wubi import facts as facts_mod  # noqa: E402

# 两次尝试之间共享的预算：超了就带着已有信息返回，而不是让平台 180s 超时后给一个空错误
TOTAL_BUDGET_SEC = int(os.environ.get("WUBI_TOTAL_BUDGET", "120"))
MAX_ATTEMPTS = int(os.environ.get("WUBI_MAX_ATTEMPTS", "2"))

# 流式模式：正文按块吐（打字机效果）。默认 ≈200 字/秒，300 字的讲解约 1.5s 吐完
# （太快就看不出在打字了）；嫌慢可调 WUBI_STREAM_DELAY。
STREAM_CHUNK = max(1, int(os.environ.get("WUBI_STREAM_CHUNK", "6")))
STREAM_DELAY = float(os.environ.get("WUBI_STREAM_DELAY", "0.03"))


class BadInput(Exception):
    """输入不合法：这类错误是调用方的问题，如实回报，不要试图猜。"""


def _parse_input(input: dict):
    input = input if isinstance(input, dict) else {}
    char = input.get("char")
    if not isinstance(char, str) or not char.strip():
        raise BadInput("缺少 char")
    char = char.strip()
    if len(char) != 1:
        raise BadInput("char 必须是单个汉字")

    notes = input.get("notes") or []
    if not isinstance(notes, list):
        notes = []
    error = input.get("error") or None
    if not (isinstance(error, dict) and error.get("expected") and error.get("actual")):
        error = None

    online = input.get("online", True)
    hints = {
        "code": input.get("code"),
        "notes": notes,
        "error": error,
        "allow_online": bool(online),
        # 调用方的逐位候选（agent.yaml 契约：给了就「只能从这里挑」）。
        # 只保留 dict 形状的项，传给事实层做权威候选，别在 collect 里被本地推导扩大。
        "positions": [p for p in (input.get("positions") or []) if isinstance(p, dict)],
    }

    # certain：调用方已经算出来的确定字根（成字字根/键名字），直接强制采纳
    certain = {}
    code = hints.get("code") or ""
    for p in input.get("positions") or []:
        if not isinstance(p, dict) or not p.get("certain"):
            continue
        idx = p.get("index")
        if idx is None and p.get("key") and code:
            found = code.find(p["key"])
            idx = found if found >= 0 else None
        if idx is not None:
            certain[str(idx)] = p["certain"]

    return char, hints, certain


def _sources_digest(facts: dict) -> list:
    out = []
    for name, f in (facts.get("facts") or {}).items():
        if f.get("ok") and f.get("code"):
            out.append({"source": name, "code": f["code"], "detail": f.get("source", "")})
        else:
            out.append({"source": name, "code": None, "detail": f.get("error", "")})
    return out


def _fail(char: str, error: str, attempts: int = 0, violations=None) -> dict:
    return {
        "char": char,
        "code": None,
        "code_source": "none",
        "positions": None,
        "explanation": None,
        "suggestions": [],
        "confidence": 0.0,
        "conflicts": [],
        "sources": [],
        "error": error,
        "self_check": {"ok": False, "attempts": attempts, "violations": violations or []},
        "model": llm.MODEL,
    }


def _stream_pieces(text: str):
    """按固定块长把正文吐出去（打字机效果），块间留一点间隔。"""
    for start in range(0, len(text), STREAM_CHUNK):
        yield {"token": text[start:start + STREAM_CHUNK]}
        if STREAM_DELAY > 0:
            time.sleep(STREAM_DELAY)


def _stream_result(result: dict):
    """把完整结果包成 generator（``config["stream"]`` 为真时返回它）。

    约定见 backend/agent_templates/miao_runner.py 的流式说明：
      * 逐段 ``{"token": ...}`` = 正文，前端据此做打字机；
      * 最后 ``{"output": result}`` = 结构化结果，runner 不当 token 转发，而是随
        ``event: done`` 下发；平台据此记录本轮 output —— 这样**流式轮次也能作为
        下一轮追问的上下文**（否则历史里 output 为空，追问会失去依据）。
    """

    def _gen():
        text = result.get("explanation") or result.get("error") or ""
        yield from _stream_pieces(text)
        yield {"output": result}

    return _gen()


def invoke(input: dict, config: dict = None):
    """平台入口：``config["stream"]`` 为真时返回 generator，否则返回完整 dict。"""
    result = _dispatch(input, config)
    if isinstance(result, dict) and (config or {}).get("stream"):
        return _stream_result(result)
    return result


def _dispatch(input: dict, config: dict = None) -> dict:
    """真正的逻辑：事实采集 → 决策 → 校验 → 组句（或追问）。"""
    started = time.time()
    input = input if isinstance(input, dict) else {}
    question = input.get("question")
    question = question.strip() if isinstance(question, str) else ""

    # ── 0. 追问轮：带 question 且没有重新给完整材料 ──
    # 结论与候选集已经在上一轮落库并注回 config.conversation，这里确定性作答。
    if question and not (input.get("code") and input.get("positions")):
        conversation = (config or {}).get("conversation") or {}
        return followup.handle(
            char=(input.get("char") or "").strip(),
            question=question,
            turns=conversation.get("turns") or [],
            started=started,
        )

    # ── 0.5 词级（词组）轮：事实由调用方给全（规则 + 逐位来源 + 候选） ──
    # 必须排在单字解析之前：`_parse_input` 会按「单个汉字」拒掉词组。
    # 词组追问轮不带 code/positions，已经在上面的追问分支处理掉了。
    if phrase.is_phrase_input(input):
        return phrase.run(input, started)

    try:
        char, hints, certain = _parse_input(input)
    except BadInput as exc:
        return _fail("", "输入不合法：%s" % exc)

    # 完整材料 + 追问文本：把问题当背景交给决策层（不要猜用户在问哪一位）
    if question:
        hints["notes"] = list(hints.get("notes") or []) + [
            {"message": f"调用方追问：{question}"}
        ]

    # ── 1. 事实采集（离线码表 + 联网核验；失败自动降级，永不抛出） ──
    # caller_positions：调用方给的逐位候选是权威清单（agent.yaml 契约），
    # 不带它才退回「字根表 ∪ 变形归并的 IDS 部件」。
    try:
        facts = facts_mod.collect(
            char, caller_code=hints.get("code"), allow_online=hints["allow_online"],
            caller_positions=hints.get("positions"),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(char, "事实采集失败：%s: %s" % (type(exc).__name__, str(exc)[:160]))

    if not llm.API_KEY:
        return _fail(char, "模型未配置（缺少 LLM_API_KEY）")

    try:
        client = llm.build_client()
    except Exception as exc:  # noqa: BLE001
        return _fail(char, "模型初始化失败：%s: %s" % (type(exc).__name__, str(exc)[:160]))

    # ── 2. 决策（最多两次，失败把原因喂回去重试） ──
    prompt = decide.build_user_prompt(char, facts, hints)
    result = None
    attempts = 0
    rejection = None
    last_error = None
    suggestions = []

    for attempt in range(MAX_ATTEMPTS):
        if time.time() - started > TOTAL_BUDGET_SEC:
            break
        attempts = attempt + 1
        p = prompt if rejection is None else decide.add_rejection(prompt, rejection)
        try:
            raw = llm.call(client, decide.SYSTEM_PROMPT, p)
        except Exception as exc:  # noqa: BLE001
            last_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            rejection = "上一次调用失败：%s" % last_error
            continue

        decision = decide.parse_decision(raw)
        if decision is None:
            rejection = "输出不是合法 JSON"
            continue

        suggestions = llm.as_suggestions(decision.get("suggestions"))
        result = verify.validate(decision, facts, certain)
        if result["ok"]:
            last_error = None
            break
        rejection = "；".join(result["violations"]) or "自校验未通过"

    if result is None or not result.get("code"):
        return _fail(char, last_error or rejection or "无法确定该字的五笔编码", attempts)

    # ── 3. 组句（服务端拼装，模型不碰整段散文） ──
    explanation = compose.build(char, result, facts, suggestions)

    return {
        "char": char,
        "code": result["code"],
        "code_source": result["code_source"],
        "positions": result["positions"],
        "explanation": explanation,
        "suggestions": suggestions,
        "confidence": result["confidence"],
        "conflicts": result["conflicts"],
        "sources": _sources_digest(facts),
        "table_digest": tables.table_digest(),
        "error": None if result["ok"] else "；".join(result["violations"]),
        "self_check": {
            "ok": result["ok"],
            "attempts": attempts,
            "violations": result["violations"],
            "confidence": result["confidence"],
        },
        "model": llm.MODEL,
        "elapsed_ms": int((time.time() - started) * 1000),
    }

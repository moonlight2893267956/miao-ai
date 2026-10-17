"""
词级（词组）取码讲解：事实由调用方给全，模型只做「挑字根 + 说字形理由」。

与单字（decide/verify/compose 那条链）同构，差别只在事实的来源：

  * 单字的事实要**采集**（内置码表 + 联网字典 + IDS 部件 + 逐键候选）；
  * 词组的事实**已经在调用方手里**：取码规则（每字取几码）、每一位取自哪个字的
    第几码、该位的候选字根 —— 这些都是练习室的确定性数据，agent 不需要也不应该
    重新推导（重新推就会推歪，历史教训见 facts.py 的 `_caller_candidates`）。

所以本模块只做三件事：把事实摆成提示词 → 校验模型的挑选 → 服务端拼装讲解。
组句仍然不交给模型：词组讲解最容易编的就是「这一位为什么取这个字」（实际上由
规则决定），所以来源由代码写死，模型只给自己的字形取舍理由。

!! 绝不向外抛异常 !!
所有失败路径都归一成 output 里的 dict（含 error 与 self_check），见 agent.py 的说明。
"""
from __future__ import annotations

import os
import time

from . import llm, progress
from .decide import add_rejection

MAX_ATTEMPTS = int(os.environ.get("WUBI_MAX_ATTEMPTS", "2"))
TOTAL_BUDGET_SEC = int(os.environ.get("WUBI_TOTAL_BUDGET", "120"))

# 词组的取码位都是**字根位**（规则只取各字的前 1~2 码，取不到识别码位），
# 所以这里不开放「声明识别码」这条路：放开只会让模型用它来掩饰挑不出来。
SYSTEM_PROMPT = """你是五笔字型（86 版）的拆字老师。这次要讲的是一个**词组**为什么这么取码。

你会拿到：
  * 词组的取码规则（每个字取前几码）；
  * 逐位材料：这一位是哪个键、**取自哪个字的第几码**、该键上的全部候选字根。

你的活儿只有两件：
  1) 为每一位从候选中选出正确的字根；
  2) 用**字形**说明为什么是它、为什么不是同键的其他候选。

硬性约束（违反即为错误）：
1. 只能使用「事实」区块里给出的信息。事实里没有的东西，一律不要补充。
2. **严禁描述笔画书写过程与笔顺**（如"首笔为竖""第二笔横折"）。你没有笔顺数据。
3. **每一位都必须是字根位**：radical 必须逐字取自该位候选字根表（字形照抄，可以用简称）。
   不要声明识别码、不要留空 —— 这些位在规则里就是字根位。
4. positions 的项数必须等于取码位数；第 i 项的 key 必须等于该位的键，且不得改写编码。
5. **from_char 必须逐字照抄事实里给的来源字**：把「取自哪个字」讲错，整段讲解就是错的。
6. reason 只讲**字形上的取舍**（例如「日窄长、曰扁宽，这里是窄长的那个」），
   不要复述取码规则，不要提笔顺。
7. 只输出 JSON，不要任何前后缀、不要 Markdown 代码围栏。

输出 JSON 结构（字段名逐字照抄）：
{
  "code": "词表里的编码，例如 xydc",
  "positions": [
    {"key": "x", "from_char": "编", "radical": "纟", "reason": "字形左侧是绞丝旁"},
    {"key": "d", "from_char": "码", "radical": "石", "reason": "字形左侧是石字旁"}
  ],
  "suggestions": ["给初学者的一句提醒"]
}"""


def is_phrase_input(input: dict) -> bool:
    """是不是词级（词组）请求。

    三个判据任一成立即走词级分支：
      * `task == "explain-phrase"`（前端显式声明）；
      * 锚点文本多于一个字（`char` / `phrase`）；
      * 逐位材料里带了 `fromChar`（词级来源字段，单字不会带）。
    """
    if not isinstance(input, dict):
        return False
    if str(input.get("task") or "").strip() == "explain-phrase":
        return True

    positions = input.get("positions")
    positions = positions if isinstance(positions, list) else []
    if any(isinstance(p, dict) and p.get("fromChar") for p in positions):
        return True

    anchor = input.get("phrase") or input.get("char") or ""
    return isinstance(anchor, str) and len(anchor.strip()) > 1 and bool(positions)


def _as_candidates(raw) -> list[dict]:
    """候选字根归一成 [{'glyph','name'}]；形状不对的项直接丢。"""
    out = []
    for item in raw or []:
        if isinstance(item, dict):
            glyph = item.get("glyph")
            if isinstance(glyph, str) and glyph.strip():
                name = item.get("name")
                out.append({"glyph": glyph.strip(), "name": name if isinstance(name, str) else ""})
        elif isinstance(item, str) and item.strip():
            out.append({"glyph": item.strip(), "name": ""})
    return out


def parse_input(input: dict) -> dict:
    """把调用方材料归一成事实块。

    :raises ValueError: `positions` 缺失或为空（没有逐位材料就无法约束模型）
    """
    input = input if isinstance(input, dict) else {}
    phrase = input.get("phrase") or input.get("char") or ""
    phrase = phrase.strip() if isinstance(phrase, str) else ""

    positions = []
    for i, item in enumerate(input.get("positions") or []):
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        candidates = _as_candidates(item.get("candidates"))
        index = item.get("index")
        positions.append({
            "index": index if isinstance(index, int) else i,
            "key": key.strip().lower(),
            "fromChar": str(item.get("fromChar") or item.get("from_char") or ""),
            "charIndex": item.get("charIndex") if isinstance(item.get("charIndex"), int) else None,
            "charCodeIndex": (
                item.get("charCodeIndex") if isinstance(item.get("charCodeIndex"), int) else None
            ),
            "candidates": candidates,
            "certain": item.get("certain") if isinstance(item.get("certain"), str) else None,
        })

    if not positions:
        raise ValueError("缺少 positions（词级讲解需要逐位候选材料）")

    notes = [
        str(n.get("message"))
        for n in (input.get("notes") or [])
        if isinstance(n, dict) and n.get("message")
    ]
    skipped = [str(c) for c in (input.get("skippedChars") or []) if str(c).strip()]

    code = input.get("code")
    return {
        "phrase": phrase,
        "code": code.strip().lower() if isinstance(code, str) else "",
        "rule": str(input.get("ruleLabel") or input.get("rule_label") or ""),
        "derivedCode": (
            input.get("derivedCode").strip().lower()
            if isinstance(input.get("derivedCode"), str) else None
        ),
        "matchesTable": input.get("matchesTable") is not False,
        "positions": positions,
        "skippedChars": skipped,
        "notes": notes,
    }


def _position_line(pos: dict, index: int) -> str:
    key = pos["key"]
    glyphs = " / ".join(c["glyph"] for c in pos["candidates"]) or "（无候选）"
    origin = ""
    if pos["fromChar"]:
        nth = pos["charCodeIndex"]
        origin = f"（取自「{pos['fromChar']}」" + (f"的第 {nth + 1} 码" if isinstance(nth, int) else "") + "）"
    line = f"  第 {index + 1} 位  键 {key}{origin}  候选：{glyphs}"
    if pos["certain"]:
        line += f"\n      ★ 调用方已确定这一位是「{pos['certain']}」"
    return line


def build_user_prompt(facts: dict) -> str:
    lines = [f"词组：{facts['phrase']}", ""]

    lines.append("事实（已核实，直接采用）：")
    if facts["code"]:
        lines.append(f"  - 词表编码：{facts['code']}")
    if facts["rule"]:
        lines.append(f"  - 取码规则：{facts['rule']}")
    if facts["derivedCode"] and facts["derivedCode"] != facts["code"]:
        lines.append(
            f"  - 按规则推导应为 {facts['derivedCode']}，但词表收录为 {facts['code']}："
            "词表对极少数词有特殊收录，**以词表为准**，不要按推导改写编码。"
        )
    if facts["skippedChars"]:
        lines.append(
            f"  - 本规则下不取码的字：{'、'.join(facts['skippedChars'])}（它们不占任何编码位）"
        )

    lines.append("")
    lines.append("逐位取码位（每一位只能从该位候选里挑一个字根，并照抄来源字）：")
    for i, pos in enumerate(facts["positions"]):
        lines.append(_position_line(pos, i))

    if facts["notes"]:
        lines.append("")
        lines.append("调用方给的补充信息（仅作背景，不要据此改写编码）：")
        for note in facts["notes"]:
            lines.append(f"  - {note}")

    lines.append("")
    lines.append(
        "现在给出这个词组的编码、每一位选的字根与来源字，以及**基于字形**的取舍理由。"
    )
    return "\n".join(lines)


def _valid_radical(radical, candidates: list[dict]) -> bool:
    """字根必须逐字来自该位候选（前端核对是精确匹配，这里就不能放宽）。"""
    if not isinstance(radical, str):
        return False
    glyph = radical.strip()
    return any(c["glyph"] == glyph for c in candidates)


def validate(decision: dict, facts: dict) -> dict:
    """校验模型的挑选；返回 ``{ok, code, positions, violations, confidence}``。

    四条：编码未被改写 / 项数一致 / 键位与来源字一致 / 字根落在该位候选内。
    """
    violations: list[str] = []
    raw_positions = decision.get("positions")
    raw_positions = raw_positions if isinstance(raw_positions, list) else []

    code = decision.get("code")
    code = code.strip().lower() if isinstance(code, str) else ""
    if facts["code"] and code and code != facts["code"]:
        violations.append(f"编码被改写：词表是 {facts['code']}，模型给了 {code}")

    expected = facts["positions"]
    if len(raw_positions) != len(expected):
        violations.append(f"位数不符：这个词组有 {len(expected)} 位取码位，模型给了 {len(raw_positions)} 位")

    out_positions = []
    for i, item in enumerate(raw_positions):
        if not isinstance(item, dict):
            violations.append(f"第 {i + 1} 项不是对象：{item!r}")
            continue
        fact = expected[i] if i < len(expected) else None
        key = item.get("key")
        key = key.strip().lower() if isinstance(key, str) else ""
        radical = item.get("radical")
        radical = radical.strip() if isinstance(radical, str) else ""
        from_char = item.get("from_char") or item.get("fromChar")
        from_char = from_char.strip() if isinstance(from_char, str) else ""

        out_positions.append({
            "index": i,
            "key": key,
            "from_char": from_char or (fact["fromChar"] if fact else ""),
            "radical": radical,
            "reason": (item.get("reason") or "").strip() if isinstance(item.get("reason"), str) else "",
            "unresolved": False,
        })

        if fact is None:
            continue

        if key != fact["key"]:
            violations.append(f"第 {i + 1} 位键位不符：应为 {fact['key']}，模型给了 {key}")
            out_positions[-1]["unresolved"] = True
            continue

        if from_char and fact["fromChar"] and from_char != fact["fromChar"]:
            violations.append(
                f"第 {i + 1} 位来源字不符：应取自「{fact['fromChar']}」，模型说是「{from_char}」"
            )
            out_positions[-1]["unresolved"] = True

        if item.get("recognition"):
            violations.append(
                f"第 {i + 1} 位（键 {fact['key']}）是字根位，不是识别码位，请从候选里挑一个字根"
            )
            out_positions[-1]["unresolved"] = True
            continue

        if not fact["candidates"]:
            # 该键没有可用字根（数据缺失/补位）：不许给字根
            if radical:
                violations.append(
                    f"第 {i + 1} 位（键 {fact['key']}）没有可选字根，不应给出字根，模型给了「{radical}」"
                )
                out_positions[-1]["unresolved"] = True
            continue

        if not radical:
            where = f"「{fact['fromChar']}」第 {(fact['charCodeIndex'] or 0) + 1} 码" if fact["fromChar"] \
                else f"键 {fact['key']}"
            violations.append(f"第 {i + 1} 位（{where}）没有给字根，必须从候选中挑一个")
            out_positions[-1]["unresolved"] = True
            continue

        if not _valid_radical(radical, fact["candidates"]):
            glyphs = " / ".join(c["glyph"] for c in fact["candidates"])
            where = f"「{fact['fromChar']}」第 {(fact['charCodeIndex'] or 0) + 1} 码（{fact['key']} 键）" \
                if fact["fromChar"] else f"键 {fact['key']}"
            violations.append(f"第 {i + 1} 位字根「{radical}」不在{where}的候选里（{glyphs}）")
            out_positions[-1]["unresolved"] = True

    # 词组讲解的结构是确定的（规则由代码写死、候选由调用方给），剩下的不确定性
    # 只在「模型挑得对不对」上，所以通过的轮次给一个偏高但不等于 1 的置信度。
    confidence = 0.85 if not violations else 0.0

    return {
        "ok": not violations,
        "code": facts["code"] or code,
        "positions": out_positions,
        "violations": violations,
        "confidence": confidence,
    }


def _origin_groups(result: dict) -> list[tuple[str, str]]:
    """把逐位结果按来源字聚成 `[(字, 码)]`，用于「编 xy + 码 dc」这种一句话摘要。"""
    groups: list[tuple[str, str]] = []
    for pos in result.get("positions") or []:
        char = pos.get("from_char") or ""
        if groups and groups[-1][0] == char:
            groups[-1] = (char, groups[-1][1] + pos["key"])
        else:
            groups.append((char, pos["key"]))
    return [(c, code) for c, code in groups if c]


def build_explanation(facts: dict, result: dict, suggestions: list) -> str:
    code = result.get("code") or ""
    rule = facts.get("rule") or ""
    groups = _origin_groups(result)
    split_text = " + ".join(f"{char} {glyphs}" for char, glyphs in groups)

    head = f"「{facts['phrase']}」的五笔编码是 {code}"
    if rule:
        head += f"（{rule}" + (f"：{split_text}" if split_text else "") + "）"
    lines = [head + "。"]

    positions = result.get("positions") or []
    if positions:
        lines.append("逐位取舍：")
        for p in positions:
            origin = ""
            if p.get("from_char"):
                origin = f"（「{p['from_char']}」）"
            if p.get("unresolved"):
                lines.append(
                    f"  第 {p['index'] + 1} 位 {p['key']}{origin}：（未能确定）"
                    "——本次没有给出可靠依据，请以码表为准"
                )
                continue
            body = str(p.get("radical") or "")
            reason = (p.get("reason") or "").strip()
            if reason:
                body += f"——{reason}"
            lines.append(f"  第 {p['index'] + 1} 位 {p['key']}{origin}：{body}")

    notes = list(facts.get("notes") or [])
    if facts.get("derivedCode") and facts["derivedCode"] != code:
        # 调用方通常已经给过一条同样的说明（它同时是给模型的提示）。两条都留着，
        # 「补充说明」里会出现同一件事说两遍 —— 只在没人说过时才补这一条。
        said = any(
            isinstance(n, str) and facts["derivedCode"] in n and code and code in n
            for n in notes
        )
        if not said:
            notes.append(
                f"词表收录为 {code}，按规则推导应为 {facts['derivedCode']}；本讲解以词表为准"
            )
    if notes:
        lines.append("补充说明：")
        for note in notes:
            lines.append(f"  - {note}")

    uniq = [s for s in dict.fromkeys([s for s in (suggestions or []) if s])]
    if uniq:
        lines.append("易混提醒：")
        for s in uniq[:4]:
            lines.append(f"  - {s}")

    return "\n".join(lines)


def _fail(error: str, attempts: int = 0, violations=None) -> dict:
    return {
        "char": None,
        "code": None,
        "positions": None,
        "explanation": None,
        "suggestions": [],
        "error": error,
        "self_check": {"ok": False, "attempts": attempts, "violations": violations or []},
        "model": llm.MODEL,
    }


def run(input: dict, started: float) -> dict:
    """词级讲解主流程：事实 → 决策（最多两次）→ 校验 → 组句。"""
    try:
        facts = parse_input(input)
    except ValueError as exc:
        return _fail("输入不合法：%s" % exc)

    anchor = facts["phrase"]
    # 词组的事实由调用方给全，没有联网/码表阶段；把「取码规则」当成一次事实查询工具，
    # 出参带规则名与位数，复盘时能确认讲解依据的是哪套规则。
    with progress.tool("tables.rule",
                       phrase=anchor,
                       rule=facts["rule"]) as t:
        t.output({"positions": len(facts["positions"])})

    if not llm.API_KEY:
        progress.log("模型未配置（缺少 LLM_API_KEY）", level="error")
        out = _fail("模型未配置（缺少 LLM_API_KEY）")
        out["char"] = anchor
        return out

    try:
        client = llm.build_client()
    except Exception as exc:  # noqa: BLE001 - 依赖缺失要变成可解释返回，别让平台看到 500
        progress.log("模型初始化失败：%s" % type(exc).__name__, level="error")
        out = _fail("模型初始化失败：%s: %s" % (type(exc).__name__, str(exc)[:160]))
        out["char"] = anchor
        return out

    prompt = build_user_prompt(facts)
    result = None
    attempts = 0
    rejection = None
    last_error = None
    suggestions: list[str] = []

    for attempt in range(MAX_ATTEMPTS):
        if time.time() - started > TOTAL_BUDGET_SEC:
            break
        attempts = attempt + 1
        p = prompt if rejection is None else add_rejection(prompt, rejection)
        with progress.tool(progress.TOOL_LLM,
                           model=llm.MODEL,
                           attempt=attempts,
                           prompt_chars=len(p)) as t:
            try:
                raw = llm.call(client, SYSTEM_PROMPT, p)
            except Exception as exc:  # noqa: BLE001
                last_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                t.fail(last_error)
                rejection = "上一次调用失败：%s" % last_error
                continue

            decision = llm.extract_json(raw)
            t.output({"chars": len(raw or ""), "parsed": decision is not None})

        if decision is None:
            progress.log("第 %d 次模型输出不是合法 JSON，重试" % attempts, level="warn")
            rejection = "输出不是合法 JSON"
            continue

        suggestions = llm.as_suggestions(decision.get("suggestions"))
        result = validate(decision, facts)
        if not result["ok"]:
            progress.log("第 %d 次校验未通过：%s" % (
                attempts, "；".join(result["violations"])[:120]), level="warn")
        if result["ok"]:
            last_error = None
            break
        rejection = "；".join(result["violations"]) or "自校验未通过"

    if result is None or not result.get("ok"):
        out = _fail(
            last_error or rejection or "未能给出该词组的逐位讲解",
            attempts,
            None if result is None else result["violations"],
        )
        out["char"] = anchor
        return out

    explanation = build_explanation(facts, result, suggestions)

    return {
        "char": anchor,
        "phrase": anchor,
        "rule_label": facts["rule"],
        "code": result["code"],
        "code_source": "caller",
        "positions": result["positions"],
        "explanation": explanation,
        "suggestions": suggestions,
        "confidence": result["confidence"],
        "conflicts": [],
        "sources": [],
        "error": None,
        "self_check": {
            "ok": True,
            "attempts": attempts,
            "violations": [],
            "confidence": result["confidence"],
        },
        "model": llm.MODEL,
        "elapsed_ms": int((time.time() - started) * 1000),
    }

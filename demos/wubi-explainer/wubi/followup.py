"""
追问层：把「同一会话里的后续提问」答成**确定性结论**。

为什么单独一层：上一轮的 output 已经由平台落库、并原样注回
``config.conversation.turns``，所以「第 2 位为什么取日」这类问题的答案本来就
躺在历史里 —— 再采一次事实、再问一次模型，只会引入新的编造机会。
本层因此只用三处信息：

  1. 上一轮 ``output.positions``：该位的结论（radical / reason / recognition）
  2. 上一轮 ``input.positions`` ：该位当时的候选字根（调用方给的材料）
  3. 本轮 ``question`` 文本    ：确定性解析位序与字根，不做超出这一步的猜测

三类回答（见输出里的 ``followup.kind``）：

  * ``why``     「为什么第 2 位是目不是具？」→ 引用该位结论，并把「不是 X」与候选集对比
  * ``revise``  「不对，第 3 位应该是土」    → 在候选集内则修正该位并标注分歧；不在则拒绝并说明依据
  * ``unknown`` 解析不出位序 / 字根        → 给出上一轮摘要与可用问法，**绝不猜**

输出结构与正常拆字完全一致（char/code/positions/explanation/...），
这样第三轮注入时结构不变，调用方也只需要解析一套字段。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import answer, compose, llm

# 追问轮的两种形态，输出里的 model 必须如实反映是哪一种：
#   * reasoned     —— 调了模型（answer.reason），model = 真实模型名
#   * 确定性降级   —— 模型不可用时用历史作答，model = MODEL_FIELD
MODEL_FIELD = "no-model(followup)"

# 「不对，第 3 位应该是土」这类纠正
_REVISE_WORDS = ("不对", "错", "应该", "改成", "改为", "更正", "纠正", "修正")
# 「为什么 / 依据」这类解释请求
_WHY_WORDS = ("为什么", "为何", "怎么", "如何", "依据", "理由", "区别", "区分", "怎么区分")

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_POS_RE = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*(?:位|个|码|键)")
_LAST_RE = re.compile(r"最后一位|末位|最后那一位")
_QUOTED_RE = re.compile(r"[「『“\"'‘]([^」』”\"'’]{1,6})[」』”\"'’]")
_NEG_RE = re.compile(r"(?:不是|不取|不用|而非|而不是)\s*[「『“\"'‘]?([\u4e00-\u9fff]{1,3})[」』”\"'’]?")
_ASSERT_RE = re.compile(
    r"(?:应该是|应是|应取|改成|改为|取|用|是)\s*[「『“\"'‘]?([\u4e00-\u9fff]{1,3})[」』”\"'’]?"
)

# 修正后置信度的折减：采纳调用方主张意味着与码表/模型依据不一致，必须体现出来
_REVISE_CONFIDENCE_PENALTY = 0.2


@dataclass
class Question:
    """确定性解析出来的追问意图。"""

    kind: str = "unknown"
    index: int | None = None  # 0 起
    glyph: str | None = None  # 主张 / 讨论的字根
    alt: str | None = None  # 「不是 X」里的 X
    last: bool = False  # 用了「最后一位 / 末位」
    text: str = ""
    notes: list[str] = field(default_factory=list)  # 解析过程中的说明（定位方式等）


def parse_question(text: str) -> Question:
    """从自然语言里抠出「第几位 + 哪个字根 + 是纠正还是解释」。

    只认有限的几种说法（第 N 位 / 最后一位 / 引号里的字根 / 应该是 X / 不是 X）。
    抠不出来的部分保持为 None，由上层走 help 分支——不猜。
    """
    q = Question(text=(text or "").strip())
    t = q.text

    m = _POS_RE.search(t)
    if m:
        token = m.group(1)
        if token.isdigit():
            q.index = int(token) - 1
        elif token in _CN_NUM:
            q.index = _CN_NUM[token] - 1
    elif _LAST_RE.search(t):
        q.last = True

    neg = _NEG_RE.search(t)
    if neg:
        q.alt = neg.group(1)

    quoted = _QUOTED_RE.findall(t)
    if quoted:
        # 引号里的第一个词是「讨论对象」；若它恰好等于「不是 X」的 X，则其实是否定项
        q.glyph = quoted[0]
        if q.alt and q.glyph == q.alt:
            q.glyph = None
    else:
        m2 = _ASSERT_RE.search(t)
        if m2:
            q.glyph = m2.group(1)

    if any(w in t for w in _REVISE_WORDS) and (q.glyph or q.alt):
        q.kind = "revise"
    elif any(w in t for w in _WHY_WORDS) or q.glyph or q.alt:
        q.kind = "why"
    return q


# ========== 上下文提取 ==========


def last_success_turn(turns: list | None) -> dict | None:
    """取最近一轮「有结论」的历史轮（平台注入的轮次都是已成功的）。"""
    for turn in reversed(turns or []):
        if not isinstance(turn, dict):
            continue
        out = turn.get("output")
        if isinstance(out, dict) and out.get("code") and out.get("positions"):
            return turn
    return None


def _candidates_at(prev_input: dict, index: int) -> list[str]:
    """上一轮第 index 位的候选字根（调用方当时给的材料）。"""
    positions = (prev_input or {}).get("positions")
    if not isinstance(positions, list) or index >= len(positions):
        return []
    item = positions[index]
    if not isinstance(item, dict):
        return []
    out = []
    for cand in item.get("candidates") or []:
        glyph = cand.get("glyph") if isinstance(cand, dict) else cand
        if isinstance(glyph, str) and glyph.strip():
            out.append(glyph.strip())
    return out


def _position_summary(positions: list) -> str:
    parts = []
    for p in positions:
        if p.get("recognition"):
            label = "识别码(%s)" % (p.get("recognition_stroke") or "?")
        else:
            label = p.get("radical") or "未确定"
        parts.append(f"第 {p['index'] + 1} 位 {p['key']}={label}")
    return "、".join(parts)


def correction_hint(question: str, turns: list | None) -> dict | None:
    """本轮若是「纠正某一位」，用**确定性代码**核对字根是否在该位候选集内。

    这个判断不能交给模型：候选集里常并列着形近字根（日 / 曰 只差一笔），
    实测模型会把「曰」看成「日」，于是把在候选内的纠正误判成候选外而拒绝执行。
    所以这里先核对，把结论当事实写进提示，模型只负责措辞与理由。
    """
    q = parse_question(question)
    if q.kind != "revise" or not q.glyph:
        return None

    prev_turn = last_success_turn(turns)
    if prev_turn is None:
        return None
    positions = prev_turn["output"]["positions"]

    index = q.index
    if index is None and q.last:
        index = len(positions) - 1
    if index is None:
        index, _ = _infer_index(turns, positions, q.glyph)
        if index is None:
            return None
    if not (0 <= index < len(positions)):
        return None

    cands = _candidates_for(turns, index)
    glyph = q.glyph
    return {
        "index": index,
        "key": positions[index].get("key"),
        "glyph": glyph,
        "candidates": cands,
        "in_candidates": (glyph in cands) if cands else None,  # None = 历史里没有候选集
    }


def _candidates_for(turns: list, index: int) -> list[str]:
    """本位候选集：候选只在**完整拆字轮**的 input 里给过。

    第二轮的 input 只有 char + question，第三轮追问时如果只看最近一轮就没有
    候选集可对照了 —— 所以这里往旧轮次回溯，取最近一次真给过候选的那轮。
    """
    for turn in reversed(turns or []):
        if not isinstance(turn, dict):
            continue
        cands = _candidates_at(turn.get("input") or {}, index)
        if cands:
            return cands
    return []


def _infer_index(turns: list, positions: list, glyph: str) -> tuple[int | None, list[int]]:
    """没写位序时，用候选集反推：只命中一处才敢用。"""
    hits = [
        i
        for i, p in enumerate(positions)
        if glyph == p.get("radical") or glyph in _candidates_for(turns, i)
    ]
    return (hits[0] if len(hits) == 1 else None), hits


# ========== 输出组装 ==========


def _base_output(prev_out: dict, positions: list, *, char: str, confidence, conflicts, error) -> dict:
    """沿用上一轮的元信息（编码 / 来源 / 码表版本），只替换本轮变动的部分。"""
    return {
        "char": char,
        "code": prev_out.get("code"),
        "code_source": prev_out.get("code_source"),
        "positions": positions,
        "explanation": "",
        "suggestions": list(prev_out.get("suggestions") or []),
        "confidence": confidence,
        "conflicts": conflicts,
        "sources": list(prev_out.get("sources") or []),
        "table_digest": prev_out.get("table_digest"),
        "error": error,
        "self_check": {
            "ok": error is None,
            "attempts": 0,  # 追问不调用模型
            "violations": [],
            "confidence": confidence,
            "mode": "followup",
        },
        "model": MODEL_FIELD,
        "elapsed_ms": 0,
        # 模型路径（answer.reason）会覆盖这两个字段；确定性降级时保持为空
        "citations": [],
        "new_claims": [],
    }


def _fail(char: str, error: str, started: float) -> dict:
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
        "self_check": {"ok": False, "attempts": 0, "violations": [], "mode": "followup"},
        "model": MODEL_FIELD,
        "elapsed_ms": int((time.time() - started) * 1000),
        "citations": [],
        "new_claims": [],
        "followup": {"kind": "unknown", "answered": False, "llm_used": False},
    }


# ========== 三类回答 ==========


def _answer_why(
    char: str, prev_out: dict, index: int, q: Question, candidates: list[str], turn_index
) -> str:
    pos = prev_out["positions"][index]
    key = pos.get("key")
    n = index + 1
    if pos.get("recognition"):
        current = "末笔识别码（%s）" % (pos.get("recognition_stroke") or "?")
    else:
        current = "「%s」" % (pos.get("radical") or "未确定")

    lines = [f"「{char}」的第 {n} 位（键 {key}）取 {current}。"]
    if pos.get("reason"):
        lines.append(f"依据（第 {turn_index} 轮的结论）：{pos['reason']}")
    else:
        lines.append(f"依据：第 {turn_index} 轮未给出该位的取舍理由，请以码表为准。")

    cand_text = " / ".join(candidates) if candidates else ""
    if q.alt:
        if cand_text and q.alt not in candidates:
            lines.append(
                f"至于「{q.alt}」：它不在键 {key} 的候选字根里（候选：{cand_text}），"
                "所以这一位取不到它。"
            )
        elif cand_text:
            lines.append(
                f"「{q.alt}」确实也在键 {key} 的候选中（{cand_text}），"
                f"但本字实际含有的是 {current} —— 见上面的依据。"
            )
        else:
            lines.append(f"「{q.alt}」不在本位的结论里；本轮没有候选集可对照。")
    elif q.glyph and cand_text and q.glyph not in candidates:
        lines.append(
            f"「{q.glyph}」不在键 {key} 的候选字根里（候选：{cand_text}），因此这一位不会取它。"
        )

    lines.append(f"（结论来自本会话第 {turn_index} 轮，未重新推理、未新增任何事实。）")
    return "\n".join(lines)


def _revise(
    char: str, prev_out: dict, index: int, glyph: str, candidates: list[str], turn_index
) -> dict:
    pos = prev_out["positions"][index]
    key = pos.get("key")
    n = index + 1
    old = pos.get("radical") or ("识别码" if pos.get("recognition") else "未确定")
    cand_text = " / ".join(candidates) if candidates else "（上一轮未提供候选集）"

    if candidates and glyph not in candidates:
        # 不在候选集内：拒绝，并把依据原样回给用户，不迁就
        error = f"第 {n} 位（键 {key}）的候选里没有「{glyph}」"
        out = _base_output(
            prev_out,
            [dict(p) for p in prev_out["positions"]],
            char=char,
            confidence=prev_out.get("confidence"),
            conflicts=list(prev_out.get("conflicts") or []),
            error=error,
        )
        lines = [
            f"第 {n} 位不能按「{glyph}」修正：它不在键 {key} 的候选字根里（候选：{cand_text}）。",
        ]
        if pos.get("reason"):
            lines.append(f"该位的依据仍然是：{pos['reason']}")
        lines.append(
            "如果你确认编码有误，请核对 code 与该位的候选字根后，重新发起一次完整拆字。"
        )
        out["explanation"] = "\n".join(lines)
        out["followup"] = {
            "kind": "revise",
            "answered": True,
            "accepted": False,
            "index": index,
            "glyph": glyph,
            "previous_radical": old,
            "based_on_turn": turn_index,
            "llm_used": False,
        }
        return out

    # 采纳修正：改写该位，并把它记成「调用方主张 vs 上一轮结论」的分歧
    new_pos = dict(pos)
    new_pos["radical"] = glyph
    new_pos["recognition"] = False
    new_pos["recognition_stroke"] = None
    new_pos["unresolved"] = False
    new_pos["reason"] = f"按调用方主张取「{glyph}」（第 {turn_index} 轮的结论是「{old}」）"
    positions = [new_pos if i == index else dict(p) for i, p in enumerate(prev_out["positions"])]

    conflicts = list(prev_out.get("conflicts") or [])
    conflicts.append(
        {
            "field": "position[%d]" % index,
            "value": glyph,
            "sources": ["caller"],
            "note": "调用方主张与上一轮结论不一致，已按调用方主张修正",
        }
    )
    prev_conf = prev_out.get("confidence")
    confidence = (
        max(0.05, round(prev_conf - _REVISE_CONFIDENCE_PENALTY, 2))
        if isinstance(prev_conf, (int, float))
        else prev_conf
    )
    out = _base_output(
        prev_out, positions, char=char, confidence=confidence, conflicts=conflicts, error=None
    )
    result = {
        "code": out["code"],
        "code_source": out["code_source"],
        "positions": positions,
        "ok": True,
        "violations": [],
        "conflicts": conflicts,
        "confidence": confidence,
    }
    body = compose.build(char, result, {}, out["suggestions"])
    header = (
        f"已按你的纠正修正：第 {n} 位（键 {key}）由「{old}」改为「{glyph}」，"
        f"置信度下调至 {confidence}（与码表依据不一致，请核对）。"
    )
    out["explanation"] = header + "\n" + body
    out["followup"] = {
        "kind": "revise",
        "answered": True,
        "accepted": True,
        "index": index,
        "glyph": glyph,
        "previous_radical": old,
        "based_on_turn": turn_index,
        "llm_used": False,
    }
    return out


def _answer_help(
    char: str, prev_out: dict, q: Question, reason: str, turn_index, hits=None
) -> dict:
    out = _base_output(
        prev_out,
        [dict(p) for p in prev_out["positions"]],
        char=char,
        confidence=prev_out.get("confidence"),
        conflicts=list(prev_out.get("conflicts") or []),
        error=reason,
    )
    lines = [f"没能把「{q.text}」对应到具体某一位或某个字根，所以不做猜测。"]
    if q.glyph and hits is not None:
        if len(hits) == 0:
            lines.append(f"「{q.glyph}」不在任何一位的候选集里。")
        elif len(hits) > 1:
            spots = "、".join(f"第 {i + 1} 位" for i in hits)
            lines.append(f"「{q.glyph}」在 {spots} 的候选里都出现，请指明是哪一位。")
    positions = prev_out["positions"]
    lines.append(f"当前结论：{char} = {prev_out.get('code')}；{_position_summary(positions)}")
    lines.append("可以这样问：")
    lines.append("  - 「第 2 位为什么取『目』不取『具』？」")
    lines.append("  - 「不对，第 3 位应该是『土』」")
    out["explanation"] = "\n".join(lines)
    out["followup"] = {
        "kind": "unknown",
        "answered": False,
        "based_on_turn": turn_index,
        "llm_used": False,
    }
    return out


def _deterministic(
    char: str, question: str, turns: list, prev_out: dict, turn_index
) -> dict:
    """确定性回答（模型不可用时的降级路径）。

    只用三处信息：上一轮 output 的结论、上一轮 input 的候选集、本轮问题的确定性解析。
    """
    positions = prev_out["positions"]
    q = parse_question(question)

    index = q.index
    hits = None
    if index is None and q.last:
        index = len(positions) - 1
        q.notes.append("按「最后一位」定位")
    if index is None:
        glyph = q.glyph or q.alt
        if not glyph:
            return _answer_help(
                char, prev_out, q, "没能从问题里定位到「第几位」或「哪个字根」", turn_index, hits
            )
        index, hits = _infer_index(turns, positions, glyph)
        if index is None:
            reason = (
                f"「{glyph}」不在任何一位的候选集里，无法定位"
                if not hits
                else f"「{glyph}」在多位的候选里都出现，无法确定是哪一位"
            )
            return _answer_help(char, prev_out, q, reason, turn_index, hits)
        q.notes.append(f"按候选集定位到第 {index + 1} 位")

    if not (0 <= index < len(positions)):
        return _answer_help(
            char,
            prev_out,
            q,
            f"第 {index + 1} 位超出本字编码长度（{len(positions)} 位）",
            turn_index,
            hits,
        )

    candidates = _candidates_for(turns, index)

    if q.kind == "revise" and q.glyph:
        out = _revise(char, prev_out, index, q.glyph, candidates, turn_index)
    else:
        out = _base_output(
            prev_out,
            [dict(p) for p in positions],
            char=char,
            confidence=prev_out.get("confidence"),
            conflicts=list(prev_out.get("conflicts") or []),
            error=None,
        )
        out["explanation"] = _answer_why(char, prev_out, index, q, candidates, turn_index)
        out["followup"] = {
            "kind": "why",
            "answered": True,
            "index": index,
            "glyph": q.glyph,
            "alt": q.alt,
            "based_on_turn": turn_index,
            "llm_used": False,
        }
    if q.notes:
        out["followup"]["notes"] = q.notes
    return out


def _reasoned_output(char: str, reasoned: dict, prev_out: dict, turn_index) -> dict:
    """把模型回答组装成与拆字同构的输出（引用 / 新增断言都如实落到字段与讲解里）。"""
    positions = reasoned["positions"]
    conflicts = list(prev_out.get("conflicts") or []) + list(reasoned["conflicts"])

    confidence = prev_out.get("confidence")
    if reasoned["conflicts"] and isinstance(confidence, (int, float)):
        # 本轮改动了某一位：与上一轮结论不一致，置信度要体现出来
        confidence = max(0.05, round(confidence - _REVISE_CONFIDENCE_PENALTY, 2))

    out = _base_output(
        prev_out, positions, char=char, confidence=confidence, conflicts=conflicts, error=None
    )
    lines = [reasoned["answer"]]
    if reasoned["citations"]:
        lines.append("依据（引用自本会话历史）：" + "、".join(reasoned["citations"]))
    if reasoned["new_claims"]:
        lines.append("本次新增（未经交叉核验）：" + "；".join(reasoned["new_claims"]))
    lines.extend(reasoned["notes"])
    out["explanation"] = "\n".join(line for line in lines if line)
    out["citations"] = reasoned["citations"]
    out["new_claims"] = reasoned["new_claims"]
    out["model"] = llm.MODEL
    out["self_check"] = {
        "ok": True,
        "attempts": reasoned["attempts"],
        "violations": [],
        "confidence": confidence,
        "mode": "followup-reasoned",
    }
    out["followup"] = {
        "kind": "reasoned",
        "answered": True,
        "based_on_turn": turn_index,
        "llm_used": True,
        "citations": len(reasoned["citations"]),
        "new_claims": len(reasoned["new_claims"]),
    }
    return out


def handle(char: str, question: str, turns: list | None, started: float) -> dict:
    """追问入口：先让模型基于历史事实作答，模型不可用时降级为确定性回答。"""
    prev_turn = last_success_turn(turns)
    if prev_turn is None:
        return _fail(
            char,
            "追问需要上下文：请先在同一会话里做一次完整拆字（char + code/positions），再追问；"
            "不同字的提问请各建一个会话，避免串味",
            started,
        )

    turn_index = prev_turn.get("index")
    prev_input = prev_turn.get("input") or {}
    prev_out = prev_turn["output"]

    prev_char = (prev_out.get("char") or prev_input.get("char") or "").strip()
    if char and prev_char and char != prev_char:
        return _fail(
            char,
            f"追问的字「{char}」与上一轮的「{prev_char}」不一致：请为新字新建会话，避免串味",
            started,
        )
    char = char or prev_char

    # ── 1) 模型路径：历史作为「已核实事实块」注入，引用必须可核对 ──
    reasoned = answer.reason(
        char, question, turns, correction=correction_hint(question, turns)
    )
    if reasoned is not None:
        out = _reasoned_output(char, reasoned, prev_out, turn_index)
        out["elapsed_ms"] = int((time.time() - started) * 1000)
        return out

    # ── 2) 降级：模型不可用（没配 key / 调用失败）→ 用历史确定性作答 ──
    out = _deterministic(char, question, turns, prev_out, turn_index)
    note = "（本次未能调用模型，以下为基于上一轮历史的确定性回答，未新增任何事实。）"
    if out.get("explanation"):
        out["explanation"] = note + "\n" + out["explanation"]
    out["followup"]["fallback"] = True
    out["elapsed_ms"] = int((time.time() - started) * 1000)
    return out

"""
确定性校验层：把「模型的判断」和「客观事实」对齐。

职责边界（整个设计的核心）：
  * 编码由事实决定，不是由模型决定。本地码表 / 联网检索只要有 >=2 个来源一致，
    就直接采信；模型即使给出别的码，也只记成冲突并降低置信度，不允许覆盖权威值。
  * 字根由模型挑，但必须落在该键的字根表内 —— 纯规则校验，不需要模型。
  * 调用方给的 certain（成字字根这类「算法已确定」的情况）直接强制采纳。

于是模型能编的只剩「为什么选这个字根」这句解释，而它也被事实约束（见 decide.py）。
"""
from __future__ import annotations

from . import tables

# 来源权重：仅在「各来源互相矛盾」时用来挑主值
_SOURCE_PRIORITY = ("local", "tavily", "caller", "agent")

# 末笔识别码：字根不足四个时，用末笔笔形补一位。映射是固定的、可外部核验的，
# 因此可以拿来校验模型的声明 —— 声明的笔形与所在键位对不上，就是编的。
#
# !! 是「区」，不是一个键 !!
# 识别码 = 末笔笔形（区）× 字型（位：左右/上下/杂合），所以每个笔形对应**三个**键，
# 一共 15 个。这里只按区校验，不判字型 —— 字型结构需要字形分析，本层拿不到；
# 而「区」这一层已经足够挡住编造（声称末笔折却落在 a 键）。此前只写左右型那一列
# （横→f、竖→h、撇→t、捺→u、点→y、折→n），会让**上下型/杂合型的正确识别码
# 也被判违规** —— 如「邑」kcb（折×上下 → b）、「只」kcu（捺×杂合 → i）。
#
# 「点」按捺区取码（点即捺），两者同区。
RECOGNITION_ZONE = {
    "横": ("g", "f", "d"),
    "竖": ("h", "j", "k"),
    "撇": ("t", "r", "e"),
    "捺": ("y", "u", "i"),
    "点": ("y", "u", "i"),
    "折": ("n", "b", "v"),
}

# 键位 → 该键在识别码里代表的笔形（提示词用：末位是识别码键时告诉模型该填哪个笔形名）
RECOGNITION_STROKE_BY_KEY = {
    "g": "横", "f": "横", "d": "横",
    "h": "竖", "j": "竖", "k": "竖",
    "t": "撇", "r": "撇", "e": "撇",
    "y": "捺", "u": "捺", "i": "捺",
    "n": "折", "b": "折", "v": "折",
}


def recognition_zone(stroke: str) -> tuple:
    """该笔形可用于识别码的键位（整区；字型未知，故不下钻到具体键）。"""
    return RECOGNITION_ZONE.get((stroke or "").strip(), ())


def is_recognition_claim_valid(stroke: str, key: str) -> bool:
    """「末笔 X 的识别码落在键 Y」这句声明是否成立。"""
    return key in recognition_zone(stroke)


def pick_code(facts: dict, agent_code):
    """定编码：事实优先，模型垫底。返回 (code, source, conflicts)。"""
    claims = dict(facts.get("claims") or {})
    if agent_code:
        claims["agent"] = agent_code

    if not claims:
        return None, "none", []

    groups = {}
    for name, code in claims.items():
        groups.setdefault(code, []).append(name)

    distinct = list(dict.fromkeys(claims.values()))
    conflicts = []
    if len(distinct) > 1:
        # 记录分歧：谁说了什么，如实上报，而不是悄悄吞掉
        conflicts = [
            {"field": "code", "value": code, "sources": sorted(srcs)}
            for code, srcs in sorted(groups.items())
        ]
        for code, srcs in sorted(groups.items()):
            if len(srcs) >= 2:      # >=2 个来源一致 -> 采信共识
                return code, "consensus(" + ",".join(sorted(srcs)) + ")", conflicts

    for name in _SOURCE_PRIORITY:
        if name in claims:
            return claims[name], name, conflicts

    return next(iter(claims.values())), "agent_only", conflicts


def _clamp(x, lo=0.05, hi=0.99):
    return max(lo, min(hi, x))


def _pickable(facts: dict, key: str, glyph: str) -> bool:
    """这个字形在该键位上是否可选（含变形等价）。

    IDS 把偏旁记作「牛」而模型看字形写「牜」，指的是同一个东西，不该互相否决。
    """
    char = facts.get("char") or ""
    allowed = {r["glyph"] for r in ((facts.get("radicals") or {}).get(key) or [])}
    allowed |= tables.glyphs_of(key)
    return bool(tables.glyph_variants(glyph) & allowed)


def validate(decision: dict, facts: dict, certain=None):
    """校验模型决策 + 定最终编码 + 算置信度。"""
    violations = []
    model_code = (decision.get("code") or "").strip().lower() or None
    if model_code and not tables.is_valid_code(model_code):
        violations.append("模型给出的编码不合法: %r" % model_code)
        model_code = None

    final_code, code_source, conflicts = pick_code(facts, model_code)
    if not final_code or not tables.is_valid_code(final_code):
        return {
            "ok": False, "code": None, "positions": [],
            "violations": violations + ["没有任何可用来源给出合法编码"],
            "conflicts": conflicts, "confidence": 0.0, "code_source": code_source,
        }

    certain = certain or {}
    raw_positions = decision.get("positions")
    raw_positions = raw_positions if isinstance(raw_positions, list) else []
    if len(raw_positions) != len(final_code):
        violations.append(
            "positions 项数 %d 与编码位数 %d 不一致" % (len(raw_positions), len(final_code))
        )

    positions = []
    n_codes = len(final_code)
    for i, key in enumerate(final_code):
        item = raw_positions[i] if i < len(raw_positions) else None
        item = item if isinstance(item, dict) else {}

        allowed = {r["glyph"] for r in ((facts.get("radicals") or {}).get(key) or [])}
        forced = certain.get(str(i)) or certain.get(key)
        radical = (item.get("radical") or "").strip()
        is_rec = bool(item.get("recognition"))
        stroke = (item.get("recognition_stroke") or "").strip()
        reason = str(item.get("reason") or "")[:120]
        unresolved = False

        if not allowed:
            violations.append("第 %d 位键 %s 在字根表里没有字根" % (i + 1, key))
            radical, is_rec, unresolved = "", False, True
        elif forced:
            # 算法已确定（成字字根/键名字）：直接采纳，不给模型自由发挥
            radical = forced
            is_rec = False
            reason = reason or "该字本身就是这一键的成字字根，直接采用"
        elif is_rec and radical:
            violations.append(
                "第 %d 位同时给了字根「%s」和识别码声明，自相矛盾" % (i + 1, radical))
            radical, unresolved = "", True
        elif is_rec:
            if i != n_codes - 1:
                violations.append(
                    "第 %d 位声明为识别码，但识别码只能出现在最后一位" % (i + 1))
                unresolved = True
            elif not stroke:
                violations.append("第 %d 位声明为识别码，却没给 recognition_stroke" % (i + 1))
                unresolved = True
            elif not recognition_zone(stroke):
                violations.append(
                    "第 %d 位声明为识别码，但笔形名「%s」不在横/竖/撇/捺/点/折里"
                    % (i + 1, stroke))
                unresolved = True
            elif not is_recognition_claim_valid(stroke, key):
                # 补一句「那该做什么」：这句 violation 会原样回到模型的重试提示里，
                # 只说「不符」模型常常换一个笔形再声明一次（仍然必拒），
                # 说清「这一位是真实字根」才有机会让它改走正确路线。
                allowed_glyphs = " / ".join(sorted(allowed)) or "无"
                violations.append(
                    "第 %d 位自称末笔「%s」的识别码（应落在 %s 键），实际落在键 %s，不符："
                    "键 %s 不在识别码的 15 个键里，这一位是**真实字根**，"
                    "请从该键候选里挑一个（可选：%s）"
                    % (i + 1, stroke, " / ".join(recognition_zone(stroke)), key,
                       key, allowed_glyphs))
                unresolved = True
            else:
                reason = reason or ("字形上无对应部件，按末笔%s的识别码取 %s" % (stroke, key))
        elif not radical:
            # 既不给字根、也不声明是识别码 —— 沉默是最糟的一种，不给依据就用它等于编造
            violations.append("第 %d 位既没有给字根，也没有声明是识别码" % (i + 1))
            unresolved = True
        elif not _pickable(facts, key, radical):
            # 用事实层实际给到模型的候选集校验，保证「判合法」与「可选」是同一份清单。
            # 判定走等价归并：IDS 把偏旁记作「牛」而模型看字形写「牜」，同一个东西，不算错。
            violations.append(
                "第 %d 位字根「%s」不在键 %s 的候选里（可选：%s）"
                % (i + 1, radical, key, " / ".join(sorted(allowed)) or "无")
            )
            radical, unresolved = "", True

        # 无论是否被否决都保留该位：positions 长度必须等于码长，
        # 否则调用方按位对齐会错位。否决掉的位置 radical 为 null、unresolved 为 true。
        ok_pos = not unresolved
        positions.append({
            "index": i, "key": key,
            "radical": radical or None,
            "recognition": is_rec and ok_pos,
            "recognition_stroke": stroke if (is_rec and ok_pos) else None,
            "unresolved": unresolved,
            "reason": reason,
        })

    # 置信度完全由客观信号算出，模型说了不算
    facts_map = facts.get("facts", {}) or {}
    agree = bool(facts.get("agree")) or (model_code is not None and model_code == final_code)
    score = 0.35
    if facts_map.get("local", {}).get("ok"):
        score += 0.2
    if facts_map.get("tavily", {}).get("ok"):
        score += 0.2
    if agree:
        score += 0.2
    if conflicts:
        score -= 0.25
    if violations:
        score -= 0.3
    # 有多少位没能给出可靠依据，置信度就该降多少 —— 讲解里会逐位标出来
    score -= 0.08 * sum(1 for p in positions if p.get("unresolved"))
    if not positions:
        score = 0.0

    return {
        "ok": bool(positions) and not violations,
        "code": final_code,
        "code_source": code_source,
        "positions": positions,
        "violations": violations,
        "conflicts": conflicts,
        "confidence": round(_clamp(score), 2),
    }

"""决策层：把事实交给模型，只让它做「挑字根 + 说理由」。

与旧版最大的差别在提示词的三条禁令 —— 旧版最大的错误来源就是模型在没有笔顺
数据的情况下自行编造笔画（"首笔为竖"），以及把编码位误当成字部件。
"""
from __future__ import annotations

from .verify import RECOGNITION_STROKE_BY_KEY, RECOGNITION_ZONE

SYSTEM_PROMPT = """你是五笔字型（86 版）的拆字老师。你会拿到一个汉字的**标准编码**（已经由码表/字典核实过），
以及**编码每一位对应键位上的全部候选字根**。你的活儿只有两件：
  1) 为每一位从候选中选出正确的字根；
  2) 用**字形**说明为什么是它、为什么不是同键的其他候选。

硬性约束（违反即为错误）：
1. 只能使用「事实」区块里给出的信息。事实里没有的东西，一律不要补充。
2. **严禁描述这个字的笔画书写过程与笔顺**（如"首笔为竖""第二笔横折"）。你没有笔顺数据，
   一旦提到就是编的，会直接被判定为不可用。
3. **编码位不等于字部件**。键名码/识别码/补位不代表某个具体的部件，
   不要把"第一位是键名码"说成"字的第一部分是…"。
4. positions 的项数必须等于编码位数，第 i 项的 key 必须等于编码第 i 个字符。
5. 每个 radical 必须逐字取自对应键的候选字根表（可以用简称，但字形要照抄）。
6. reason 只讲**字形上的取舍**（例如「日窄长、曰扁宽，这个字是窄长的那个」，
   或者「带钩的是虎字头，这里没有钩」），不要复述编码规则，不要提笔顺。
7. **有些位在字形上根本找不到对应部件**，那是末笔识别码（字根不足四个时用末笔笔形补一位）。
   识别码 = 末笔笔形（区）× 字型，键位按**区**取：横→g/f/d、竖→h/j/k、撇→t/r/e、
   捺(点)→y/u/i、折→n/b/v —— 共 15 个键，且**只会出现在最后一位**。
   如果某一位的键不在这 15 个里（如 a/x/m/q 等），那它一定是一个**真实的字根**，
   请正常从候选里挑一个字根，不要声明识别码。
   确实要声明识别码时：radical 留空（""）、recognition 填 true、recognition_stroke 填笔形名
   （横/竖/撇/捺/点/折）。**宁可承认"这一位没有部件"，也绝不要硬凑一个字根** ——
   凑出来的字根一旦与字形无关，就是在编造。
8. 只输出 JSON，不要任何前后缀、不要 Markdown 代码围栏。

输出 JSON 结构（字段名逐字照抄）：
{
  "code": "四到一位小写字母",
  "positions": [
    {"key": "r", "radical": "看", "reason": "这个字本身就是 R 键的成字字根"},
    {"key": "f", "radical": "", "recognition": true, "recognition_stroke": "横",
     "reason": "字形上找不到对应部件，按末笔横的识别码取 f"}
  ],
  "suggestions": ["给初学者的一句提醒"]
}"""


def build_user_prompt(char: str, facts: dict, hints: dict) -> str:
    lines = [f"汉字：{char}", ""]

    lines.append("事实（已核实，直接采用）：")
    facts_map = facts.get("facts", {}) or {}
    for name in ("local", "tavily", "caller"):
        f = facts_map.get(name)
        if not f:
            continue
        if f.get("ok") and f.get("code"):
            extra = ""
            if f.get("simple"):
                extra += f"，其中简码 {f['simple']}"
            lines.append(f"  - {name}：{f['code']}{extra}")
        else:
            lines.append(f"  - {name}：{f.get('error') or '不可用'}")

    lines.append("")
    lines.append("逐位候选字根（每一位只能从这里挑）：")
    code = (facts.get("claims", {}) or {}).get("local") or \
           (facts.get("distinct_codes") or [""])[0] or ""
    radicals = facts.get("radicals") or {}
    real_parts = facts.get("real_parts") or {}
    last = len(code) - 1
    for i, key in enumerate(code):
        items = radicals.get(key) or []
        glyphs = " / ".join(r["glyph"] for r in items) if items else "（无候选）"
        lines.append(f"  第 {i + 1} 位  键 {key}  候选：{glyphs}")

        real = real_parts.get(key) or []
        if real:
            lines.append(
                f"      ★ 这个字实际含有这些部件：{' / '.join(real)}——优先从它们里面挑"
            )
        stroke = RECOGNITION_STROKE_BY_KEY.get(key)
        if i == last and stroke:
            zone = " / ".join(RECOGNITION_ZONE.get(stroke, ()))
            lines.append(
                f"      ↑ 这一位是末位，键 {key} 属{stroke}区（识别码键 {zone} 之一）。"
                "若字形上真的找不到对应部件，就 radical 留空、recognition 填 true、"
                f"recognition_stroke 填「{stroke}」。"
                "宁可承认没有部件，也不要硬凑一个字根。"
            )

    notes = hints.get("notes") or []
    if notes:
        lines.append("")
        lines.append("调用方给的补充信息（仅作背景，不要据此改写编码）：")
        for n in notes:
            if isinstance(n, dict) and n.get("message"):
                lines.append(f"  - {n['message']}")

    err = hints.get("error")
    if err:
        lines.append("")
        lines.append(
            f"用户这次的错误：期望按 {err.get('expected')} 键，实际按了 {err.get('actual')} 键。"
            "如果这两键有候选字根，请在 suggestions 里提示易混点。"
        )

    lines.append("")
    lines.append(
        "现在给出这个字的编码、每一位选的字根，以及**基于字形**的取舍理由。"
    )
    return "\n".join(lines)


def add_rejection(prompt: str, rejection: str) -> str:
    return prompt + (
        "\n\n上一次的回答没有通过确定性校验，问题如下，请修正后重新给出完整 JSON：\n"
        f"  {rejection}"
    )


def parse_decision(text: str) -> dict | None:
    from . import llm

    return llm.extract_json(text)

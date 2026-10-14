"""
讲解组装：**句子由服务端拼，模型只给「理由片段」。

旧版让模型直接写整段讲解，它就会在段落里夹带编造的笔画/部件说法，
而且这些内容混在自然段里，用户与调用方都很难发现。现在讲解的结构固定为：
编码结论 + 逐位取舍 + 易混提醒 + 分歧/置信度提示，全部由确定性代码生成。
"""
from __future__ import annotations

_SOURCE_LABEL = {
    "local": "内置码表",
    "tavily": "联网字典",
    "caller": "调用方传入",
    "agent": "模型判断",
}


def _source_text(code_source: str) -> str:
    """把 code_source 翻成人话。

    consensus(...) 至少有两种：内置码表 + 联网字典一致，或内置码表 + 调用方传入一致。
    旧文案一律写成「码表与联网结果一致」——当联网源其实投了反对票（同一轮 conflicts
    里还会明写「联网字典说 xxx」）时，这两句会自相矛盾。所以按实际来源逐个列名。
    """
    if code_source.startswith("consensus"):
        names = [n for n in ("local", "tavily", "caller") if n in code_source]
        if names:
            return "、".join(_SOURCE_LABEL.get(n, n) for n in names) + "一致"
    return _SOURCE_LABEL.get(code_source, code_source)


def build(char: str, result: dict, facts: dict, suggestions: list) -> str:
    code = result.get("code") or ""
    positions = result.get("positions") or []
    lines = []

    lines.append(
        f"「{char}」的五笔编码是 {code or '（未确定）'}（依据：{_source_text(result.get('code_source', ''))}）。"
    )

    if positions:
        lines.append("逐位取舍：")
        for p in positions:
            reason = (p.get("reason") or "").strip()
            if p.get("unresolved"):
                # 这一位的说法已被校验否决，理由本身就是不可信的，不能展示给用户
                lines.append(
                    f"  第 {p['index'] + 1} 位 {p['key']}：（未能确定）"
                    "——本次没有给出可靠依据，请以码表为准"
                )
                continue
            if p.get("recognition"):
                # 识别码位不是字根，如实说清楚，别让它看起来像"选了个字"
                stroke = p.get("recognition_stroke") or "?"
                body = f"末笔识别码（{stroke}）——字形上没有对应部件"
                if reason:
                    body += f"（{reason}）"
            else:
                body = str(p.get("radical") or "")
                if reason:
                    body += f"——{reason}"
            lines.append(f"  第 {p['index'] + 1} 位 {p['key']}：{body}")

    if suggestions:
        uniq = [s for s in dict.fromkeys([s for s in suggestions if s])]
        if uniq:
            lines.append("易混提醒：")
            for s in uniq[:4]:
                lines.append(f"  - {s}")

    conflicts = result.get("conflicts") or []
    if conflicts:
        parts = []
        for c in conflicts:
            srcs = "、".join(_SOURCE_LABEL.get(s, s) for s in c.get("sources", []))
            parts.append(f"{srcs} 说 {c.get('value')}")
        lines.append("⚠️ 各来源编码不一致：" + "；".join(parts) + f"。已采用 {code}，建议核对后再使用。")

    conf = result.get("confidence")
    if isinstance(conf, (int, float)) and conf < 0.6:
        lines.append(
            f"ℹ️ 置信度较低（{conf:.2f}）：本次缺少可靠的交叉验证，讲解仅供参考，请以码表为准。"
        )

    return "\n".join(lines)

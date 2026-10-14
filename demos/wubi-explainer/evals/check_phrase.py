#!/usr/bin/env python3
"""
词级（词组）讲解的离线自检 —— **不依赖模型**。

为什么要有这个脚本
------------------
词级讲解的事实全部来自调用方（取码规则、逐位来源、候选字根），agent 只负责
「挑 + 说理由」。也就是说这层里**唯一可能出错的代码是归一化与校验**，
它们完全不需要模型就能验证。上线前跑一遍，比等一次十几秒的线上调用便宜得多。

覆盖：
  1. 路由判据（is_phrase_input）：词级入参进词级分支，单字入参不受影响；
  2. 事实归一（parse_input）：键 / 来源字 / 该字第几码的映射不能错位；
  3. 校验（validate）：编码改写、位数、键位、来源字、候选集、声明识别码、空字根；
  4. 组句（build_explanation）：规则、来源分组、补充说明都在正文里；
  5. 端到端分派（agent._dispatch）：词组入参不会掉进单字的「char 必须是一个汉字」拒绝。

用法：python evals/check_phrase.py   （退出码非 0 表示有问题）
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from wubi import llm, phrase, tables  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name if not detail else f"{name}：{detail}")


def candidates_for(key: str) -> list[dict]:
    """该键的字根候选（用 agent 自己的字根表，故本脚本不依赖前端）"""
    return [{"glyph": r["glyph"], "name": r.get("name", "")} for r in tables.radicals_of(key)]


# (词组, 词表码, 规则, [(键, 来源字, 该字在词里的位序, 是该字第几码)])
SAMPLES = [
    ("编码", "xydc", "二字词 · 每字取前 2 码",
     [("x", "编", 0, 0), ("y", "编", 0, 1), ("d", "码", 1, 0), ("c", "码", 1, 1)]),
    ("中国人", "klww", "三字词 · 前两字首码 + 末字前 2 码",
     [("k", "中", 0, 0), ("l", "国", 1, 0), ("w", "人", 2, 0), ("w", "人", 2, 1)]),
    ("中华人民共和国", "kwwl", "多字词 · 前三字首码 + 末字首码",
     [("k", "中", 0, 0), ("w", "华", 1, 0), ("w", "人", 2, 0), ("l", "国", 6, 0)]),
]


def payload(anchor: str, code: str, rule: str, spec, *, derived: str | None = None,
            skipped: list[str] | None = None) -> dict:
    return {
        "task": "explain-phrase",
        "char": anchor,
        "phrase": anchor,
        "code": code,
        "ruleLabel": rule,
        "derivedCode": code if derived is None else derived,
        "matchesTable": (code if derived is None else derived) == code,
        "skippedChars": skipped or [],
        "positions": [
            {
                "index": i,
                "key": key,
                "fromChar": char,
                "charIndex": char_index,
                "charCodeIndex": code_index,
                "candidates": candidates_for(key),
            }
            for i, (key, char, char_index, code_index) in enumerate(spec)
        ],
        "notes": [],
    }


def correct_decision(facts: dict) -> dict:
    return {
        "code": facts["code"],
        "positions": [
            {
                "key": p["key"],
                "from_char": p["fromChar"],
                "radical": p["candidates"][0]["glyph"],
                "reason": "字形上与之相符",
            }
            for p in facts["positions"]
        ],
        "suggestions": ["注意形近字根"],
    }


def main() -> int:
    print("1) 路由判据 is_phrase_input")
    for anchor, code, rule, spec in SAMPLES:
        check(f"「{anchor}」进词级分支", phrase.is_phrase_input(payload(anchor, code, rule, spec)))
    single = {"char": "照", "code": "jvko",
              "positions": [{"index": 0, "key": "j", "candidates": [{"glyph": "日"}]}]}
    check("单字入参不进词级分支", not phrase.is_phrase_input(single))
    check("追问轮（只有 char+question）不进词级分支",
          not phrase.is_phrase_input({"char": "编码", "question": "为什么不取第二码"}))

    print("\n2) 事实归一 parse_input")
    for anchor, code, rule, spec in SAMPLES:
        facts = phrase.parse_input(payload(anchor, code, rule, spec))
        check(f"「{anchor}」键位顺序", [p["key"] for p in facts["positions"]] == [k for k, *_ in spec])
        check(f"「{anchor}」来源字与位序",
              [(p["fromChar"], p["charIndex"], p["charCodeIndex"]) for p in facts["positions"]]
              == [(c, ci, kk) for _, c, ci, kk in spec])
        check(f"「{anchor}」候选非空", all(p["candidates"] for p in facts["positions"]))

    print("\n3) 校验 validate")
    anchor, code, rule, spec = SAMPLES[0]
    facts = phrase.parse_input(payload(anchor, code, rule, spec))
    ok = phrase.validate(correct_decision(facts), facts)
    check("正确断言通过", ok["ok"] and not ok["violations"], str(ok["violations"]))
    check("通过时逐位带出来源字", [p["from_char"] for p in ok["positions"]] == ["编", "编", "码", "码"])

    def violation_of(mutate) -> str:
        decision = correct_decision(facts)
        mutate(decision)
        result = phrase.validate(decision, facts)
        return "" if result["ok"] else "；".join(result["violations"])

    cases = [
        ("编码被改写", lambda d: d.update(code="xydx")),
        ("位数不符", lambda d: d["positions"].pop()),
        ("键位不符", lambda d: d["positions"][1].update(key="a")),
        ("来源字不符", lambda d: d["positions"][2].update(from_char="编")),
        ("字根不在候选里", lambda d: d["positions"][2].update(radical="龘")),
        ("声明识别码", lambda d: d["positions"][3].update(radical="", recognition=True)),
        ("该位空字根", lambda d: d["positions"][3].update(radical="")),
    ]
    for name, mutate in cases:
        text = violation_of(mutate)
        check(f"{name}被判违规", bool(text), text)

    print("\n4) 组句 build_explanation")
    body = phrase.build_explanation(facts, ok, ["注意形近字根"])
    check("正文带词组与编码", "「编码」" in body and "xydc" in body)
    check("正文带规则与来源分组", rule in body and "编 xy" in body and "码 dc" in body)
    check("正文带逐位来源标注", "第 1 位 x（「编」）" in body)
    check("正文带易混提醒", "易混提醒" in body)

    print("\n5) suggestions 归一化（模型把数组给成字符串时不能按字符拆开）")
    tips = llm.as_suggestions("记住‘编’字旁是绞丝旁")
    check("字符串当成一条建议", tips == ["记住‘编’字旁是绞丝旁"], str(tips))
    check("数组去重去空并截断到 4 条",
          llm.as_suggestions(["a", "a", "", "b", "c", "d", "e"]) == ["a", "b", "c", "d"])
    check("非字符串项被丢弃", llm.as_suggestions([1, None, "ok"]) == ["ok"], str(llm.as_suggestions([1, None, "ok"])))
    body3 = phrase.build_explanation(facts, ok, tips)
    check("易混提醒写成整句而不是散字", "- 记住‘编’字旁是绞丝旁" in body3)

    mismatch = phrase.parse_input(payload(anchor, "xydx", rule, spec, derived="xydc"))
    body2 = phrase.build_explanation(
        mismatch, {"code": "xydx", "positions": ok["positions"]}, [],
    )
    check("词表与推导不一致时如实说明（没人说过就补一条）", "按规则推导应为 xydc" in body2)

    with_note = payload(anchor, "xydx", rule, spec, derived="xydc")
    with_note["notes"] = [
        {"message": "词表收录为 xydx，按规则推导应为 xydc（词表对极少数词有特殊收录，以词表为准）"}
    ]
    body4 = phrase.build_explanation(
        phrase.parse_input(with_note), {"code": "xydx", "positions": ok["positions"]}, [],
    )
    check("调用方已说过就不再重复（这件事只说一遍）",
          body4.count("按规则推导应为 xydc") == 1, str(body4.count("按规则推导应为 xydc")))

    print("\n6) 端到端分派 agent._dispatch（无模型 key 也应给出可解释错误）")
    import agent  # noqa: E402  （放在最后：它只在被导入时才读环境变量）

    routed = agent._dispatch(payload(anchor, code, rule, spec))
    check("词组入参走词级分支（不是「char 必须是单个汉字」）",
          isinstance(routed, dict) and "单个汉字" not in str(routed.get("error")),
          str(routed.get("error")))

    print("\n" + ("全部通过" if not FAILURES else f"有 {len(FAILURES)} 项未通过"))
    for item in FAILURES:
        print(f"  - {item}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())

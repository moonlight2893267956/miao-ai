"""
追问能力回归：默认**不联网**（用伪造的模型响应），可选 --live 打真实模型。

为什么能离线跑：追问的推理层（wubi/answer.py）与模型之间只隔 ``llm.call()``，
把它换掉就能在不花 token 的前提下覆盖「引用校验 / 纠正挡拦 / 降级」这些真正会
出错的逻辑。真实模型的表现交给 ``--live`` 与 run_eval.py。

覆盖两条路径：
  * reasoned  —— 模型不可用以外的正常路径：事实块 + citations 校验 + position_revisions 挡拦
  * fallback  —— 模型不可用（没配 key）：降级为基于历史的确定性回答

用法（在 agent 目录内）：
    python3 evals/run_followup_eval.py            # 离线，秒级
    python3 evals/run_followup_eval.py --live     # 真调模型（需 LLM 环境变量）
退出码非 0 表示有用例不符合预期。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

import agent as agent_mod  # noqa: E402
from wubi import answer as answer_mod, llm  # noqa: E402

# 第 1 轮的真实形态：input 是调用方给的材料，output 是 agent 的结论（照/jvko）
TURN1_INPUT = {
    "char": "照",
    "code": "jvko",
    "positions": [
        {"key": "j", "candidates": [{"glyph": "昭"}, {"glyph": "日"}, {"glyph": "曰"}]},
        {"key": "v", "candidates": [{"glyph": "刀"}, {"glyph": "九"}, {"glyph": "女"}]},
        {"key": "k", "candidates": [{"glyph": "口"}, {"glyph": "川"}]},
        {"key": "o", "candidates": [{"glyph": "灬"}, {"glyph": "火"}]},
    ],
    "notes": [],
}
TURN1_OUTPUT = {
    "char": "照",
    "code": "jvko",
    "code_source": "local",
    "positions": [
        {"index": 0, "key": "j", "radical": "昭", "recognition": False, "recognition_stroke": None,
         "unresolved": False, "reason": "「照」字的上部正是「昭」，且「昭」在 J 键上作为成字字根。"},
        {"index": 1, "key": "v", "radical": "刀", "recognition": False, "recognition_stroke": None,
         "unresolved": False, "reason": "「昭」的右侧是「召」，其下部为「刀」。"},
        {"index": 2, "key": "k", "radical": "口", "recognition": False, "recognition_stroke": None,
         "unresolved": False, "reason": "「召」的上部是「口」。"},
        {"index": 3, "key": "o", "radical": "灬", "recognition": False, "recognition_stroke": None,
         "unresolved": False, "reason": "「照」的底部是四点底「灬」。"},
    ],
    "explanation": "「照」的五笔编码是 jvko（依据：内置码表）。",
    "suggestions": ["「照」拆为「昭」+「灬」，注意「昭」是成字字根。"],
    "confidence": 0.95,
    "conflicts": [],
    "sources": [{"source": "local", "code": "jvko", "detail": "内置码表 86-1"}],
    "table_digest": "86-1/test",
    "error": None,
    "self_check": {"ok": True, "attempts": 1, "violations": [], "confidence": 0.95},
    "model": "stub",
    "elapsed_ms": 5000,
}
TURNS = [{"index": 1, "input": TURN1_INPUT, "output": TURN1_OUTPUT}]

# 第二轮的 input 只有 char + question（没有候选集）：第三轮必须能回溯到第一轮去拿候选
TURN2_OUTPUT = dict(TURN1_OUTPUT)
TURN2_OUTPUT["explanation"] = "「照」的第 1 位（键 j）取 「昭」。"
TURNS_AFTER_WHY = TURNS + [
    {"index": 2, "input": {"char": "照", "question": "第 1 位为什么取「昭」？"},
     "output": TURN2_OUTPUT}
]


def _model_json(answer: str, citations=None, revisions=None, claims=None) -> str:
    return json.dumps(
        {
            "answer": answer,
            "citations": citations or [],
            "position_revisions": revisions or [],
            "new_claims": claims or [],
        },
        ensure_ascii=False,
    )


def _install_fake(responses: list):
    """把 llm 换成一个按顺序吐固定响应的假模型（str=返回文本，Exception=抛错）。"""
    calls = {"n": 0}

    def _call(client, system_prompt, user_prompt):  # noqa: ANN001
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        item = responses[i]
        if isinstance(item, Exception):
            raise item
        return item

    llm.API_KEY = "sk-fake"
    llm.build_client = lambda: object()
    llm.call = _call
    return calls


def _uninstall_fake():
    llm.API_KEY = ""
    llm.build_client = _real_build_client
    llm.call = _real_call


_real_build_client = llm.build_client
_real_call = llm.call


# 每个用例：问题 + 模型响应（None = 不装假模型，走降级）+ 期望
CASES = [
    # ── 模型路径（reasoned）──
    {
        "name": "reasoned：正常回答 + 有效引用 + 新增断言分开标注",
        "question": "为什么第 2 位是刀？",
        "model": [_model_json(
            "第 2 位（键 v）取「刀」：「昭」的右侧是「召」，它的下半正是「刀」。",
            citations=[{"turn": 1, "field": "positions[1].reason"}],
            claims=["「煦」的第 3 位是口"],
        )],
        "expect": {"mode": "followup-reasoned", "kind": "reasoned", "attempts": 1,
                   "citations": 1, "new_claims": 1, "error": None,
                   "contains": ["依据（引用自本会话历史）", "本次新增（未经交叉核验）",
                                "第 1 轮 positions[1].reason"]},
    },
    {
        "name": "reasoned：引用指到不存在的字段 → 打回重试后通过",
        "question": "为什么第 2 位是刀？",
        "model": [
            _model_json("编的", citations=[{"turn": 1, "field": "positions[9].reason"}]),
            _model_json("第二次才对", citations=[{"turn": 1, "field": "code"}]),
        ],
        "expect": {"mode": "followup-reasoned", "attempts": 2, "citations": 1,
                   "contains": ["第二次才对", "第 1 轮 code"]},
    },
    {
        "name": "reasoned：两次引用都无效 → 丢掉引用并如实标注",
        "question": "为什么第 2 位是刀？",
        "model": [_model_json("回答", citations=[{"turn": 9, "field": "positions[0].reason"}])],
        "expect": {"mode": "followup-reasoned", "citations": 0,
                   "contains": ["无法对应到历史字段，已丢弃"]},
    },
    {
        "name": "reasoned：纠正落在候选内 → 采纳 + 记冲突 + 降置信度",
        "question": "不对，第 1 位应该是「曰」",
        "model": [_model_json(
            "按你的纠正，第 1 位改为「曰」。",
            citations=[{"turn": 1, "field": "input.positions[0].candidates"}],
            revisions=[{"index": 0, "key": "j", "radical": "曰", "reason": "按调用方主张"}],
        )],
        "expect": {"mode": "followup-reasoned", "radical_at": [0, "曰"], "confidence": 0.75,
                   "conflicts": 1, "contains": ["已由「昭」修正为「曰」"]},
    },
    {
        "name": "reasoned：纠正落在候选内但模型拒不给出 revision → 系统补上并标注",
        "question": "不对，第 1 位应该是「曰」",
        "model": [_model_json("我不同意，第 1 位应当是「昭」。")],
        "expect": {"mode": "followup-reasoned", "attempts": 2, "radical_at": [0, "曰"],
                   "conflicts": 1,
                   "contains": ["模型未给出该修正，系统已按你的主张"]},
    },
    {
        "name": "reasoned：纠正落在候选外 → 挡下并说明候选集",
        "question": "不对，第 3 位应该是「土」",
        "model": [_model_json(
            "第 3 位应为「土」。",
            revisions=[{"index": 2, "key": "k", "radical": "土", "reason": "调用方主张"}],
        )],
        "expect": {"mode": "followup-reasoned", "radical_at": [2, "口"], "conflicts": 0,
                   "contains": ["候选里没有「土」", "该位保持不变"]},
    },
    {
        "name": "reasoned：需要新判断的问题（跨字比较）也能答，新增部分单独标注",
        "question": "「照」和「煦」的拆法差在哪？",
        "model": [_model_json(
            "「照」第 1 位取「昭」，而「煦」的第 1 位是「日」。",
            citations=[{"turn": 1, "field": "positions[0].radical"}],
            claims=["「煦」的第 1 位是日"],
        )],
        "expect": {"mode": "followup-reasoned", "claims": 1, "error": None,
                   "contains": ["本次新增（未经交叉核验）"]},
    },
    {
        "name": "reasoned：模型输出不是 JSON → 两次失败后降级为确定性回答",
        "question": "第 1 位为什么取「昭」不取「日」？",
        "model": ["这不是 JSON"],
        "expect": {"model": "no-model(followup)", "fallback": True,
                   "contains": ["未能调用模型", "第 1 位"]},
    },
    {
        "name": "reasoned：模型调用抛错 → 直接降级（不重试）",
        "question": "第 1 位为什么取「昭」不取「日」？",
        "model": [RuntimeError("boom")],
        "expect": {"model": "no-model(followup)", "fallback": True,
                   "contains": ["未能调用模型"]},
    },
    # ── 降级路径（不装假模型）──
    {
        "name": "fallback：why + 「不取 X」",
        "question": "第 1 位为什么取「昭」不取「日」？",
        "model": None,
        "expect": {"model": "no-model(followup)", "fallback": True, "error": None,
                   "contains": ["未能调用模型", "第 1 位", "候选"]},
    },
    {
        "name": "fallback：纠正在候选内 → 确定性采纳",
        "question": "不对，第 1 位应该是「曰」",
        "model": None,
        "expect": {"model": "no-model(followup)", "fallback": True, "radical_at": [0, "曰"],
                   "confidence": 0.75, "conflicts": 1, "error": None},
    },
    {
        "name": "fallback：纠正在候选外 → 确定性拒绝",
        "question": "不对，第 3 位应该是「土」",
        "model": None,
        "expect": {"model": "no-model(followup)", "fallback": True, "radical_at": [2, "口"],
                   "error_contains": "候选里没有"},
    },
    {
        "name": "fallback：未写位序 → 按候选集反推",
        "question": "不对，应该是「九」",
        "model": None,
        "expect": {"model": "no-model(followup)", "fallback": True, "radical_at": [1, "九"],
                   "error": None},
    },
    {
        "name": "fallback：第三轮仍能回溯候选集",
        "question": "第 3 位为什么不取「川」？",
        "turns": TURNS_AFTER_WHY,
        "model": None,
        "expect": {"model": "no-model(followup)", "fallback": True, "error": None,
                   "contains": ["川", "候选"]},
    },
    {
        "name": "fallback：问不到点上 → 提示问法，不猜",
        "question": "这个字怎么读？",
        "model": None,
        "expect": {"model": "no-model(followup)", "fallback": True, "error_contains": "没能",
                   "contains": ["当前结论", "可以这样问"]},
    },
    {
        "name": "无上下文：会话里还没有成功轮次（模型也救不了，直接报错）",
        "question": "第 1 位为什么取「昭」？",
        "turns": [],
        "model": [_model_json("x")],
        "expect": {"error_contains": "需要上下文"},
    },
    {
        "name": "防串味：追问的 char 与上一轮不一致",
        "question": "第 1 位为什么取「昭」？",
        "payload_char": "牡",
        "model": [_model_json("x")],
        "expect": {"error_contains": "不一致"},
    },
]

# 追问输出结构（两种路径必须一致，否则第 N+1 轮注入会变形）
FOLLOWUP_KEYS = {"char", "code", "code_source", "positions", "explanation", "suggestions",
                 "confidence", "conflicts", "sources", "table_digest", "error", "self_check",
                 "model", "elapsed_ms", "citations", "new_claims", "followup"}
# 无法作答时（无上下文 / 串味）：沿用 agent.py 的失败形态，另加 citations/new_claims/followup
FAIL_KEYS = {"char", "code", "code_source", "positions", "explanation", "suggestions",
             "confidence", "conflicts", "sources", "error", "self_check", "model",
             "elapsed_ms", "citations", "new_claims", "followup"}


def check(case: dict, out: dict) -> list[str]:
    exp = case["expect"]
    bad = []
    fu = out.get("followup") or {}
    if "error" in exp and out.get("error") != exp["error"]:
        bad.append(f"error={out.get('error')!r} 期望 {exp['error']!r}")
    if "error_contains" in exp and exp["error_contains"] not in (out.get("error") or ""):
        bad.append(f"error={out.get('error')!r} 期望包含 {exp['error_contains']!r}")
    if "mode" in exp and out["self_check"].get("mode") != exp["mode"]:
        bad.append(f"self_check.mode={out['self_check'].get('mode')!r} 期望 {exp['mode']!r}")
    if "kind" in exp and fu.get("kind") != exp["kind"]:
        bad.append(f"followup.kind={fu.get('kind')!r} 期望 {exp['kind']!r}")
    if "attempts" in exp and out["self_check"].get("attempts") != exp["attempts"]:
        bad.append(f"attempts={out['self_check'].get('attempts')} 期望 {exp['attempts']}")
    if exp.get("fallback") and not fu.get("fallback"):
        bad.append("followup.fallback 未标注降级")
    if "model" in exp and out.get("model") != exp["model"]:
        bad.append(f"model={out.get('model')!r} 期望 {exp['model']!r}")
    if exp.get("mode") == "followup-reasoned" and out.get("model") != llm.MODEL:
        bad.append(f"reasoned 路径的 model 应为真实模型名 {llm.MODEL!r}，实际 {out.get('model')!r}")
    if "citations" in exp and len(out.get("citations") or []) != exp["citations"]:
        bad.append(f"citations={len(out.get('citations') or [])} 期望 {exp['citations']}")
    if "new_claims" in exp and len(out.get("new_claims") or []) != exp["new_claims"]:
        bad.append(f"new_claims={len(out.get('new_claims') or [])} 期望 {exp['new_claims']}")
    if "claims" in exp and len(out.get("new_claims") or []) != exp["claims"]:
        bad.append(f"new_claims={len(out.get('new_claims') or [])} 期望 {exp['claims']}")
    if "radical_at" in exp:
        i, glyph = exp["radical_at"]
        got = (out.get("positions") or [{}])[i].get("radical")
        if got != glyph:
            bad.append(f"positions[{i}].radical={got!r} 期望 {glyph!r}")
    if "confidence" in exp and out.get("confidence") != exp["confidence"]:
        bad.append(f"confidence={out.get('confidence')!r} 期望 {exp['confidence']!r}")
    if "conflicts" in exp and len(out.get("conflicts") or []) != exp["conflicts"]:
        bad.append(f"conflicts={len(out.get('conflicts') or [])} 期望 {exp['conflicts']}")
    text = out.get("explanation") or ""
    for needle in exp.get("contains", []):
        if needle not in text:
            bad.append(f"explanation 缺少 {needle!r}")

    expected_keys = FAIL_KEYS if out.get("positions") is None else FOLLOWUP_KEYS
    if set(out) != expected_keys:
        bad.append(f"字段集不符 多={set(out) - expected_keys} 少={expected_keys - set(out)}")
    if out["self_check"].get("ok") is not (out.get("error") is None):
        bad.append("self_check.ok 与 error 不一致")
    return bad


def run_case(case: dict) -> dict:
    turns = case.get("turns", TURNS)
    cfg = {"conversation": {"id": "conv_eval", "turn": 2, "max_turns_injected": 10,
                            "truncated": False, "dropped_turns": 0, "turns": turns}}
    payload = {"char": case.get("payload_char", "照"), "question": case["question"]}
    responses = case.get("model")
    if responses:
        _install_fake(responses)
    else:
        _uninstall_fake()
    try:
        return agent_mod.invoke(payload, cfg)
    finally:
        _uninstall_fake()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="真调模型跑一遍（需要 LLM 环境变量）；默认离线，用伪造响应")
    args = ap.parse_args()

    if args.live:
        if not llm.API_KEY:
            print("未配置 LLM_API_KEY / DASHSCOPE_API_KEY，--live 无法运行")
            sys.exit(2)
        print(f"live 模式：真实模型 {llm.MODEL}")
        for case in (CASES[0], CASES[3]):
            out = agent_mod.invoke(
                {"char": "照", "question": case["question"]},
                {"conversation": {"id": "c", "turn": 2, "turns": TURNS}},
            )
            print(f"\n[{case['question']}]")
            print("model:", out["model"], "| mode:", out["self_check"]["mode"],
                  "| attempts:", out["self_check"]["attempts"],
                  "| citations:", out.get("citations"))
            print(out.get("explanation"))
        return

    failures = 0
    for case in CASES:
        out = run_case(case)
        bad = check(case, out)
        print(f"[{'OK ' if not bad else 'BAD'}] {case['name']}")
        print(f"      问：{case['question']}")
        head = (out.get("explanation") or out.get("error") or "").splitlines()[0][:100]
        print(f"      答：{head}")
        for b in bad:
            print(f"      x {b}")
        failures += bool(bad)

    # 完整材料 + question：必须走正常拆字路径（不装假模型，也不该出现 followup 字段）
    _uninstall_fake()
    out = agent_mod.invoke(
        {"char": "照", "code": "jvko", "positions": TURN1_INPUT["positions"],
         "question": "为什么第 1 位是昭？", "online": False},
        {},
    )
    if "followup" in out:
        print("[BAD] 完整材料 + question 不应走追问分支")
        failures += 1
    else:
        print("[OK ] 完整材料 + question → 正常拆字路径（追问文本只作背景）")

    print()
    if failures:
        print(f"{failures} 个用例不符合预期")
        sys.exit(1)
    print(f"全部追问用例通过（{len(CASES)} 例离线 + 1 例路径守卫）")


if __name__ == "__main__":
    main()

"""
候选集回归检查：调用方给的 candidates 必须是权威，且候选里不能混进整块部件。

为什么需要它：这是一个**真实发生过**的缺陷，而且只有自动检查才守得住 ——

  「编」（xyna）讲解反复失败，前端报「第 2 位字根「扁」不在 y 键的候选里」。
  根因是事实层把 IDS 拆解出的**整块部件**当成字根并进了该键候选：
  提示词写着「每一位只能从这里挑」，模型照单挑了「扁」；
  agent 自己的自检也用这份被扩过的清单，所以它认了 ——
  而调用方（五笔练习室）按 86 版字根表核对，每次都拒。
  抽样 3000 字，59% 的字至少有一位候选被这样污染。

本脚本不起模型、不联网，只验事实层与校验层的不变量：

  1. 调用方给了 positions[].candidates → facts.radicals[key] 必须**等于**它
  2. 整块部件（扁/昭）不得出现在任何候选里，也不得被判为可选
  3. 变形部件（IDS 写 A、表里是 B 的同义形）仍可并进候选
  4. 调用方没给候选时，退回本地推导，且同样不许混进整块部件
  5. real_parts 只保留落在候选集内的部件（否则提示词自相矛盾）

用法：
    python3 evals/check_candidates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from wubi import facts as facts_mod  # noqa: E402
from wubi import tables, verify  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def caller_positions_for(char: str, code: str) -> list[dict]:
    """按五笔练习室的语义构造调用方 positions（用 agent 自己的字根表模拟对方表）。"""
    out = []
    for i, key in enumerate(code):
        out.append({
            "index": i,
            "key": key,
            "candidates": [{"glyph": r["glyph"], "name": r["name"]}
                           for r in tables.radicals_of(key)],
        })
    return out


def glyphs(items) -> list[str]:
    return [r["glyph"] for r in items]


def main() -> int:
    print("候选集回归检查（无模型、无网络）\n")

    # ── 1. 「编」：调用方候选必须原样成为权威，整块部件「扁」不得入选 ──
    print("1) 编（xyna）—— 用户实际踩到的那个字")
    code = "xyna"
    facts = facts_mod.collect("编", caller_code=code, allow_online=False,
                              caller_positions=caller_positions_for("编", code))
    y = glyphs(facts["radicals"].get("y", []))
    check("调用方候选 = 权威候选（原样）", y == glyphs(tables.radicals_of("y")),
          f"y 键候选 = {' '.join(y)}")
    check("整块部件「扁」不在候选里", "扁" not in y)
    check("「扁」被判为不可选", verify._pickable(facts, "y", "扁") is False)
    check("合法字根「丶」仍可选", verify._pickable(facts, "y", "丶") is True)
    check("real_parts 不含候选集外的「扁」", "扁" not in facts["real_parts"].get("y", []),
          f"real_parts[y] = {facts['real_parts'].get('y', [])}")

    # ── 2. 「照」：同一类污染（昭）也必须消失 ──
    print("\n2) 照（jvko）—— 同一类污染")
    code = "jvko"
    facts = facts_mod.collect("照", caller_code=code, allow_online=False,
                              caller_positions=caller_positions_for("照", code))
    j = glyphs(facts["radicals"].get("j", []))
    check("整块部件「昭」不在候选里", "昭" not in j, f"j 键候选 = {' '.join(j)}")
    check("「昭」被判为不可选", verify._pickable(facts, "j", "昭") is False)
    check("合法字根「日」仍可选", verify._pickable(facts, "j", "日") is True)

    # ── 3. 校验层端到端：拿「扁」当 Y 键字根必须被判违规 ──
    print("\n3) verify.validate：bad 决策必须被拦下")
    decision = {
        "code": "xyna",
        "positions": [
            {"key": "x", "radical": "纟"},
            {"key": "y", "radical": "扁"},          # 整块部件
            {"key": "n", "radical": "尸"},
            {"key": "a", "radical": "艹"},
        ],
    }
    out = verify.validate(decision, facts_mod.collect(
        "编", caller_code="xyna", allow_online=False,
        caller_positions=caller_positions_for("编", "xyna")))
    check("「扁」触发违规", any("扁" in v for v in out["violations"]),
          "; ".join(out["violations"][:2]) or "（无违规）")
    check("该位被标为未解决", out["positions"][1]["unresolved"] is True)

    # ── 4. 没给候选时：退回本地推导，仍不得混进整块部件 ──
    print("\n4) 调用方未给候选（Try Run 等）—— 本地推导同样不得混进整块部件")
    facts = facts_mod.collect("编", caller_code="xyna", allow_online=False)
    y = glyphs(facts["radicals"].get("y", []))
    check("「扁」不在本地推导的候选里", "扁" not in y, f"y 键候选 = {' '.join(y)}")
    check("本地候选仍覆盖该键字根表", set(glyphs(tables.radicals_of("y"))) <= set(y))

    # ── 5. 变形归并仍然生效（别把功能一起改没了） ──
    print("\n5) 变形归并保留：字根表里的同义形仍可并进候选")
    merged = tables.all_radical_glyphs()
    variants = [g for g in tables._VARIANT_BASE if tables.is_radical_like(g)]
    check("存在可归并的变形字根", bool(variants),
          f"例：{' '.join(variants[:6])}")
    check("整块部件不算字根", not tables.is_radical_like("扁")
          and not tables.is_radical_like("昭"))
    check("真正的字根算字根", tables.is_radical_like("日") and tables.is_radical_like("丶"),
          f"全表字形 {len(merged)} 个")

    # ── 6. 规模回归：污染面必须显著下降 ──
    print("\n6) 规模回归：抽样字的候选中不得再出现「整块部件」")
    sample_chars = ["编", "照", "牡", "好", "语", "谢", "赢", "翻", "麟", "鑫"]
    polluted = []
    for ch in sample_chars:
        local = tables.lookup_code(ch)
        if not local:
            continue
        c = local["code"]
        f = facts_mod.collect(ch, caller_code=c, allow_online=False,
                              caller_positions=caller_positions_for(ch, c))
        for key, items in f["radicals"].items():
            for g in glyphs(items):
                # 单字部件若不在任何键的字根表里，就是被错误并入的整块部件
                if len(g) == 1 and g not in tables.all_radical_glyphs() \
                        and not tables.glyph_variants(g) & tables.all_radical_glyphs():
                    polluted.append((ch, key, g))
    check("抽样字未出现候选污染", not polluted, f"污染项 {polluted[:5]}" if polluted else "")

    print()
    if FAILED:
        print(f"❌ 失败 {len(FAILED)} 项：{'; '.join(FAILED)}")
        return 1
    print("✅ 候选集回归检查全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
五笔 agent 评测：分层抽样 + 自动打分。

为什么需要它：没有基准就没法证明「改动让它更准」。以后每次调整 agent 或换模型，
都跑一次同一份评测集，看指标变化。

用法（在 agent 目录内、且已具备 LLM 环境变量时）：
    python3 evals/run_eval.py --mode new --n 40
    python3 evals/run_eval.py --mode old --n 40     # 基线：给定候选的旧版

分层抽样覆盖四类易错场景：
  key      键名字/成字字根（字本身就是某键字根）
  simple   有简码的字（位数与全码不一致，最容易被讲错）
  bigkey   编码落在候选最多的几个键上（r/h/f/s/a…）
  full     四位全码的普通字
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from wubi import tables  # noqa: E402

# 编造判定：只抓「对这个字的笔顺/笔画下断言」这类硬编造。
# 注意「横线」「横折钩」是部件形状描述，不是笔顺断言，不算编造；
# 识别码位的「末笔」也已与服务端的固定映射交叉校验过，同样不算。
_STROKE_CLAIM = re.compile(
    r"首笔|末笔|起笔|收笔|笔顺|笔画|第[一二三四五六七八九十\d]+笔"
)



def build_set(n: int, seed: int = 20261011):
    codes = tables.load_codes()
    radicals = tables.load_radicals()

    key_chars, simple_chars, bigkey_chars, full_chars = [], [], [], []
    big_keys = {k for k, v in radicals.items() if len(v) >= 8}

    for char, item in sorted(codes.items()):
        code, level = item["code"], item["level"]
        if any(r["glyph"] == char for k in radicals for r in radicals[k]):
            key_chars.append(char)
        elif level and level < 4:
            simple_chars.append(char)
        elif any(c in big_keys for c in code):
            bigkey_chars.append(char)
        else:
            full_chars.append(char)

    rng = random.Random(seed)
    picked = []
    for pool, tag, want in (
        (key_chars, "key", max(6, n // 5)),
        (simple_chars, "simple", max(8, n // 3)),
        (bigkey_chars, "bigkey", max(8, n // 3)),
        (full_chars, "full", max(6, n // 5)),
    ):
        pool = pool or []
        picked += [(c, tag) for c in rng.sample(pool, min(want, len(pool)))]

    return [{"char": c, "stratum": t, "expected": codes[c]["code"]} for c, t in picked[:n]]


def old_payload(char: str, expected: str) -> dict:
    """旧版由调用方喂 code + 每位候选，这里按码表复原同样的输入。"""
    return {
        "char": char,
        "code": expected,
        "positions": [
            {
                "index": i,
                "key": k,
                "candidates": [{"glyph": r["glyph"], "name": r["name"]}
                               for r in tables.radicals_of(k)],
                "certain": char if any(r["glyph"] == char for r in tables.radicals_of(k)) else None,
            }
            for i, k in enumerate(expected)
        ],
        "notes": [],
    }


def has_fabrication(text: str) -> bool:
    return bool(_STROKE_CLAIM.search(text or ""))


def allowed_glyphs(char: str, key: str) -> set:
    """与 agent 事实层完全一致的候选集：字根表 ∪ 该字 IDS 部件。

    口径必须一致 —— 否则模型选了「牜」这类部件，会被评测误判成非法。
    """
    return {r["glyph"] for r in tables.candidates_for(char, key)}


def score_case(out: dict, expected: str, char: str) -> dict:
    code = out.get("code")
    positions = out.get("positions") or []
    # 结构完整：positions 长度必须等于码长，调用方才不会按位错位
    complete = bool(code) and len(positions) == len(code)
    # 无硬编造：任何「不在候选集内的字根」都是不可接受的
    illegal = [p for p in positions
               if p.get("radical")
               and not tables.is_allowed(char, p.get("key"), p["radical"])]
    valid = complete and not illegal
    # 只有模型自己写的字根理由才是"自由文本"；讲解正文由服务端确定性拼装，不参与判定
    model_text = " ".join(
        (p.get("reason") or "") for p in positions if not p.get("recognition")
    )
    return {
        "code": code,
        "code_match": code == expected,
        "positions_valid": valid,
        "n_positions": len(positions),
        "n_recognition": sum(1 for p in positions if p.get("recognition")),
        "n_unresolved": sum(1 for p in positions if p.get("unresolved")),
        "illegal_radicals": [p["radical"] for p in illegal],
        "positions": positions,
        "ok": bool((out.get("self_check") or {}).get("ok")),
        "confidence": out.get("confidence"),
        "fabrication": has_fabrication(model_text),
        "elapsed_ms": out.get("elapsed_ms"),
        "conflicts": len(out.get("conflicts") or []),
        "error": out.get("error"),
        "explanation": (out.get("explanation") or "")[:400],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["new", "old"], default="new")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--online", action="store_true", help="允许 Tavily 联网核验")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.mode == "new":
        import agent as agent_mod
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec_path = Path(os.environ.get("OLD_AGENT", "/tmp/agent_v4_backup.py"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("agent_old", spec_path)
        agent_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_mod)

    cases = build_set(args.n)
    rows = []
    started = time.time()
    for i, c in enumerate(cases, 1):
        payload = {"char": c["char"]}
        if args.mode == "new":
            payload["online"] = bool(args.online)
        else:
            payload = old_payload(c["char"], c["expected"])
        t = time.time()
        try:
            out = agent_mod.invoke(payload, {})
        except Exception as exc:  # noqa: BLE001
            out = {"error": "EXC %s: %s" % (type(exc).__name__, str(exc)[:200])}
        row = score_case(out, c["expected"], c["char"])
        row.update(char=c["char"], stratum=c["stratum"], expected=c["expected"],
                   wall_ms=int((time.time() - t) * 1000))
        rows.append(row)
        good = row["code_match"] and row["positions_valid"] and not row["fabrication"]
        flag = "OK " if good else "BAD"
        extra = ""
        if row["n_recognition"]:
            extra += " rec=%d" % row["n_recognition"]
        if row["n_unresolved"]:
            extra += " unres=%d" % row["n_unresolved"]
        print(f"[{i:2d}/{len(cases)}] {flag} {c['char']}({c['stratum']}) "
              f"exp={c['expected']} got={row['code']} valid={row['positions_valid']} "
              f"fab={int(row['fabrication'])} conf={row['confidence']}{extra} "
              f"{row['wall_ms']}ms", flush=True)

    n = len(rows)
    confs = [r["confidence"] for r in rows if isinstance(r["confidence"], (int, float))]
    summary = {
        "mode": args.mode,
        "cases": n,
        "code_match": sum(r["code_match"] for r in rows),
        "positions_valid": sum(r["positions_valid"] for r in rows),
        "no_fabrication": sum(not r["fabrication"] for r in rows),
        "self_check_ok": sum(r["ok"] for r in rows),
        "recognition_positions": sum(r["n_recognition"] for r in rows),
        "unresolved_positions": sum(r["n_unresolved"] for r in rows),
        "conflicts": sum(r["conflicts"] for r in rows),
        "avg_confidence": round(statistics.mean(confs), 2) if confs else 0,
        "avg_wall_ms": int(statistics.mean(r["wall_ms"] for r in rows)) if n else 0,
        "p90_wall_ms": int(sorted(r["wall_ms"] for r in rows)[int(n * 0.9) - 1]) if n else 0,
        "total_s": round(time.time() - started, 1),
    }
    print("\n=== 汇总 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    by_stratum = {}
    for r in rows:
        s = by_stratum.setdefault(r["stratum"], [0, 0])
        s[0] += 1
        s[1] += 1 if (r["code_match"] and r["positions_valid"] and not r["fabrication"]) else 0
    print("分层命中:", {k: f"{v[1]}/{v[0]}" for k, v in sorted(by_stratum.items())})

    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("明细已写入", args.out)


if __name__ == "__main__":
    main()

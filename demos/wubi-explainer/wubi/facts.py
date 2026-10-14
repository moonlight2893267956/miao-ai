"""事实采集层：先拿事实，再做判断。

agent 自己回答两个问题并把出处一并交给决策层：
  1. 这个字的标准五笔编码是什么？  → 内置码表（离线）+ Tavily 联网检索
  2. 涉及的每个键上有哪些字根？      → 内置 25 键字根表

多源并列保留、互不覆盖：一致则高置信度，不一致则把分歧原样上报。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from functools import lru_cache

from . import tables

TAVILY_ENDPOINT = "https://api.tavily.com/search"
SEARCH_TIMEOUT = 12.0

# 强证据：编码紧跟五笔/wubi 关键词，避免从英文散文抓到 code / face 这类 a-y 单词
_STRONG_PATTERNS = [
    r"五笔(?:编码|码|字型|拆字)?\s*(?:编码)?\s*[：:是为]?\s*([a-y]{1,4})\b",
    r"五笔\s*[（(]?\s*([a-y]{4})\s*[)）]",
    r"\bwubi\s*(?:code)?\s*[：:=]?\s*([a-y]{1,4})\b",
]

# 注：这里曾用一份手写英文停用词表过滤噪声，但永远追不上英文词汇
#（the/dog/hot/bad/car/end… 全部漏过），已改为按码表白名单校验，见 _valid。

def _valid(code: str) -> bool:
    """候选码是否可信。

    判据是「**这个码在码表里真实存在**」而不是「它不像英文单词」。
    手写黑名单永远追不上英文词汇（the/dog/hot/bad/car/end… 全漏过），
    而码表白名单来自 12452 字真实数据，既不会漏、也不会误伤合法编码。
    """
    if not re.fullmatch(r"[a-y]{1,4}", code):
        return False
    return code in known_codes()


@lru_cache(maxsize=1)
def known_codes() -> set:
    """码表里真实出现过的全部编码（含各级简码）。"""
    out: set = set()
    for item in tables.load_codes().values():
        out.add(item["code"])
        if item.get("simple"):
            out.add(item["simple"])
    return out


def extract_codes(*texts: str) -> list[str]:
    """从文本里抽候选五笔码（去重保序）：强模式优先，其次孤立 token 兜底。"""
    found: list[str] = []

    def _add(c: str) -> None:
        c = c.lower()
        if _valid(c) and c not in found:
            found.append(c)

    joined = "\n".join(t for t in texts if t)
    for pat in _STRONG_PATTERNS:
        for m in re.finditer(pat, joined, re.I):
            _add(m.group(1))
    if not found:
        for m in re.finditer(r"(?<![a-z])([a-y]{3,4})(?![a-z])", joined):
            _add(m.group(1))
    return found


def _tavily_key() -> str:
    return (os.environ.get("TAVILY_API_KEY") or "").strip()


def _overlaps(a: str, b: str) -> bool:
    """两个编码是否至少有一位落在同一个键上。

    五笔的取码规则对所有来源都一样，同一个字不同来源的编码不该毫无交集
    （简码与全码是前缀关系，天然重合前若干位）。一位都对不上，
    基本只可能是从网页散文里误抓的英文词 —— 如「照」的 jvko 对上 its。
    """
    return any(x == y for x, y in zip(a or "", b or ""))


def tavily_lookup(char: str, queries: list[str] | None = None,
                  local_code: str | None = None) -> dict:
    """向 Tavily 检索该字的五笔编码；任何失败都降级为 ok=False，不拖累主流程。

    联网结果是**辅助**来源，噪声很大（网页散文里的英文词 the / its / and
    往往也是合法的 a-y 短串，码表白名单挡不住），因此设两把锁，都通过才采信：

      1. **多源投票**：每个 query 独立提取，同一个码要被 >=2 个 query 命中。
         只出现一次的码一律当噪声丢弃 —— 这条能挡住与真实码重合的英文词。
      2. **与本地码有键位重叠**：同字不同来源的编码不该毫无交集。

    被丢弃的码记进 ``noise``（含原因与票数），便于事后审计「当时丢了什么、为什么」。
    两把锁都失败时返回 ok=False —— 宁可少一个交叉验证来源，也不放进假分歧。
    """
    key = _tavily_key()
    if not key:
        return {"ok": False, "error": "未配置 TAVILY_API_KEY", "code": None}

    queries = queries or [
        f"{char} 字的五笔编码是什么",
        f"五笔 {char} 拆字 字根 编码",
    ]
    votes: dict[str, int] = {}
    details: list[str] = []
    last_error = None

    for q in queries:
        payload = {
            "api_key": key, "query": q, "search_depth": "advanced",
            "max_results": 4, "include_answer": True,
        }
        req = urllib.request.Request(
            TAVILY_ENDPOINT, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
                data = json.load(resp)
        except Exception as exc:  # noqa: BLE001 — 外部依赖只降级、不抛出
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue

        texts = [str(data.get("answer") or "")]
        for item in (data.get("results") or [])[:4]:
            texts.append(str(item.get("content") or ""))
        for code in extract_codes(*texts):
            votes[code] = votes.get(code, 0) + 1
        details.append(f"{q} ({round(time.time() - started, 1)}s)")
        # 已经与本地码对上，无需再问下一个 query
        if local_code and votes.get(local_code):
            break

    if not votes:
        return {"ok": False, "code": None, "detail": "; ".join(details),
                "error": last_error or "检索结果里没有可识别的五笔编码"}

    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    noise: list[dict] = []
    picked = None
    for code, n in ranked:
        # 与本地码完全一致 —— 最强信号，无需再满足票数（命中它时已提前 break，
        # 此时票数必然是 1，不能用「>=2 票」去卡它）
        if local_code and code == local_code:
            picked = code
            break
        if len(queries) > 1 and n < 2:
            noise.append({"code": code, "votes": n, "reason": "只有一个来源命中"})
            continue
        if local_code and not _overlaps(code, local_code):
            noise.append({"code": code, "votes": n,
                          "reason": f"与本地码 {local_code} 无任何共同键位"})
            continue
        picked = code
        break

    if picked is None:
        return {"ok": False, "code": None, "candidates": [], "noise": noise,
                "detail": "; ".join(details),
                "error": "检索到的编码都没通过降噪校验（多为网页里的英文词）"}

    return {"ok": True, "code": picked, "candidates": [c for c, _ in ranked][:4],
            "votes": dict(ranked), "detail": "; ".join(details), "noise": noise}


def _caller_candidates(caller_positions) -> dict[str, list[dict]]:
    """把调用方 ``positions[].candidates`` 归并成 ``{key: [字形记录]}``。

    调用方（五笔练习室）传的是它自己的 25 键 86 版字根表 —— agent.yaml 的契约
    写明「调用方给了 candidates，模型只能从这里挑」。这份清单是**权威**：
    事实层不该再用自己推导的候选（含 IDS 部件归并）去扩大它，否则两侧对不上，
    调用方的核对会把 agent 认过的断言全部拒掉（「编」取「扁」当 Y 键字根就是这么来的）。
    """
    out: dict[str, list[dict]] = {}
    for p in caller_positions or []:
        if not isinstance(p, dict):
            continue
        key = str(p.get("key") or "").strip().lower()
        if not key:
            continue
        bucket = out.setdefault(key, [])
        seen = {c["glyph"] for c in bucket}
        for c in p.get("candidates") or []:
            glyph = c.get("glyph") if isinstance(c, dict) else c
            glyph = str(glyph or "").strip()
            if not glyph or glyph in seen:
                continue
            name = (c.get("name") if isinstance(c, dict) else None) or glyph
            seen.add(glyph)
            bucket.append({"glyph": glyph, "name": name,
                           "key_name": False, "variant": False})
    return out


def collect(char: str, caller_code: str | None = None, allow_online: bool = True,
            caller_positions=None) -> dict:
    """采集一个字的全部事实；永不抛异常，失败也要返回可降级的 facts。

    :param caller_positions: 调用方的 ``positions``（含 ``key``/``candidates``）。
        给了就以它的候选为**该键的权威候选**（契约见 agent.yaml）；
        没给才退回本地推导（字根表 ∪ 变形归并后的 IDS 部件）。
    """
    facts: dict[str, dict] = {}

    local = tables.lookup_code(char)
    facts["local"] = (
        {"ok": True, "code": local["code"], "simple": local.get("simple"),
         "level": local.get("level"), "source": f"内置码表 {tables.table_digest()}"}
        if local else {"ok": False, "code": None, "error": "内置码表里没有这个字"}
    )

    # 把本地码喂给联网核验做「键位重叠」这把锁（本地码表本就是主力来源）
    facts["tavily"] = (tavily_lookup(char, local_code=local["code"] if local else None)
                       if allow_online else
                       {"ok": False, "code": None, "error": "按参数要求跳过联网"})

    # 调用方给的码不算事实，只是「它认为的答案」——单列，便于报告分歧
    if caller_code:
        facts["caller"] = {"ok": True, "code": caller_code, "source": "调用方传入"}

    claims = {n: f["code"] for n, f in facts.items() if f.get("ok") and f.get("code")}
    distinct = list(dict.fromkeys(claims.values()))
    keys = sorted({c for code in distinct for c in code})

    caller_cands = _caller_candidates(caller_positions)
    radicals: dict[str, list] = {}
    for k in keys:
        if caller_cands.get(k):
            # 契约优先：调用方给的候选就是这一位的全部可选项
            radicals[k] = caller_cands[k]
        else:
            radicals[k] = tables.candidates_for(char, k)

    # 这个字实际含有的部件按键归位。
    # 只保留**落在本键候选集内**的部件：提示词会写「优先从它们里面挑」，
    # 候选集外的部件（如「编」里的整块「扁」）留在 parts 里做参考，但不能
    # 出现在这条「优先」提示里 —— 否则又是自相矛盾。
    real_parts: dict[str, list] = {}
    for p in tables.parts_of(char):
        k = tables.part_key(p)
        if not k or p in real_parts.setdefault(k, []):
            continue
        allowed = {r["glyph"] for r in radicals.get(k, [])}
        if p in allowed or (tables.glyph_variants(p) & allowed):
            real_parts[k].append(p)

    return {
        "char": char,
        "facts": facts,
        "claims": claims,
        "distinct_codes": distinct,
        "agree": len(distinct) == 1 and bool(distinct),
        "conflict": len(distinct) > 1,
        "radicals": radicals,
        "parts": tables.parts_of(char),
        "real_parts": real_parts,
    }
"""
内置数据表：五笔码表 + 25 键字根表。

这两张表是 agent「自己决策」的事实底座：
  - 码表回答「这个字的标准编码是什么」（离线、零延迟、可复现）
  - 字根表回答「某个键下到底有哪些字根」（用于确定性校验，而不是让模型自己编）

数据由 scripts/build_tables.py 从 miao-toolbox 的数据源生成，来源与许可见各 JSON 的 meta。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CODE_FILE = "wubi_codes.json"
RADICAL_FILE = "radicals.json"
PARTS_FILE = "wubi_parts.json"

# 五笔 86 版只有 25 个键，不含 z
VALID_KEYS = set("qwertyuiopasdfghjklzxcvbnm")


def _read_json(name: str) -> dict:
    path = DATA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(
            f"缺少内置数据表 {path}；它由 scripts/build_tables.py 生成，"
            f"请确认打包时 data/ 目录被一起带上"
        )
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_codes() -> dict:
    """char -> {"code": 全码, "level": 等级, "simple": 简码或 None}"""
    raw = _read_json(CODE_FILE)
    out: dict[str, dict] = {}
    for char, item in raw["codes"].items():
        code, level, simple = item[0], item[1], (item[2] if len(item) > 2 else None)
        out[char] = {"code": code, "level": level, "simple": simple}
    return out


@lru_cache(maxsize=1)
def load_radicals() -> dict:
    """key -> [{"glyph": 字形, "name": 名称, "key_name": bool, "variant": bool}, ...]"""
    raw = _read_json(RADICAL_FILE)
    out: dict[str, list] = {}
    for key, items in raw["keys"].items():
        parsed = []
        for item in items:
            glyph, name, key_name = item[0], item[1], bool(item[2])
            variant = bool(item[3]) if len(item) > 3 else False
            parsed.append({"glyph": glyph, "name": name,
                           "key_name": key_name, "variant": variant})
        out[key] = parsed
    return out


@lru_cache(maxsize=1)
def load_parts() -> dict:
    """char -> 该字在权威 IDS 拆解里的部件列表。

    这是「这个字字面上到底由什么构成」的客观依据，比字根表更贴近当前这个字：
    字根表只能回答「某键下有哪些字根」，回答不了「这个字有没有牜这样的偏旁」。
    缺了它，模型遇到含偏旁的字就会无解（牡 的第 2 位需要牜，字根表里却没有）。
    """
    return _read_json(PARTS_FILE).get("parts", {})


# 偏旁部件 -> 其本字。用于推断「这个部件该落在哪个键」：
# 偏旁本身不成字（如牜），但它的本字（牛）有编码，首字母即所属键。
_VARIANT_BASE = {
    "讠": "言", "亻": "人", "彳": "亻", "牜": "牛", "犭": "犬", "钅": "金",
    "忄": "心", "氵": "水", "灬": "火", "礻": "示", "衤": "衣", "饣": "人",
    "阝": "耳", "⻖": "耳", "⺮": "竹", "刂": "刂", "冫": "水", "飠": "人",
    "辶": "之", "冖": "冖", "虍": "虍", "癶": "癶", "匚": "匚", "彐": "彐",
    "龶": "龶", "㐅": "又", "纟": "丝", "艹": "艹", "冂": "冂", "勹": "勹",
    "扌": "手", "氺": "水",
}


@lru_cache(maxsize=1)
def _part_key_index() -> dict:
    """部件字形 -> 它所属的键（字根表归属优先，其次本字五笔码首字母）。"""
    index: dict[str, str] = {}
    for key, items in load_radicals().items():
        for r in items:
            index.setdefault(r["glyph"], key)
    return index


@lru_cache(maxsize=1)
def part_key(part: str) -> str | None:
    """推断一个 IDS 部件属于哪个键；判断不了返回 None。"""
    radical_index = _part_key_index()
    if part in radical_index:
        return radical_index[part]
    codes = load_codes()
    for cand in (_VARIANT_BASE.get(part, part), part):
        item = codes.get(cand)
        if item and item.get("code"):
            return item["code"][0]
    return None


def parts_of(char: str) -> list:
    """该字的 IDS 部件列表。"""
    return load_parts().get(char, [])


def glyph_variants(glyph: str) -> set:
    """一个字形的等价写法：它本身、它的本字、以及所有同本字的变形。

    IDS 拆解把「牡」记成 ⿰牛土，而模型看字形更可能写「牜」。
    两者指的是同一个偏旁，不能因为写法不同就判非法 ——
    86 版口诀只列基本字根，变形字根靠这里做等价归并。
    """
    out = {glyph}
    base = _VARIANT_BASE.get(glyph)
    if base:
        out.add(base)
    for variant, b in _VARIANT_BASE.items():
        if b == glyph or (base and b == base):
            out.add(variant)
    return out


def is_allowed(char: str, key: str, glyph: str) -> bool:
    """该键位上是否可选这个字形（含变形等价）。"""
    allowed = {r["glyph"] for r in candidates_for(char, key)}
    if not allowed:
        return False
    return bool(glyph_variants(glyph) & allowed)


@lru_cache(maxsize=1)
def all_radical_glyphs() -> frozenset:
    """25 键字根表全部字形的并集（判断一个 IDS 部件到底是不是字根时用）。"""
    return frozenset(
        r["glyph"] for k in "abcdefghijklmnopqrstuvwxy" for r in radicals_of(k)
    )


def is_radical_like(glyph: str) -> bool:
    """这个字形像不像「字根」。

    用于把 IDS 部件并入候选时做过滤 —— 这是修过的一个真实缺陷：

    原先 `candidates_for` 把所有 `part_key(part) == key` 的 IDS 部件都塞进候选，
    于是「编」的第二位候选里出现了**整块部件「扁」**（它不是任何键的字根），
    「照」的第一位出现了「昭」。提示词又明确写着「每一位只能从这里挑」，
    模型照单挑了它们；agent 自己的自检也用这份被扩过的清单，所以**它认了**。
    结果：调用方（五笔练习室，按 86 版字根表核对）每次都把这些断言拒掉 ——
    抽样 3000 字，59% 的字至少有一位候选被这样污染。

    规则：只有「本身就在某键字根表里」或「与某个字根等价（变形）」的部件才并进候选；
    整块部件仍然保留在 `real_parts` 里作为「这个字实际含有」的提示，不参与取舍。
    """
    if not glyph:
        return False
    return bool(glyph_variants(glyph) & all_radical_glyphs())


def candidates_for(char: str, key: str) -> list:
    """某个键位上，这个字真正可选的候选字根。

    = 该键的字根表  ∪  该字 IDS 部件中**属于字根**且归属此键的部件

    前者保证覆盖面（识别码位、IDS 拆不全时仍能用）；后者只做**变形归并**
    （IDS 写「牜」而表里是「牛」这类），不再把整块部件当作字根塞进来（见 is_radical_like）。
    """
    out = list(radicals_of(key))
    seen = {r["glyph"] for r in out}
    for part in parts_of(char):
        if part_key(part) != key or part in seen:
            continue
        if not is_radical_like(part):
            continue
        seen.add(part)
        out.append({"glyph": part, "name": part + "（本字部件）",
                    "key_name": False, "variant": part in _VARIANT_BASE})
    return out


@lru_cache(maxsize=1)
def codes_meta() -> dict:
    return _read_json(CODE_FILE).get("meta", {})


def lookup_code(char: str) -> dict | None:
    """查一个字的内置编码；没有则 None。"""
    return load_codes().get(char)


def radicals_of(key: str) -> list:
    """某个键下的全部字根（无此键返回空列表）。"""
    return load_radicals().get(key, [])


def glyphs_of(key: str) -> set:
    return {r["glyph"] for r in radicals_of(key)}


def is_valid_code(code: str | None) -> bool:
    """编码是否合法：非空、只含 a-y、长度 1~4。"""
    if not code or not isinstance(code, str):
        return False
    return len(code) <= 4 and all(c in VALID_KEYS for c in code)


def table_digest() -> str:
    """给输出里记一个数据版本指纹，便于事后复盘「当时用的是哪版码表」。"""
    meta = codes_meta()
    return f"{meta.get('v', '?')}/{meta.get('generated', '?')}"
"""
追问推理层：把「已核实的历史结论」摆成事实块交给模型，只让它回答本轮问题。

与主链的分工（两者都不越界）：
  * 主链 decide/verify：模型挑字根 + 给字形理由 → 规则层校验 → 编码由事实定。
  * 本层：编码与逐位字根**已经固定在历史里**，模型只做基于这些事实的问答，
    并且必须为每句陈述给出处（citations）。出处是程序化校验的 —— 指不到真实
    字段就打回重试，重试仍不行就丢掉并如实标注。

四条约束（写进 prompt，也由 validate() 兜底）：
  1. 不重新决定编码、不改历史里的字根；
  2. 关于「哪一位是什么 / 依据是什么」的陈述必须带 citations，且只能指向
     「可引用字段」清单（由 iter_citable_paths() 程序化生成，与校验同一份来源）；
  3. 纠正某一位要写成 position_revisions，且字根必须落在该位候选集内 ——
     候选集是调用方给过的材料，属于**权威事实**，模型不得覆盖；
  4. 需要新判断的内容（别的字、假设改动、规则解释）单独放 new_claims，
     由上层在讲解里标成「未经交叉核验」，不许混进事实叙述。

模型不可用（没配 key / 调用失败）时本层返回 None，由 followup 降级成确定性回答。
"""
from __future__ import annotations

import os

from . import llm

# 追问最多尝试几次（与主链一样：失败把原因喂回去再试一次）
MAX_ATTEMPTS = int(os.environ.get("WUBI_FOLLOWUP_ATTEMPTS", "2"))

SYSTEM_PROMPT = """你在回答用户对一个汉字五笔拆字结果的**追问**。

你会拿到本会话既往轮次的结论：编码、逐位字根与理由、以及该位允许的候选字根。
这些内容**已经核实过，是事实**：不要重新拆字，不要改编码，不要重新挑字根。

你的活儿只有一件：基于这些事实，把用户本轮的问题回答清楚。

硬性约束（违反即判为不可用）：
1. **不得推翻事实块里的编码与字根**。要引用它们，就照抄。
2. 每一句关于「哪一位是什么 / 依据是什么」的陈述，都必须在 citations 里给出处：
   格式 {"turn": 轮号, "field": "字段路径"}，且只能从「可引用字段」清单里逐字挑选。
   找不到出处的句子，宁可不写。
3. 如果用户是在**纠正**某一位（例如「不对，第 3 位应该是土」），先看该字根在不在该位候选集内：
   - **在候选集内** → **必须**给出 position_revisions：
     {"index": 位序(0 起), "key": 键, "radical": 字根, "reason": 一句字形理由}。
     即使你认为上一轮的字根更好，也要照用户的主张给出这一项（系统会把它记成
     「调用方主张 vs 上一轮结论」的分歧并下调置信度）；你的保留意见写在 answer 里。
   - **不在候选集内** → 不要给出 position_revisions，改为在 answer 里说明该位候选集是什么、
     为什么取不到它。候选集是调用方给过的权威事实，不许迁就。
4. new_claims 只放**超出历史事实**的内容：别的字怎么拆、把某位改掉会怎样、五笔规则的通用解释。
   对已有 positions 的论证与保留意见属于 answer 正文，不要塞进 new_claims。
5. **严禁**描述笔顺、笔画过程（"首笔为竖"这类），你没有笔顺数据。
6. 只输出 JSON，不要任何前后缀、不要 Markdown 代码围栏。

输出结构（字段名逐字照抄，没有内容就用空数组）：
{
  "answer": "回答正文，可分点，用 \\n 换行",
  "citations": [{"turn": 1, "field": "positions[0].reason"}],
  "position_revisions": [],
  "new_claims": []
}"""


# ========== 事实块与可引用字段（唯一来源，提示与校验共用） ==========


def iter_citable_paths(turns: list | None):
    """产出 (轮号, 字段路径, 该字段的值) —— 模型能引用的东西仅限这里。

    字段路径的写法（也是 citations.field 的合法取值）：
        code / explanation / positions[i].radical / positions[i].reason /
        positions[i].recognition / input.positions[i].candidates
    """
    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        turn_index = turn.get("index")
        out = turn.get("output") or {}
        inp = turn.get("input") or {}

        if out.get("code"):
            yield turn_index, "code", out["code"]
        if out.get("explanation"):
            yield turn_index, "explanation", out["explanation"]
        for i, pos in enumerate(out.get("positions") or []):
            if not isinstance(pos, dict):
                continue
            if pos.get("radical"):
                yield turn_index, f"positions[{i}].radical", pos["radical"]
            if pos.get("reason"):
                yield turn_index, f"positions[{i}].reason", pos["reason"]
            if pos.get("recognition"):
                yield turn_index, f"positions[{i}].recognition", pos.get("recognition_stroke") or True
        for i, pos in enumerate(inp.get("positions") or []):
            if not isinstance(pos, dict):
                continue
            cands = [
                c.get("glyph")
                for c in (pos.get("candidates") or [])
                if isinstance(c, dict) and c.get("glyph")
            ]
            if cands:
                yield turn_index, f"input.positions[{i}].candidates", " / ".join(cands)


def candidates_by_index(turns: list | None) -> dict[int, list[str]]:
    """本位候选集（往旧轮次回溯，取最近一次真给过候选的那轮）。"""
    out: dict[int, list[str]] = {}
    for turn_index, path, value in iter_citable_paths(turns):
        if not path.endswith(".candidates"):
            continue
        index = int(path.split("[")[1].split("]")[0])
        if index not in out:  # iter 是从旧到新，后面的轮次会覆盖 → 取最新一次
            out[index] = str(value).split(" / ")
        else:
            out[index] = str(value).split(" / ")
    return out


def build_facts_block(char: str, turns: list | None) -> str:
    """把历史摆成「事实块」：最新一轮给全（含理由），更早的轮次只给结论。"""
    valid_turns = [t for t in (turns or []) if isinstance(t, dict)]
    if not valid_turns:
        return f"汉字：{char}\n（本会话还没有既往结论）"

    lines = [f"汉字：{char}", "", "本会话既往结论（已核实，不需要你重新判断）："]
    cands = candidates_by_index(valid_turns)
    for offset, turn in enumerate(valid_turns):
        out = turn.get("output") or {}
        index = turn.get("index")
        is_latest = offset == len(valid_turns) - 1
        # 词级（词组）轮：把取码规则一起带上，否则追问时模型看不到「每字取几码」
        rule = (turn.get("input") or {}).get("ruleLabel") or out.get("rule_label") or ""
        anchor = out.get("phrase") or out.get("char")
        if rule:
            lines.append(
                f"  第 {index} 轮 · 词组「{anchor}」取码 {out.get('code')}"
                f"（来源 {out.get('code_source')}，{rule}）"
            )
        else:
            lines.append(f"  第 {index} 轮 · 编码 {out.get('code')}（来源 {out.get('code_source')}）")
        for pos in out.get("positions") or []:
            if not isinstance(pos, dict):
                continue
            if pos.get("recognition"):
                label = f"末笔识别码（{pos.get('recognition_stroke') or '?'}）"
            else:
                label = pos.get("radical") or "（未确定）"
            # 词级：注出来源字，追问「为什么不取某字的第二码」这类问题才有依据
            origin = f"（取自「{pos['from_char']}」）" if pos.get("from_char") else ""
            head = f"    第 {pos.get('index', 0) + 1} 位 {pos.get('key')}{origin}：{label}"
            if is_latest:
                head += f" —— 理由：{pos.get('reason') or '（无）'}"
                c = cands.get(pos.get("index", 0))
                if c:
                    head += f"｜该位候选：{' / '.join(c)}"
            lines.append(head)
    return "\n".join(lines)


# ========== 提示 ==========


def _citable_list(turns: list | None) -> str:
    lines = []
    for turn_index, path, value in iter_citable_paths(turns):
        shown = str(value).replace("\n", " ")
        if len(shown) > 60:
            shown = shown[:60] + "…"
        lines.append(f"  第 {turn_index} 轮 · {path}：{shown}")
    return "\n".join(lines) or "  （无可引用字段）"


def _correction_block(correction: dict | None) -> list[str]:
    """把「用户主张的字根是否在候选集内」这个**确定性结论**写进提示。

    这一条不该让模型自己判断：候选集里常并列形近字根（日 / 曰 差一笔），
    模型会看串，把在候选内的纠正误判成候选外而拒绝执行（实测过）。
    """
    if not correction:
        return []
    cands = correction.get("candidates") or []
    cand_text = " / ".join(cands) if cands else "（历史里没有候选集）"
    lines = [
        "",
        "系统已核对（确定性结论，以此为准，不要再自己判断）：",
        f"  用户主张：第 {correction['index'] + 1} 位（键 {correction.get('key')}）"
        f"换成「{correction['glyph']}」",
        f"  该位候选集：{cand_text}",
    ]
    if correction.get("in_candidates") is True:
        lines.append(
            "  结论：该字根**在**候选集内 → 必须给出 position_revisions"
            "（即使你有保留意见也要给出，保留意见写在 answer 里）。"
        )
    elif correction.get("in_candidates") is False:
        lines.append(
            "  结论：该字根**不在**候选集内 → 不要给出 position_revisions，"
            "在 answer 里说明候选集与原因。"
        )
    else:
        lines.append("  结论：历史里没有候选集可对照 → position_revisions 由你判断。")
    return lines


def build_user_prompt(
    char: str, question: str, turns: list | None, correction: dict | None = None
) -> str:
    lines = [
        build_facts_block(char, turns),
    ]
    lines += _correction_block(correction)
    lines += [
        "",
        "可引用字段（citations.field 只能从这里逐字挑）：",
        _citable_list(turns),
        "",
        f"用户本轮追问：{question}",
        "",
        "请按系统提示的 JSON 结构输出。",
    ]
    return "\n".join(lines)


def rejection_feedback(problems: list[str]) -> str:
    body = "\n".join(f"  - {p}" for p in problems)
    return (
        "\n\n上一次的回答没有通过校验，问题如下，请修正后重新输出完整 JSON：\n"
        f"{body}"
    )


# ========== 校验与归一 ==========


def validate(
    data: dict,
    allowed: dict[tuple, str],
    cands: dict[int, list[str]],
    correction: dict | None = None,
) -> list[str]:
    """程序化校验模型输出；返回问题清单（空 = 通过）。"""
    problems: list[str] = []

    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        problems.append("answer 必须是非空字符串")

    citations = data.get("citations")
    if citations is None:
        citations = []
        data["citations"] = citations
    if not isinstance(citations, list):
        problems.append("citations 必须是数组")
    else:
        for item in citations:
            if not isinstance(item, dict):
                problems.append(f"citations 项必须是对象：{item!r}")
                continue
            key = (item.get("turn"), item.get("field"))
            if key not in allowed:
                problems.append(
                    f"citations 指向了不存在的字段：第 {item.get('turn')} 轮 {item.get('field')!r}"
                    "（只能引用「可引用字段」清单里的项）"
                )

    revisions = data.get("position_revisions")
    if revisions is None:
        revisions = []
        data["position_revisions"] = revisions
    if not isinstance(revisions, list):
        problems.append("position_revisions 必须是数组")
    else:
        for item in revisions:
            if not isinstance(item, dict):
                problems.append(f"position_revisions 项必须是对象：{item!r}")
                continue
            idx = item.get("index")
            radical = item.get("radical")
            if not isinstance(idx, int) or isinstance(idx, bool):
                problems.append(f"position_revisions.index 必须是整数：{item!r}")
                continue
            positions_len = len(cands) or 0
            if positions_len and not (0 <= idx < max(positions_len, idx + 1)):
                problems.append(f"position_revisions.index 越界：{idx}")
            allowed_glyphs = cands.get(idx)
            if allowed_glyphs and radical not in allowed_glyphs:
                problems.append(
                    f"第 {idx + 1} 位候选里没有「{radical}」（候选：{' / '.join(allowed_glyphs)}），"
                    "不要给出这一项，改为在 answer 里说明"
                )

    claims = data.get("new_claims")
    if claims is None:
        data["new_claims"] = []
    elif not isinstance(claims, list) or any(not isinstance(c, str) for c in claims):
        problems.append("new_claims 必须是字符串数组")

    # 用户主张在候选集内的纠正必须落成 position_revisions（系统已在提示里告知结论）
    if correction and correction.get("in_candidates") is True:
        idx = correction["index"]
        has = any(
            isinstance(r, dict) and r.get("index") == idx
            for r in (data.get("position_revisions") or [])
        )
        if not has:
            problems.append(
                f"缺少必须的 position_revisions：用户主张第 {idx + 1} 位取"
                f"「{correction['glyph']}」，系统已核对该字根在候选集内"
            )

    return problems


def apply_revisions(positions: list, data: dict, cands: dict[int, list[str]]):
    """把通过校验的 position_revisions 落到 positions 上；返回 (新 positions, 冲突, 说明行)。"""
    revisions = [r for r in (data.get("position_revisions") or []) if isinstance(r, dict)]
    if not revisions:
        return [dict(p) for p in positions], [], []

    conflicts = []
    notes = []
    out = [dict(p) for p in positions]
    for item in revisions:
        idx = item.get("index")
        radical = item.get("radical")
        if not isinstance(idx, int) or not (0 <= idx < len(out)):
            notes.append(f"注：模型给出的位序 {idx} 越界，已忽略该修正。")
            continue
        old = out[idx].get("radical") or ("识别码" if out[idx].get("recognition") else "未确定")
        if radical == old:
            continue
        new_pos = dict(out[idx])
        new_pos["radical"] = radical
        new_pos["recognition"] = False
        new_pos["recognition_stroke"] = None
        new_pos["unresolved"] = False
        new_pos["reason"] = item.get("reason") or f"按本轮追问修正为「{radical}」（原为「{old}」）"
        out[idx] = new_pos
        conflicts.append(
            {
                "field": "position[%d]" % idx,
                "value": radical,
                "sources": ["followup"],
                "note": f"追问轮把第 {idx + 1} 位由「{old}」修正为「{radical}」",
            }
        )
        notes.append(f"注：第 {idx + 1} 位已由「{old}」修正为「{radical}」。")
    return out, conflicts, notes


# ========== 入口 ==========


def reason(
    char: str, question: str, turns: list | None, correction: dict | None = None
) -> dict | None:
    """让模型基于历史事实回答追问。

    :returns: ``{"answer","citations","new_claims","positions","conflicts","notes",
        "attempts","dropped_citations","rejected_revisions"}``；
        模型不可用（没 key / 建客户端或调用失败 / 输出无法解析）时返回 None，
        由调用方降级为确定性回答。
    """
    if not llm.API_KEY:
        return None
    try:
        client = llm.build_client()
    except Exception:  # noqa: BLE001 - 依赖缺失/配置错误都走降级，不抛给平台
        return None

    allowed = {(turn_index, path): value for turn_index, path, value in iter_citable_paths(turns)}
    cands = candidates_by_index(turns)
    prompt = build_user_prompt(char, question, turns, correction)

    parsed: dict | None = None
    problems: list[str] = []
    attempts = 0
    for attempt in range(MAX_ATTEMPTS):
        attempts = attempt + 1
        p = prompt if not problems else prompt + rejection_feedback(problems)
        try:
            raw = llm.call(client, SYSTEM_PROMPT, p)
        except Exception:  # noqa: BLE001 - 网络/鉴权失败：不重试，直接降级
            return None
        data = llm.extract_json(raw)
        if not isinstance(data, dict):
            problems = ["输出不是合法 JSON"]
            continue
        parsed = data
        problems = validate(data, allowed, cands, correction)
        if not problems:
            break

    if parsed is None:
        return None

    extra_notes: list[str] = []
    # 系统已核对该纠正在候选集内，但模型重试后仍没给出这一项：
    # 按调用方主张落到位（纠正要生效），并如实说明这一项是系统补的。
    if correction and correction.get("in_candidates") is True:
        idx = correction["index"]
        revs = parsed.get("position_revisions")
        if not isinstance(revs, list):
            revs = []
            parsed["position_revisions"] = revs
        if not any(isinstance(r, dict) and r.get("index") == idx for r in revs):
            revs.insert(
                0,
                {
                    "index": idx,
                    "key": correction.get("key"),
                    "radical": correction["glyph"],
                    "reason": f"按调用方主张取「{correction['glyph']}」（该字根在候选集内）",
                },
            )
            extra_notes.append(
                f"注：模型未给出该修正，系统已按你的主张把第 {idx + 1} 位改为"
                f"「{correction['glyph']}」（该字根在候选集内）。"
            )

    # 走到这里仍可能有残余问题（重试后依旧）：不丢弃整轮回答，只丢掉不可信的部分
    rejected_revisions = []
    if isinstance(parsed.get("position_revisions"), list):
        keep = []
        for item in parsed["position_revisions"]:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            glyph = item.get("radical")
            allowed_glyphs = cands.get(idx) if isinstance(idx, int) else None
            if allowed_glyphs and glyph not in allowed_glyphs:
                rejected_revisions.append(
                    {"index": idx, "glyph": glyph, "candidates": allowed_glyphs}
                )
            else:
                keep.append(item)
        parsed["position_revisions"] = keep

    dropped_citations = []
    if isinstance(parsed.get("citations"), list):
        keep_c, drop_c = [], []
        for item in parsed["citations"]:
            key = (item.get("turn"), item.get("field")) if isinstance(item, dict) else None
            (keep_c if key in allowed else drop_c).append(item)
        parsed["citations"] = keep_c
        dropped_citations = [
            f"第 {c.get('turn')} 轮 {c.get('field')}" for c in drop_c if isinstance(c, dict)
        ]

    prev_out = (turns[-1].get("output") or {}) if turns else {}
    positions, conflicts, notes = apply_revisions(
        prev_out.get("positions") or [], parsed, cands
    )
    for item in rejected_revisions:
        cand_text = " / ".join(item["candidates"])
        notes.append(
            f"注：第 {item['index'] + 1} 位（键 "
            f"{(prev_out.get('positions') or [{}])[item['index']].get('key') if prev_out.get('positions') else '?'}）"
            f"的候选里没有「{item['glyph']}」（候选：{cand_text}），该位保持不变。"
        )
    if dropped_citations:
        notes.append(
            "注：模型给出的 "
            + "、".join(dropped_citations)
            + " 无法对应到历史字段，已丢弃。"
        )

    return {
        "answer": parsed.get("answer") or "",
        "citations": [
            f"第 {c.get('turn')} 轮 {c.get('field')}"
            for c in parsed.get("citations") or []
            if isinstance(c, dict)
        ],
        "new_claims": [c for c in (parsed.get("new_claims") or []) if isinstance(c, str)],
        "positions": positions,
        "conflicts": conflicts,
        "notes": extra_notes + notes,
        "attempts": attempts,
        "dropped_citations": dropped_citations,
        "rejected_revisions": rejected_revisions,
    }

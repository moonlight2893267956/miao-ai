"""
wubi-explainer —— 五笔拆字解释 agent

职责：给定一个汉字的编码与**逐位候选字根**，解释「这个字为什么这么拆」。

── 设计红线：只讲已知事实，不产生事实 ──

调用方（五笔练习室）会把算法已经算出的东西全部送进来：编码、每一位对应哪个键、
该键上有哪些候选字根、已有的标注、以及用户这次按错了哪个键。
因此本 agent 的**发挥空间被限制在候选集内** —— 它无法说出一个不在该键上的字根。

请特别注意：不要试图「自己查五笔码表」或「凭记忆拆分」。编码是权威（由调用方给定），
agent 的活儿是**在给定候选里挑出最合理的那一个，并说明理由**。

── 返回值 ──

同步返回 dict（不返回 generator）：
    {
      "explanation": "……",
      "positions": [{"key": "j", "radical": "日"}, ...],   # 位数必须与编码一致
      "suggestions": ["……"],
      "model": "qwen-plus",
      "self_check": {"ok": true, "attempts": 1, "violations": []}
    }

调用方会**再校验一遍** `positions` 是否落在候选集内；这里先自校验并重试一次，
是为了把「能通过校验的完整断言」的比例提上去 —— 校验不过的会被前端退回确定性视图。

── 打包说明 ──

单文件打包，兼容平台 Docker 构建（仅 COPY agent.py，不会带入子目录）。
不要拆子模块，也不要写 fastapi/uvicorn/pydantic/langfuse 到 requirements.txt（平台已装）。
"""

import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger("wubi-explainer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

MODEL = os.environ.get("LLM_MODEL") or os.environ.get("DASHSCOPE_MODEL") or "qwen-plus"
API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or ""
BASE_URL = os.environ.get("LLM_BASE_URL") or os.environ.get("DASHSCOPE_BASE_URL") or ""
# !! 温度强制为 0，**刻意不采用注入值** !!
#
# 这个任务是结构化抽取：答案必须逐位落在给定的候选字根里，输出格式也必须严格。
# 而平台实测注入的是 0.7 —— 那恰恰是这类任务最不需要的东西（更容易编字根、更容易
# 破格式），模型的自由发挥对「答案对不对」毫无帮助，只会推高自校验的失败率。
# 文风的收益远小于「断言不可校验」的代价，所以这里硬编码。
TEMPERATURE = 0.0
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS") or os.environ.get("DASHSCOPE_MAX_TOKENS") or "2048")
# 平台同步调用超时 180s，这里留出余量；两次尝试共享这个预算
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "70"))
TOTAL_BUDGET_SEC = int(os.environ.get("WUBI_TOTAL_BUDGET", "170"))

SYSTEM_PROMPT = """你是五笔字型（86 版）的拆字讲解老师。

你会收到一个汉字的**完整编码**，以及**每一个码位对应的键位上全部候选字根**。

硬性约束（违反即为错误）：
1. 编码是权威，**不得改写**任何一位的键位。
2. 每一位的字根**只能从该位给出的候选字根里挑**，不许出现候选之外的任何字形。
3. `positions` 必须**给全每一位**（编码有几位就给几项），不允许因为不确定就缺项。
4. 只输出 JSON，不要任何解释性前后缀、不要 Markdown 代码围栏。

讲解要点（用中文，3~5 句，面向正在学五笔的人）：
- 先说这个字拆成了哪几个字根，按码位顺序。
- 再说明**怎么在候选里排除其他可能**（例如「日」与「曰」的区别、字形哪一块对上了）。
- 若给了「用户这次的错误」（期望键 → 实际键），**指名点出**这两个键上的字根为何容易混。
- 若标注里提到识别码 / 简码 / 键名字 / 成字字根，用一句话说明它对编码的影响。

输出 JSON 结构（**字段名必须逐字照抄**，尤其 positions 里是 key 与 radical 两个键）：
{
  "explanation": "「照」拆成 日 + 刀 + 口 + 灬。第一位取「日」而不是同键的「曰」，因为字形里是窄长的日……",
  "positions": [
    {"key": "j", "radical": "日"},
    {"key": "v", "radical": "刀"},
    {"key": "k", "radical": "口"},
    {"key": "o", "radical": "灬"}
  ],
  "suggestions": ["注意「日」与「曰」的区别：日窄曰宽"]
}

注意上面例子的几个要点（最常见的错法都在这）：
- positions 是**数组**，不是对象；每一项是 {"key": "...", "radical": "..."}，
  不要写成 "glyph" / "radicalGlyph" / "字根" 之类别的名字。
- key 是**小写字母**（就是编码那一位），radical 是**该位选中的字根字形**。
- positions 的**项数必须等于编码位数**，顺序与编码一致。"""


def _build_llm():
    """构造模型客户端。

    关闭 SDK 自带重试（max_retries=0）：重试由本文件控制，避免一次请求内部静默
    重试多次把平台 180s 超时吃光，最后连错误都来不及返回。

    !! langchain 在这里才 import（延迟导入）!!
    写在文件顶部的话，依赖没装好会在 import 期直接崩 —— 平台看到的是
    「进程起不来 / crashed」，健康检查失败，**任何有信息的错误都传不出来**。
    延迟导入后，同样的问题会变成一次正常的返回：{"error": "模型初始化失败：No module named …"}，
    在平台的 Try Run 里一眼可见。
    """
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = dict(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        api_key=API_KEY or "sk-placeholder",
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )
    if BASE_URL:
        kwargs["base_url"] = BASE_URL
    return ChatOpenAI(**kwargs)


def _strip_fence(text: str) -> str:
    """去掉模型习惯性加的 Markdown 代码围栏。"""
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")[1:]
    while lines and lines[-1].strip() in ("```", "``"):
        lines.pop()
    return "\n".join(lines).strip()


def _extract_json(text: str) -> dict | None:
    """从模型输出里取出 JSON 对象；取不到返回 None。"""
    cleaned = _strip_fence(text)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    # 兜底：截取第一个 { 到最后一个 } —— 模型偶尔会在 JSON 前后加一句话
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(cleaned[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ─── 输入校验 ──────────────────────────────────────────────


class BadInput(Exception):
    """输入不合法：这类错误是调用方的问题，直接如实回报，不要试图猜。"""


ZONE_KEYS = set("qwertyuiopasdfghjklzxcvbnm")


def _parse_input(input: dict) -> dict:
    char = input.get("char")
    code = input.get("code")
    raw_positions = input.get("positions")

    if not isinstance(char, str) or not char:
        raise BadInput("缺少 char")
    if not isinstance(code, str) or not code:
        raise BadInput("缺少 code")
    if not re.fullmatch(r"[a-z]+", code):
        raise BadInput(f"code 必须是纯小写字母，收到 {code!r}")
    if not isinstance(raw_positions, list) or len(raw_positions) != len(code):
        raise BadInput(f"positions 位数必须与编码一致（编码 {len(code)} 位）")

    positions = []
    for i, item in enumerate(raw_positions):
        if not isinstance(item, dict):
            raise BadInput(f"positions[{i}] 不是对象")
        key = item.get("key")
        if key != code[i]:
            raise BadInput(f"positions[{i}].key（{key!r}）与编码第 {i + 1} 位（{code[i]!r}）不一致")
        candidates = item.get("candidates") or []
        glyphs = [c.get("glyph") for c in candidates if isinstance(c, dict) and c.get("glyph")]
        positions.append({"index": i, "key": key, "role": item.get("role"), "glyphs": glyphs})

    notes = input.get("notes") or []
    error = input.get("error") or None
    if error is not None and not (isinstance(error, dict) and error.get("expected") and error.get("actual")):
        error = None

    return {"char": char, "code": code, "positions": positions, "notes": notes, "error": error}


def _build_user_prompt(data: dict, rejection: str | None = None) -> str:
    lines = [f"汉字：{data['char']}", f"完整编码：{data['code']}", ""]

    lines.append("逐位候选（每一位只能从这里挑）：")
    for pos in data["positions"]:
        role = f"（{pos['role']}）" if pos["role"] else ""
        if pos["glyphs"]:
            shown = " / ".join(pos["glyphs"])
        else:
            shown = "（补位键，该键不代表任何字根，本项不要给字根）"
        lines.append(f"  第 {pos['index'] + 1} 位  键 {pos['key']}  {role}候选：{shown}")

    if data["notes"]:
        lines.append("")
        lines.append("已知标注：")
        for note in data["notes"]:
            if isinstance(note, dict) and note.get("message"):
                lines.append(f"  - {note['message']}")

    if data["error"]:
        lines.append("")
        lines.append(
            f"用户这次的错误：期望按 {data['error']['expected']} 键，实际按了 {data['error']['actual']} 键。"
            "请在讲解里指名点出这两个键上的字根为何容易混。"
        )

    if rejection:
        lines.append("")
        lines.append("上一次的回答没有通过校验，问题如下，请重新给出完整且合规的 JSON：")
        lines.append(f"  {rejection}")

    return "\n".join(lines)


# ─── 自校验（与调用方同一套判定口径） ──────────────────────


def _self_check(payload: dict, data: dict) -> tuple[bool, str]:
    """校验模型的结构化断言是否落在候选集内。

    与前端 validateExplainResult 保持同一口径：位数一致、键位一致、字根在候选内。
    """
    positions = payload.get("positions")
    if not isinstance(positions, list) or not positions:
        return False, "缺少 positions 结构化断言"

    if len(positions) != len(data["positions"]):
        return False, f"positions 位数不符：编码 {len(data['positions'])} 位，给出 {len(positions)} 位"

    for i, item in enumerate(positions):
        expected = data["positions"][i]
        if not isinstance(item, dict):
            return False, f"positions[{i}] 不是对象"

        if item.get("key") != expected["key"]:
            return False, f"第 {i + 1} 位键位不符：应为 {expected['key']}，给出 {item.get('key')}"

        allowed = expected["glyphs"]
        if not allowed:
            return False, f"第 {i + 1} 位是补位键，不应给出字根，却给了「{item.get('radical')}」"

        if item.get("radical") not in allowed:
            return False, (
                f"第 {i + 1} 位字根「{item.get('radical')}」不在 {expected['key']} 键的候选里"
                f"（{'/'.join(allowed)}）"
            )

    return True, ""


def _normalize_positions(payload: dict) -> list[dict]:
    out = []
    for item in payload.get("positions") or []:
        if isinstance(item, dict):
            out.append({"key": item.get("key"), "radical": item.get("radical")})
    return out


def _is_timeout(exc: Exception) -> bool:
    """判断是否是「超时」类错误。

    刻意按**类名**判断，而不是 import openai / langchain 的异常类：
    那些依赖是延迟导入的，在错误处理路径上再触发一次导入失败，就成了「报错时报错」。
    langchain 的 OpenAITimeoutError 与 openai 的 APITimeoutError 类名都含 Timeout。
    """
    return "timeout" in type(exc).__name__.lower()


def _model_info() -> dict:
    """出错时回报「用的是哪个模型、哪个端点」。

    排障时第一个要知道的就是这个：拿到 401 却不知道是哪家 key 的问题，只能挨个猜。
    base_url 不敏感（就是服务地址）；**api_key 永远不回报，连前缀都不给**。
    """
    return {"model": MODEL, "base_url": BASE_URL or None}


def _call_llm(llm: Any, user_prompt: str) -> str:
    """单轮调用模型。

    注：不写 `llm: ChatOpenAI` —— 注解在 def 期求值，而 ChatOpenAI 是延迟导入的，
    写类型名会在**导入期**直接 NameError（比依赖缺失更难查：连错误返回都到不了）。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])
    return resp.content or ""


# ─── 入口 ──────────────────────────────────────────────────


def invoke(input: dict, config: dict = None) -> dict:
    """平台入口：`agent:invoke`。

    !! 绝不向外抛异常 !!
    抛出会被平台变成 500/502，前端只看到「服务异常」，拿不到任何可解释的信息。
    所有失败路径都归一成 output 里的一个 dict（含 error 字段）。
    """
    config = config or {}
    started = time.time()

    try:
        data = _parse_input(input or {})
    except BadInput as exc:
        logger.warning("bad input: %s", exc)
        return {"error": f"输入不合法：{exc}", "explanation": None, "positions": None}

    if not API_KEY:
        logger.error("LLM_API_KEY / DASHSCOPE_API_KEY 未注入")
        return {"error": "模型未配置（缺少 LLM_API_KEY）", "explanation": None, "positions": None}

    try:
        llm = _build_llm()
    except Exception as exc:  # noqa: BLE001 - 构造失败也要如实回报
        logger.exception("build llm failed")
        return {"error": f"模型初始化失败：{exc}", "explanation": None, "positions": None}

    rejection: str | None = None
    violations: list[str] = []
    attempts = 0

    # 最多两次：第一次正常提问；若自校验不过，把违规点喂回去让它改
    for attempt in range(2):
        attempts = attempt + 1

        # 预算保护：超时就带着已有信息返回，而不是让平台 180s 超时后给用户一个空错误
        if time.time() - started > TOTAL_BUDGET_SEC:
            logger.warning("budget exhausted after %s attempts", attempts - 1)
            break

        try:
            raw = _call_llm(llm, _build_user_prompt(data, rejection))
        except Exception as exc:  # noqa: BLE001
            logger.exception("llm call failed (attempt %s)", attempts)

            # !! 超时**要重试**（回退掉上一版「超时立即放弃」的做法）!!
            #
            # 依据是实测：这个平台最常见的超时来源是**冷启动** —— agent 刚激活 /
            # 闲置后再调用，模型侧要预热，第一次 70s 超时，紧接着第二次十几秒就成功。
            # 不重试等于把这种一次性失败直接变成用户可见的错误。
            # 总时长由循环顶部的 TOTAL_BUDGET_SEC 兜住，不会无限等。
            if _is_timeout(exc) and attempts < 2:
                # 记进 violations：这样即使两次都超时，最终返回里的原因也是诚实的
                # （而不是笼统的「自校验未通过」）
                violations.append(f"第 {attempts} 次调用超时（单次上限 {LLM_TIMEOUT}s），已重试")
                logger.info("超时（第 %s 次），按预算允许重试", attempts)
                continue

            if attempt == 0:
                return {
                    **_model_info(),
                    "error": f"模型调用失败：{exc}",
                    "explanation": None,
                    "positions": None,
                }
            break

        payload = _extract_json(raw)
        if payload is None:
            rejection = "输出不是合法 JSON"
            violations.append(rejection)
            continue

        ok, reason = _self_check(payload, data)
        if ok:
            return {
                "explanation": payload.get("explanation"),
                "positions": _normalize_positions(payload),
                "suggestions": payload.get("suggestions") or [],
                "model": MODEL,
                "char": data["char"],
                "code": data["code"],
                "self_check": {"ok": True, "attempts": attempts, "violations": []},
            }

        rejection = reason
        violations.append(reason)
        logger.info("self-check failed (attempt %s): %s", attempts, reason)

    # 两次都没过：把解释照常返回（前端会因校验不过而不展示），
    # 但同时明说原因，便于从 ai_invocations 里复盘是哪一类失败。
    return {
        "explanation": None,
        "positions": None,
        "suggestions": [],
        "model": MODEL,
        "error": "自校验未通过",
        "self_check": {"ok": False, "attempts": attempts, "violations": violations},
    }

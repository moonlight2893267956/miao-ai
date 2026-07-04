"""
JSON 修复 Agent (json-repairer)

接收损坏 JSON 文本，调用 LLM 修复语法错误，返回修复后的合法 JSON。

修复策略（按优先级）：
1. json.loads 直接成功 → 立即返回
2. 正则预处理（尾随逗号、无引号 key） → 重试 json.loads
3. 锁定损坏区域 → 只把有问题的那一小段送给 LLM 修复
4. 全量 LLM 修复（兜底）
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger("json-repairer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

MODEL = os.environ.get("LLM_MODEL", os.environ.get("DASHSCOPE_MODEL", "qwen-plus"))
API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("DASHSCOPE_API_KEY", ""))
BASE_URL = os.environ.get("LLM_BASE_URL", os.environ.get("DASHSCOPE_BASE_URL", ""))
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", os.environ.get("DASHSCOPE_TEMPERATURE", "0.0")))
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", os.environ.get("DASHSCOPE_MAX_TOKENS", "4096")))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))
# 一次调用中最多允许 2 次 LLM 重试（含首次），避免级联延迟
MAX_LLM_CALLS = 2
# 单次 agent 调用的总超时，必须在平台 invoke_sync_timeout 之内（默认 300s）
TOTAL_TIMEOUT = int(os.environ.get("AGENT_TOTAL_TIMEOUT", "240"))

logger.info(f"Config: model={MODEL}, base_url={BASE_URL[:30] if BASE_URL else '(empty)'}, "
            f"temp={TEMPERATURE}, max_tokens={MAX_TOKENS}, timeout={LLM_TIMEOUT}s")

FRAGMENT_PROMPT = """You are a JSON repair expert. You are given a CORRUPTED FRAGMENT of a larger JSON document. Your job: fix syntax errors in this fragment ONLY.

Rules:
- Fix syntax errors: missing brackets, braces, quotes, commas
- Remove trailing commas
- Fix unquoted keys
- CRITICAL: Keep the fragment structure exactly as-is. Do NOT add outer {} or [].
- CRITICAL: The fragment will be spliced back into the larger JSON, so do NOT change the fragment's boundaries.
- Return ONLY the fixed fragment. No markdown fences, no explanation."""


SYSTEM_PROMPT = """You are a JSON repair expert. Your task is to fix damaged JSON text.

Rules:
- Fix all syntax errors: missing brackets, braces, quotes, commas, colons
- Handle truncation (incomplete JSON at the end)
- Handle mixed content (non-JSON text mixed with JSON)
- Remove trailing commas
- Convert single quotes to double quotes
- Fix unquoted keys
- CRITICAL: Preserve the original top-level structure (object/array). Do NOT remove outer {} or [].
- If input looks like an object, output MUST be a valid object { ... }
- If input looks like an array, output MUST be a valid array [ ... ]
- Return ONLY the corrected valid JSON string
- NO markdown fences (```json or ```), NO explanation, NO extra text
- The response must be parseable by json.loads() / JSON.parse()
"""

MAX_RETRIES = 2


# -------- 正则预处理：修复常见琐碎错误（无需 LLM）--------

def _quick_preprocess(text: str) -> str:
    """用正则修复最常见的琐碎错误，不改动结构。"""
    # 1) 尾随逗号：{"a":1,} → {"a":1}   [1,2,] → [1,2]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # 2) 无引号 key（仅限简单字母/下划线开头的 key）
    text = re.sub(r'([{,])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', text)
    return text


# -------- 结构锁定：找出损坏区域，只送给 LLM 必要部分 --------

def _locate_corruption(text: str) -> tuple[str, str, str] | None:
    """利用 json.JSONDecodeError 精准定位损坏位置，取周围 ~600 字符窗口。

    返回 (prefix, corrupted_window, suffix)，或 None。
    """
    # 先尝试预处理后的版本，获取错误位置
    test_text = _quick_preprocess(text)
    try:
        json.loads(test_text)
        # 预处理后已经合法了，返回一个微小窗口让 LLM pass-through
        return None
    except json.JSONDecodeError as e:
        error_pos = e.pos

    # 以 error_pos 为中心取 ~600 字符窗口，但对齐到对象边界（不切断 key）
    MARGIN = 300
    raw_start = max(0, error_pos - MARGIN)
    raw_end = min(len(text), error_pos + MARGIN)

    # 对齐: 往前找到最近的 `{"` 或面有 `,{`
    window_start = raw_start
    for i in range(raw_start, max(0, raw_start - 100), -1):
        if text[i:i+2] in ('{"', ',{'):
            window_start = i
            break

    window_end = raw_end
    prefix = text[:window_start]
    corrupted = text[window_start:window_end]
    suffix = text[window_end:]

    # 窗口太小 → 说明已经是小文本，不用锁定
    if len(corrupted) < 30:
        return None
    # 窗口太大（比如错误在开头/结尾） → 回退使用原有逻辑
    if len(corrupted) > 2000:
        return _locate_unclosed(text)

    return (prefix, corrupted, suffix)


def _locate_unclosed(text: str) -> tuple[str, str, str] | None:
    """找到未闭合的 bracket 位置，用于 _locate_corruption 的回退。"""
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}
    last_safe = 0

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]":
            if not stack or ch != stack[-1]:
                window_start = max(0, last_safe - 200)
                window_end = min(len(text), i + 400)
                prefix = text[:window_start]
                corrupted = text[window_start:window_end]
                suffix = text[window_end:]
                if 50 <= len(corrupted) <= 3000:
                    return (prefix, corrupted, suffix)
                return None
            stack.pop()
            if not stack:
                last_safe = i + 1

    # 未闭合：取从最后一个安全位置到末尾，但不超过 800 字符
    if stack:
        win_len = min(len(text) - last_safe, 800)
        window_start = max(0, last_safe - 100)
        prefix = text[:window_start]
        corrupted = text[window_start:window_start + win_len + 100]
        suffix = text[window_start + win_len + 100:] if window_start + win_len + 100 < len(text) else ""
        if 50 <= len(corrupted) <= 2000:
            return (prefix, corrupted, suffix)

    return None


def _splice_and_validate(prefix: str, repair_result: str, suffix: str) -> str | None:
    """将 prefix + 修复后的 repair_result + suffix 拼接并验证。"""
    candidate = prefix + repair_result + suffix
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        return None


def _build_llm() -> ChatOpenAI:
    """构建 LLM 实例，正确处理空 base_url。

    max_retries=0 禁用 OpenAI SDK 内部重试，避免单次 LLM 调用被 SDK
    自动 retry 拖长到 2-3 倍超时时间。agent 自己有重试逻辑。
    """
    kwargs = dict(
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


def _strip_markdown_fence(text: str) -> str:
    """安全剥离 markdown code fence。"""
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    result_lines = lines[1:]
    while result_lines and result_lines[-1].strip() in ("```", "``"):
        result_lines.pop()
    return "\n".join(result_lines).strip()


def _extract_balanced_json(text: str) -> str | None:
    """从文本中提取第一个平衡的 JSON 对象/数组。"""
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start < 0:
        return None

    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]":
            if not stack or ch != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return text[start : i + 1].strip()

    return None


def _normalize_llm_output(text: str) -> str:
    """标准化 LLM 输出：剥离 fence，去空白。不再提取平衡 JSON（太容易截断有效内容）。"""
    return _strip_markdown_fence((text or "").strip())


def invoke(input: dict, config: dict = None) -> dict:
    """
    miao-ai Agent 标准入口。

    策略：先用正则/结构分析定位损坏区域，只把问题部分送给 LLM。
    全量 LLM 修复作为最后兜底。
    """
    json_text = input.get("json_text", "")
    if not json_text:
        return {"repaired": ""}

    logger.info(f"Invoke start: input_len={len(json_text)}")
    t0 = time.time()

    def _elapsed() -> float:
        return time.time() - t0

    def _check_timeout(stage: str) -> None:
        if _elapsed() > TOTAL_TIMEOUT:
            raise TimeoutError(f"Agent total timeout ({TOTAL_TIMEOUT}s) exceeded at stage: {stage}")

    # ---- 1) 已经是合法 JSON，直接返回 ----
    try:
        json.loads(json_text)
        logger.info(f"Already valid JSON, returning as-is ({_elapsed():.1f}s)")
        return {"repaired": json_text}
    except json.JSONDecodeError:
        pass

    # ---- 2) 正则预处理 ----
    preprocessed = _quick_preprocess(json_text)
    if preprocessed != json_text:
        try:
            json.loads(preprocessed)
            logger.info(f"Fixed by regex preprocess ({_elapsed():.1f}s)")
            return {"repaired": preprocessed}
        except json.JSONDecodeError:
            json_text = preprocessed  # 用预处理后的版本继续

    _check_timeout("structural")
    llm = _build_llm()
    llm_calls = 0

    # ---- 3) 结构锁定：只把损坏区域送给 LLM（最多 1 次，失败了直接走全量 LLM）----
    corruption = _locate_corruption(json_text)
    if corruption:
        prefix, window, suffix = corruption
        logger.info(f"Corruption locked: prefix={len(prefix)}B window={len(window)}B suffix={len(suffix)}B")

        _check_timeout("structural_llm")
        t_s = time.time()
        patch = _call_llm(llm, window, fragment=True)
        logger.info(f"Structural LLM (fragment): {time.time()-t_s:.1f}s, result_len={len(patch) if patch else 0}")

        if patch:
            result = _splice_and_validate(prefix, patch, suffix)
            if result:
                logger.info(f"Structural repair success ({_elapsed():.1f}s)")
                return {"repaired": result}

    # ---- 4) 兜底：全量 LLM 修复 ----
    for attempt in range(MAX_LLM_CALLS):
        _check_timeout(f"full_llm_{attempt+1}")
        t1 = time.time()
        if attempt == 0:
            repaired = _call_llm(llm, json_text)
        else:
            repaired = _retry_with_error(llm, json_text, repaired, str(e) if repaired else "empty response")
        llm_calls += 1
        logger.info(f"Full LLM call {attempt+1}: {time.time()-t1:.1f}s, result_len={len(repaired) if repaired else 0}")

        if repaired:
            try:
                json.loads(repaired)
                logger.info(f"Invoke done: {_elapsed():.1f}s ({llm_calls} LLM calls, valid)")
                return {"repaired": repaired}
            except json.JSONDecodeError as e:
                if attempt + 1 >= MAX_LLM_CALLS:
                    logger.info(f"Invoke done: {_elapsed():.1f}s ({llm_calls} LLM calls, still invalid)")
                    return {"repaired": repaired}
                continue  # 还有重试机会

    logger.error(f"Invoke failed: {_elapsed():.1f}s, empty after {llm_calls} LLM calls")
    raise ValueError("LLM returned empty response — the model may not support this input")


def _call_llm(llm: ChatOpenAI, json_text: str, *, fragment: bool = False) -> str:
    """调用 LLM 并标准化输出。

    当 fragment=True 时使用 FRAGMENT_PROMPT（针对损坏片段的修复），
    且只剥离 markdown fence（不提取平衡 JSON，因为片段本身可能以非 { [ 开头）。
    """
    prompt = FRAGMENT_PROMPT if fragment else SYSTEM_PROMPT
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=json_text),
    ]
    response = llm.invoke(messages)
    raw = (response.content or "").strip()
    if fragment:
        return _strip_markdown_fence(raw)
    return _normalize_llm_output(raw)


def _call_llm_with_explicit_prompt(llm: ChatOpenAI, json_text: str) -> str:
    """LLM 返回空时，用更明确的 prompt 重试。"""
    explicit_prompt = f"""The following text is a damaged JSON string that needs repair. Please fix all syntax errors and return ONLY the corrected JSON. No explanation, no markdown fences.

Damaged JSON:
{json_text}

Corrected JSON:"""
    messages = [
        SystemMessage(content="You are a JSON repair expert. Output ONLY valid corrected JSON."),
        HumanMessage(content=explicit_prompt),
    ]
    response = llm.invoke(messages)
    return _normalize_llm_output(response.content)


def _retry_with_error(llm: ChatOpenAI, original: str, bad_output: str, error_msg: str) -> str:
    """带上错误信息重试修复。"""
    retry_prompt = f"""The previous repair attempt produced INVALID JSON.

Original damaged JSON:
{original}

Your (invalid) output:
{bad_output}

Error: {error_msg}

Please output ONLY the valid corrected JSON. No markdown fences, no explanation."""
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=retry_prompt),
    ]
    response = llm.invoke(messages)
    return _normalize_llm_output(response.content)

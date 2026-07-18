"""
Cron 表达式编辑器 AI 智能辅助 Agent (cron-assistant)

为阿渺工具箱 Cron 表达式编辑器提供五种 AI 增强能力：
- generate：自然语言 → Cron 表达式
- explain ：Cron 表达式 → 自然语言详解（频率/陷阱/匹配度）
- optimize ：表达式优化/简化建议
- diagnose：排错诊断（支持多轮对话）
- convert ：linux5 ↔ spring6 方言转换

工程模式复用 regex-assistant / json-repairer / translate-agent：
- _build_llm()：max_retries=0 + timeout，禁用 SDK 内部重试
- TOTAL_TIMEOUT + _check_timeout(stage)：agent 总超时守护
- MAX_LLM_CALLS：LLM 调用失败重试
- _strip_markdown_fence()：剥离 markdown fence
- 流式：config["stream"] 为 True 时逐 token yield {"token": ...}
- 单文件打包：业务代码全内联，第三方依赖经 requirements.txt，
  兼容平台 Docker 构建（仅 COPY agent.py）
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# 兼容各类运行环境（无害）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

logger = logging.getLogger("cron-assistant")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

MODEL = os.environ.get("LLM_MODEL", os.environ.get("DASHSCOPE_MODEL", "qwen-plus"))
API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("DASHSCOPE_API_KEY", ""))
BASE_URL = os.environ.get("LLM_BASE_URL", os.environ.get("DASHSCOPE_BASE_URL", ""))
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", os.environ.get("DASHSCOPE_TEMPERATURE", "0.3")))
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", os.environ.get("DASHSCOPE_MAX_TOKENS", "4096")))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))

MAX_LLM_CALLS = 2
# 单次 agent 调用总超时，必须 < 平台 invoke_sync_timeout（默认 180s），留余量取 170
TOTAL_TIMEOUT = int(os.environ.get("AGENT_TOTAL_TIMEOUT", "170"))

VALID_TASKS = {"generate", "explain", "optimize", "diagnose", "convert"}
VALID_DIALECTS = {"linux5", "spring6"}

logger.info(
    "Config: model=%s, base_url=%s, temp=%s, max_tokens=%s, timeout=%ss",
    MODEL,
    BASE_URL[:30] if BASE_URL else "(empty)",
    TEMPERATURE,
    MAX_TOKENS,
    LLM_TIMEOUT,
)


# -------- LLM 构建与输出标准化 --------

def _build_llm() -> ChatOpenAI:
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
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    result_lines = lines[1:]
    while result_lines and result_lines[-1].strip() in ("```", "``"):
        result_lines.pop()
    return "\n".join(result_lines).strip()


def _normalize_llm_output(text: str) -> str:
    return _strip_markdown_fence((text or "").strip())


def _call_llm(llm: ChatOpenAI, messages: list) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(MAX_LLM_CALLS):
        try:
            response = llm.invoke(messages)
            out = _normalize_llm_output(response.content)
            if out:
                return out
            last_err = ValueError("LLM 返回空响应")
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM call attempt %d failed: %s", attempt + 1, e)
    raise ValueError(f"LLM 调用失败（{MAX_LLM_CALLS} 次尝试）：{last_err}")


def _stream_llm(llm: ChatOpenAI, messages: list):
    for chunk in llm.stream(messages):
        if chunk.content:
            yield {"token": chunk.content}


# -------- 输入解析 --------

def _count_fields(expr: str) -> int:
    return len([x for x in expr.strip().split() if x])


def _parse_input(input: dict) -> dict:
    task = (input.get("task") or "generate").strip().lower()
    if task not in VALID_TASKS:
        task = "generate"

    dialect = (input.get("dialect") or "").strip().lower()
    if dialect and dialect not in VALID_DIALECTS:
        dialect = ""

    target_dialect = (input.get("targetDialect") or "").strip().lower()
    if target_dialect and target_dialect not in VALID_DIALECTS:
        target_dialect = ""

    # generate 默认 linux5（标准 5 位最通用）
    if task == "generate" and not dialect:
        dialect = "linux5"

    # convert 源方言按字段数推断
    if task == "convert" and not dialect:
        n = _count_fields(input.get("expression", "") or "")
        dialect = "linux5" if n == 5 else "spring6"

    conversation = input.get("conversation") or []
    if not isinstance(conversation, list):
        conversation = []

    semantic_warnings = input.get("semanticWarnings") or []
    if not isinstance(semantic_warnings, list):
        semantic_warnings = [str(semantic_warnings)]

    return {
        "task": task,
        "description": input.get("description", "") or "",
        "expression": input.get("expression", "") or "",
        "dialect": dialect,
        "targetDialect": target_dialect,
        "phenomenon": input.get("phenomenon", "") or "",
        "conversation": conversation,
        "explanationContext": input.get("explanationContext", "") or "",
        "semanticWarnings": semantic_warnings,
        "timezone": input.get("timezone", "") or "",
    }


# -------- System Prompt --------

SYSTEM_PROMPT = """你是一个资深 Cron 表达式专家，服务于「阿渺工具箱 · Cron 表达式编辑器」。

## 任务范围
你支持五种任务，由用户消息的上下文决定：
1. generate：根据自然语言描述生成 Cron 表达式
2. explain ：解释给定 Cron 表达式的含义
3. optimize：对给定 Cron 表达式提出优化/简化建议
4. diagnose：根据用户描述的现象，诊断定时任务为何未按预期触发
5. convert ：在 linux5 与 spring6 方言间相互转换

## 方言规则
- linux5 ：5 个字段 —— 分 时 日 月 星期
- spring6：6 个字段 —— 秒 分 时 日 月 星期
- generate 时若用户未明确指定方言偏好，默认生成 linux5
- convert 时若用户未给出源方言，按字段段数推断：5 段→linux5，6 段→spring6

## 输出规则（严格）
你必须只输出一个合法 JSON 对象，禁止输出 JSON 以外的任何闲聊文本。各任务的 JSON 字段约定如下：

- generate → {"expression": "生成的表达式(空格分隔字段,不含分隔符与字段名)", "dialect": "linux5 或 spring6", "explanation": "简要说明为何如此生成"}
- explain  → {"explanation": "三段式：①触发频率 ②潜在陷阱/边界情况 ③与常见需求的匹配度"}
- optimize → {"suggestions": ["{优化后表达式} — {理由}", ...]}  （至少 1 条；若无冗余，写明「已是最优」）
- diagnose → {"diagnosis": "结构：可能原因(列表) + 针对性修复建议", "expression": "修正后的标准 Cron 表达式(字段数须与源方言一致：linux5 为5段、spring6 为6段)；若原表达式本身无需修正则输出空字符串 \"\""}
- convert  → {"originalExpression": "原表达式", "convertedExpression": "转换后表达式", "dialect": "源方言", "targetDialect": "目标方言", "notes": "说明 ?/L/W 处理与字段顺序变化"}

## 约束
- 仅处理与 Cron / 定时任务相关的请求；若用户描述与定时无关，请在对应字段中礼貌拒绝并说明，不要编造表达式。
- 所有解释、建议、诊断一律使用简体中文，专业、简洁、可执行。
- 生成/转换的表达式必须语法正确、字段数符合目标方言、数值在合法范围内。
- 只输出 JSON。"""


# -------- User Message 构造 --------

def _context_block(p: dict) -> str:
    parts: List[str] = []
    if p["explanationContext"]:
        parts.append(f"本地解析结果（参考）：{p['explanationContext']}")
    if p["semanticWarnings"]:
        warns = "; ".join(str(w) for w in p["semanticWarnings"])
        parts.append(f"本地语义警告：{warns}")
    if p["timezone"]:
        parts.append(f"时区：{p['timezone']}")
    return ("\n\n上下文：\n" + "\n".join(parts)) if parts else ""


def _build_user_message(p: dict) -> str:
    ctx = _context_block(p)

    if p["task"] == "generate":
        dh = f"（目标方言：{p['dialect']}）" if p["dialect"] else ""
        return f"请根据以下自然语言描述生成 Cron 表达式{dh}：\n{p['description']}{ctx}"

    if p["task"] == "explain":
        dh = f"（方言：{p['dialect']}）" if p["dialect"] else ""
        return f"请解释以下 Cron 表达式{dh}：\n{p['expression']}{ctx}"

    if p["task"] == "optimize":
        dh = f"（方言：{p['dialect']}）" if p["dialect"] else ""
        return f"请优化以下 Cron 表达式{dh}，如无冗余请原样说明：\n{p['expression']}{ctx}"

    if p["task"] == "diagnose":
        dh = f"（方言：{p['dialect']}）" if p["dialect"] else ""
        return (
            f"当前 Cron 表达式{dh}：\n{p['expression']}\n\n"
            f"现象描述：\n{p['phenomenon']}{ctx}"
        )

    if p["task"] == "convert":
        dh = f"（源方言：{p['dialect']}）" if p["dialect"] else ""
        th = f"（目标方言：{p['targetDialect']}）" if p["targetDialect"] else ""
        return f"请将以下 Cron 表达式{dh}转换为{th}：\n{p['expression']}{ctx}"

    return ""


def _build_messages(p: dict) -> list:
    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    # diagnose 多轮：把历史对话插入到当前 user 消息之前
    if p["task"] == "diagnose":
        for m in p["conversation"]:
            if not isinstance(m, dict):
                continue
            role = (m.get("role") or "").lower()
            content = m.get("content", "") or ""
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=_build_user_message(p)))
    return messages


# -------- JSON 输出解析 --------

def _parse_json_output(raw: str) -> dict:
    """从 LLM 输出中提取 JSON，兼容 markdown fence 包裹。"""
    text = _normalize_llm_output(raw)

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return {
        "explanation": text,
        "_parse_warning": "LLM 输出非标准 JSON，已做兜底解析",
    }


# 兜底提取：诊断文本中反引号包裹的 Cron 表达式（5 或 6 段）
_CRON_TOKEN_RE = r"[0-9*/,\-?LW#]+"
_CRON_EXPR_RE = re.compile(r"`(" + _CRON_TOKEN_RE + r"(?:\s+" + _CRON_TOKEN_RE + r"){4,5})`")


def _extract_cron_from_text(text: str) -> str:
    """当 LLM 未直接返回 expression 字段时，从诊断文本中提取反引号包裹的修正表达式。"""
    if not text:
        return ""
    m = _CRON_EXPR_RE.search(text)
    return m.group(1) if m else ""


# 整行/整段即为 Cron 的纯候选（无反引号、无描述），用于 optimize 兜底
_CRON_LINE_RE = re.compile(r"^\s*(" + _CRON_TOKEN_RE + r"(?:\s+" + _CRON_TOKEN_RE + r"){4,5})\s*$")


def _extract_cron_plain(text: str) -> str:
    """提取整条文本本身就是 Cron 表达式的候选（如 LLM 只返回 `*/5 * * * *` 无理由）。"""
    if not text:
        return ""
    m = _CRON_LINE_RE.search(text)
    return m.group(1) if m else ""


def _normalize_suggestion(s) -> str:
    """确保每条优化建议符合 '{表达式} — {理由}' 格式；缺分隔符时尽量提取表达式并补默认理由。"""
    s = (s or "").strip()
    if not s or " — " in s:
        return s
    # 反引号包裹的表达式（如 "建议改为 `*/5 * * * *`"）
    expr = _extract_cron_from_text(s)
    if expr:
        reason = s.replace("`" + expr + "`", "").strip(" —-")
        return f"{expr} — {reason or '优化建议'}"
    # 整条本身就是纯表达式（无描述）
    expr2 = _extract_cron_plain(s)
    if expr2:
        return f"{expr2} — 优化建议"
    # 无法解析则保持原样（前端该条退化不出按钮，但不破坏整体）
    return s


def _validate_output(result: dict, task: str, p: dict) -> dict:
    """确保输出包含任务所需的必需字段，缺失则补默认值。"""
    result.setdefault("explanation", "")

    if task == "generate":
        result.setdefault("expression", "")
        result.setdefault("dialect", p["dialect"] or "linux5")
    elif task == "optimize":
        sugg = result.get("suggestions")
        if not isinstance(sugg, list):
            sugg = [str(sugg)] if sugg else []
        result["suggestions"] = [_normalize_suggestion(x) for x in sugg]
    elif task == "diagnose":
        result.setdefault("diagnosis", result.get("explanation", ""))
        expr = result.get("expression", "")
        if not expr or not str(expr).strip():
            extracted = _extract_cron_from_text(str(result.get("diagnosis", "")))
            if extracted:
                expr = extracted
        result["expression"] = expr or ""
    elif task == "convert":
        result.setdefault("originalExpression", p["expression"])
        result.setdefault("convertedExpression", "")
        result.setdefault("dialect", p["dialect"])
        result.setdefault("targetDialect", p["targetDialect"])
        result.setdefault("notes", "")

    return result


# -------- 顶层 answer（供前端 Try Run 普通模式展示） --------

def _top_answer(result: dict, task: str) -> str:
    if task == "generate":
        return result.get("expression", "")
    if task == "explain":
        return result.get("explanation", "")
    if task == "optimize":
        sug = result.get("suggestions", [])
        return "\n".join(sug) if isinstance(sug, list) else str(sug)
    if task == "diagnose":
        return result.get("diagnosis", "")
    if task == "convert":
        return result.get("convertedExpression", "")
    return ""


# -------- 五任务实现 --------

def _do_generate(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_generate")
    raw = _call_llm(llm, _build_messages(p))
    return _validate_output(_parse_json_output(raw), "generate", p)


def _do_explain(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_explain")
    raw = _call_llm(llm, _build_messages(p))
    return _validate_output(_parse_json_output(raw), "explain", p)


def _do_optimize(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_optimize")
    raw = _call_llm(llm, _build_messages(p))
    return _validate_output(_parse_json_output(raw), "optimize", p)


def _do_diagnose(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_diagnose")
    raw = _call_llm(llm, _build_messages(p))
    return _validate_output(_parse_json_output(raw), "diagnose", p)


def _do_convert(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_convert")
    raw = _call_llm(llm, _build_messages(p))
    result = _parse_json_output(raw)
    # 回填源/目标方言，便于前端配对
    result["originalExpression"] = p["expression"]
    result["dialect"] = p["dialect"]
    result["targetDialect"] = p["targetDialect"]
    return _validate_output(result, "convert", p)


# -------- 流式路径 --------

def _stream_invoke(p: dict, llm: ChatOpenAI):
    yield from _stream_llm(llm, _build_messages(p))


# -------- 错误响应 --------

def _err(task: str, msg: str) -> dict:
    return {
        "task": task,
        "explanation": "",
        "answer": "",
        "notes": msg,
    }


# -------- 标准入口 --------

def invoke(input: dict, config: dict = None):
    """miao-ai Agent 标准入口。

    非流式：返回 dict；流式（config["stream"]=True）：返回 generator 逐 token yield。
    """
    config = config or {}
    p = _parse_input(input)

    # 输入校验（聚焦任务所需字段齐全）
    if p["task"] == "generate" and not p["description"]:
        return _err("generate", "generate 任务缺少 description 参数")
    if p["task"] in ("explain", "optimize") and not p["expression"]:
        return _err(p["task"], f"{p['task']} 任务缺少 expression 参数")
    if p["task"] == "diagnose" and (not p["expression"] or not p["phenomenon"]):
        return _err("diagnose", "diagnose 任务缺少 expression 或 phenomenon 参数")
    if p["task"] == "convert" and (not p["expression"] or not p["targetDialect"]):
        return _err("convert", "convert 任务缺少 expression 或 targetDialect 参数")

    logger.info(
        "Invoke: task=%s dialect=%s stream=%s",
        p["task"], p["dialect"] or "(any)", bool(config.get("stream")),
    )

    llm = _build_llm()

    # 流式模式
    if config.get("stream"):
        return _stream_invoke(p, llm)

    # 非流式模式：超时守护
    t0 = time.time()

    def _check_timeout(stage: str) -> None:
        if time.time() - t0 > TOTAL_TIMEOUT:
            raise TimeoutError(f"Agent 总超时（{TOTAL_TIMEOUT}s），阶段：{stage}")

    if p["task"] == "generate":
        result = _do_generate(llm, p, _check_timeout)
    elif p["task"] == "explain":
        result = _do_explain(llm, p, _check_timeout)
    elif p["task"] == "optimize":
        result = _do_optimize(llm, p, _check_timeout)
    elif p["task"] == "diagnose":
        result = _do_diagnose(llm, p, _check_timeout)
    else:
        result = _do_convert(llm, p, _check_timeout)

    result["task"] = p["task"]
    result["answer"] = _top_answer(result, p["task"])
    logger.info("Invoke done: task=%s %.2fs", p["task"], time.time() - t0)
    return result

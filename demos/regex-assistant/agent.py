"""
正则表达式助手 Agent (regex-assistant)

为阿渺工具箱的正则测试器提供五种 AI 增强能力：
- generate：根据自然语言描述生成正则表达式
- explain：逐段解释给定正则表达式的含义
- optimize：对给定正则表达式提出优化建议（suggestions 内嵌表达式）
- diagnose：结合样例文本诊断表达式为何不匹配预期，并给出修正
- convert：在不同引擎方言之间转换（js / pcre / python / java / go / rust）

工程模式复用 translate-agent / json-repairer：
- _build_llm()：max_retries=0 + timeout，禁用 SDK 内部重试
- TOTAL_TIMEOUT + _check_timeout(stage)：agent 总超时守护
- MAX_LLM_CALLS：LLM 调用失败重试
- _strip_markdown_fence()：剥离 markdown fence
- 流式：config["stream"] 为 True 时逐 token yield {"token": ...}
- 单文件打包：所有依赖内联，兼容平台 Docker 构建（仅 COPY agent.py）
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# 兼容各类运行环境（无害）
import sys as _sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

logger = logging.getLogger("regex-assistant")
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
# 与前端 AIPanel 对齐：js / pcre / python / java / go / rust（去掉 php，增补 pcre/rust）
VALID_ENGINES = {"js", "pcre", "python", "java", "go", "rust"}

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


def _call_llm(llm: ChatOpenAI, system_prompt: str, user_content: str, history_msgs: Optional[list] = None) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(MAX_LLM_CALLS):
        try:
            messages = [SystemMessage(content=system_prompt)]
            if history_msgs:
                messages.extend(history_msgs)
            messages.append(HumanMessage(content=user_content))
            response = llm.invoke(messages)
            out = _normalize_llm_output(response.content)
            if out:
                return out
            last_err = ValueError("LLM 返回空响应")
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM call attempt %d failed: %s", attempt + 1, e)
    raise ValueError(f"LLM 调用失败（{MAX_LLM_CALLS} 次尝试）：{last_err}")


def _stream_llm(llm: ChatOpenAI, system_prompt: str, user_content: str, history_msgs: Optional[list] = None):
    messages = [SystemMessage(content=system_prompt)]
    if history_msgs:
        messages.extend(history_msgs)
    messages.append(HumanMessage(content=user_content))
    for chunk in llm.stream(messages):
        if chunk.content:
            yield {"token": chunk.content}


# -------- 输入解析 --------

def _parse_input(input: dict) -> dict:
    task = (input.get("task") or "generate").strip().lower()
    if task not in VALID_TASKS:
        task = "generate"
    engine = (input.get("engine") or "").strip().lower()
    if engine and engine not in VALID_ENGINES:
        engine = ""
    target_engine = (input.get("targetEngine") or "").strip().lower()
    if target_engine and target_engine not in VALID_ENGINES:
        target_engine = ""
    samples = input.get("samples") or []
    if isinstance(samples, str):
        samples = [samples]
    conversation = input.get("conversation") or []
    if not isinstance(conversation, list):
        conversation = []
    return {
        "task": task,
        "description": input.get("description", "") or "",
        "pattern": input.get("pattern", "") or "",
        "flags": input.get("flags", "") or "",
        "engine": engine,
        "targetEngine": target_engine,
        "samples": samples,
        "conversation": conversation,
    }


# -------- System Prompt --------

SYSTEM_PROMPT = """你是一个正则表达式专家助手，服务于阿渺工具箱的正则测试器。用户会向你提出五种类型的请求：

1. **generate**：根据自然语言描述生成正则表达式
2. **explain**：解释给定正则表达式的含义（逐段拆解元字符）
3. **optimize**：对给定正则提出简化/优化建议
4. **diagnose**：结合样例文本诊断表达式为何不匹配预期，并给出修正
5. **convert**：在不同引擎方言之间转换（js / pcre / python / java / go / rust）

## 输出规则（必须严格遵守）

你必须返回 JSON 格式（也可逐 token 流式输出同一 JSON）。字段按任务选择：

- generate：必须返回 `pattern`（生成的表达式，不含 / 与 flags）；建议返回 `engine`；可返回 `explanation`
- explain：返回 `pattern`（原样回显）+ `explanation`（中文详解）
- optimize：必须返回 `suggestions`（字符串数组，每条格式为 `"{优化后表达式} — {理由}"`）；可返回 `explanation`、`originalPattern`。**不要再单独返回** `pattern` 单字段（旧字段仅前端兼容兜底）
- diagnose：必须返回 `diagnosis`（根因说明）+ `pattern`（修正后的标准正则，可直接使用）；可返回 `originalPattern`、`explanation`
- convert：必须返回 `convertedPattern`（转换结果）+ `engine`（等于 targetEngine）；可返回 `originalPattern`、`explanation`

## 硬性要求

- 引擎感知：根据 engine / targetEngine 调整元字符与特性（如 JS 不支持后行断言、Go RE2 不支持前瞻/后顾/反向引用与反向引用、Python re 的 \\d 匹配 Unicode、Java 的 \\b 行为等）
- `pattern` / `convertedPattern` 字段不要包含 / 分隔符与 flags（flags 单独通过 flags 字段传入）
- diagnose 的 `pattern` 必须是可直接使用的修正表达式，不要只写在 diagnosis 文本里
- optimize 时不要把解释文本塞进 pattern 字段，且不要改变原表达式的匹配语义
- convert 若无法等价转换，返回最接近的可执行改写（合法正则），并在 explanation 中显式标注不兼容点与语义差异；不要返回纯文字方案或非正则文本
- 只输出 JSON，不要任何其他文字"""


# -------- User Message 构造 --------

def _build_user_message(p: dict) -> str:
    task = p["task"]

    if task == "generate":
        desc = p["description"]
        engine_hint = f"（目标引擎：{p['engine']}）" if p["engine"] else ""
        return f"请根据以下描述生成正则表达式{engine_hint}：\n{desc}"

    elif task == "explain":
        pattern = p["pattern"]
        context = []
        if p["flags"]:
            context.append(f"标志位：{p['flags']}")
        if p["engine"]:
            context.append(f"引擎：{p['engine']}")
        ctx_str = f"（{', '.join(context)}）" if context else ""
        return f"请解释以下正则表达式{ctx_str}：\n/{pattern}/"

    elif task == "optimize":
        pattern = p["pattern"]
        context = []
        if p["flags"]:
            context.append(f"标志位：{p['flags']}")
        if p["engine"]:
            context.append(f"引擎：{p['engine']}")
        ctx_str = f"（{', '.join(context)}）" if context else ""
        return f"请优化以下正则表达式{ctx_str}，如无优化空间则原样返回：\n/{pattern}/"

    elif task == "diagnose":
        pattern = p["pattern"]
        context = []
        if p["flags"]:
            context.append(f"标志位：{p['flags']}")
        if p["engine"]:
            context.append(f"引擎：{p['engine']}")
        ctx_str = f"（{', '.join(context)}）" if context else ""
        samples = p["samples"]
        samples_text = "\n".join(f"- {s}" for s in samples) if samples else "（未提供样例）"
        return (
            f"以下正则表达式{ctx_str}期望按样例匹配，但可能未达预期，请诊断原因并给出修正：\n"
            f"/{pattern}/\n\n"
            f"样例文本（每行一条，「应匹配:」表示应匹配，「不应匹配:」表示不应匹配）：\n"
            f"{samples_text}"
        )

    elif task == "convert":
        pattern = p["pattern"]
        src = p["engine"] or "自动推断"
        tgt = p["targetEngine"] or "未知"
        context = []
        if p["flags"]:
            context.append(f"当前标志位：{p['flags']}")
        ctx_str = f"（{', '.join(context)}）" if context else ""
        return (
            f"请将以下正则表达式从「{src}」引擎转换为「{tgt}」引擎{ctx_str}。\n"
            f"若无法等价转换，返回最接近的可执行改写，并在说明中明确标注不兼容点与语义差异；"
            f"不要返回纯文字方案。\n"
            f"/{pattern}/"
        )

    return ""


def _build_conversation_messages(conversation: list) -> list:
    """将多轮诊断历史转换为 langchain 消息对象（仅 diagnose 用）。"""
    msgs = []
    for turn in conversation:
        role = (turn.get("role") or "").lower()
        content = turn.get("content") or ""
        if not content:
            continue
        if role == "assistant":
            msgs.append(AIMessage(content=content))
        else:
            msgs.append(HumanMessage(content=content))
    return msgs


# -------- JSON 输出解析 --------

def _parse_json_output(raw: str) -> dict:
    """从 LLM 输出中提取 JSON，兼容 markdown fence 包裹。"""
    text = _normalize_llm_output(raw)

    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试从文本中提取 JSON 块
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # 解析失败，返回兜底结构
    return {
        "pattern": "",
        "explanation": text,
        "suggestions": [],
        "_parse_warning": "LLM 输出非标准 JSON，已做兜底解析",
    }


def _validate_output(result: dict, task: str) -> dict:
    """确保输出包含所有必需字段，缺失则补默认值。"""
    for key in ("pattern", "explanation", "originalPattern", "convertedPattern", "diagnosis", "engine"):
        result.setdefault(key, "")
    result.setdefault("suggestions", [])

    # 确保 suggestions 是列表
    if not isinstance(result["suggestions"], list):
        result["suggestions"] = [str(result["suggestions"])] if result["suggestions"] else []

    # explain 任务原样返回输入 pattern
    if task == "explain" and not result["pattern"]:
        result["pattern"] = ""

    return result


# -------- 三任务实现 --------

def _do_generate(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_generate")
    user_msg = _build_user_message(p)
    raw = _call_llm(llm, SYSTEM_PROMPT, user_msg)
    result = _parse_json_output(raw)
    return _validate_output(result, "generate")


def _do_explain(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_explain")
    user_msg = _build_user_message(p)
    raw = _call_llm(llm, SYSTEM_PROMPT, user_msg)
    result = _parse_json_output(raw)
    # explain 任务：pattern 原样返回输入
    result["pattern"] = p["pattern"]
    return _validate_output(result, "explain")


def _do_optimize(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_optimize")
    user_msg = _build_user_message(p)
    raw = _call_llm(llm, SYSTEM_PROMPT, user_msg)
    result = _parse_json_output(raw)
    return _validate_output(result, "optimize")


def _do_diagnose(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_diagnose")
    user_msg = _build_user_message(p)
    history = _build_conversation_messages(p.get("conversation"))
    raw = _call_llm(llm, SYSTEM_PROMPT, user_msg, history_msgs=history)
    result = _parse_json_output(raw)
    result = _validate_output(result, "diagnose")
    # 回显原文，便于前端「原 → 修正」对照
    if not result.get("originalPattern"):
        result["originalPattern"] = p["pattern"]
    return result


def _do_convert(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    check_timeout("llm_convert")
    user_msg = _build_user_message(p)
    raw = _call_llm(llm, SYSTEM_PROMPT, user_msg)
    result = _parse_json_output(raw)
    result = _validate_output(result, "convert")
    # 回显原文，并强制 engine = targetEngine
    if not result.get("originalPattern"):
        result["originalPattern"] = p["pattern"]
    result["engine"] = p["targetEngine"]
    return result


# -------- 流式路径 --------

def _stream_invoke(p: dict, llm: ChatOpenAI):
    user_msg = _build_user_message(p)
    history = _build_conversation_messages(p.get("conversation")) if p["task"] == "diagnose" else None
    yield from _stream_llm(llm, SYSTEM_PROMPT, user_msg, history_msgs=history)


# -------- 标准入口 --------

def invoke(input: dict, config: dict = None):
    """miao-ai Agent 标准入口。

    非流式：返回 dict；流式（config["stream"]=True）：返回 generator 逐 token yield。
    """
    config = config or {}
    p = _parse_input(input)

    # 输入校验
    if p["task"] == "generate" and not p["description"]:
        return {
            "task": "generate",
            "pattern": "",
            "explanation": "",
            "suggestions": [],
            "answer": "",
            "notes": "generate 任务缺少 description 参数",
        }
    if p["task"] in ("explain", "optimize") and not p["pattern"]:
        return {
            "task": p["task"],
            "pattern": "",
            "explanation": "",
            "suggestions": [],
            "answer": "",
            "notes": f"{p['task']} 任务缺少 pattern 参数",
        }
    if p["task"] == "diagnose" and not p["pattern"]:
        return {
            "task": "diagnose",
            "pattern": "",
            "explanation": "",
            "suggestions": [],
            "answer": "",
            "notes": "diagnose 任务缺少 pattern 参数",
        }
    if p["task"] == "diagnose" and not p["samples"]:
        return {
            "task": "diagnose",
            "pattern": "",
            "explanation": "",
            "suggestions": [],
            "answer": "",
            "notes": "diagnose 任务缺少 samples 参数",
        }
    if p["task"] == "convert" and not p["pattern"]:
        return {
            "task": "convert",
            "pattern": "",
            "explanation": "",
            "suggestions": [],
            "answer": "",
            "notes": "convert 任务缺少 pattern 参数",
        }
    if p["task"] == "convert" and not p["targetEngine"]:
        return {
            "task": "convert",
            "pattern": "",
            "explanation": "",
            "suggestions": [],
            "answer": "",
            "notes": "convert 任务缺少 targetEngine 参数",
        }

    logger.info(
        "Invoke: task=%s engine=%s stream=%s",
        p["task"], p["engine"] or "(any)", bool(config.get("stream")),
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
    # 顶层 answer：convert 取 convertedPattern，其余取 pattern（generate/diagnose）
    # explain/optimize 也保留 pattern 兜底。
    if p["task"] == "convert":
        result["answer"] = result.get("convertedPattern", "")
    else:
        result["answer"] = result.get("pattern", "")
    logger.info("Invoke done: task=%s %.2fs", p["task"], time.time() - t0)
    return result

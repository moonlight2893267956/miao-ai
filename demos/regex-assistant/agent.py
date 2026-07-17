"""
正则表达式助手 Agent (regex-assistant)

为阿渺工具箱的正则测试器提供三种 AI 增强能力：
- generate：根据自然语言描述生成正则表达式
- explain：逐段解释给定正则表达式的含义
- optimize：对给定正则表达式提出优化建议

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
from langchain_core.messages import HumanMessage, SystemMessage

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
TOTAL_TIMEOUT = int(os.environ.get("AGENT_TOTAL_TIMEOUT", "240"))

VALID_TASKS = {"generate", "explain", "optimize"}
VALID_ENGINES = {"js", "java", "python", "go", "php"}

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


def _call_llm(llm: ChatOpenAI, system_prompt: str, user_content: str) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(MAX_LLM_CALLS):
        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_content),
            ]
            response = llm.invoke(messages)
            out = _normalize_llm_output(response.content)
            if out:
                return out
            last_err = ValueError("LLM 返回空响应")
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM call attempt %d failed: %s", attempt + 1, e)
    raise ValueError(f"LLM 调用失败（{MAX_LLM_CALLS} 次尝试）：{last_err}")


def _stream_llm(llm: ChatOpenAI, system_prompt: str, user_content: str):
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
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
    return {
        "task": task,
        "description": input.get("description", "") or "",
        "pattern": input.get("pattern", "") or "",
        "flags": input.get("flags", "") or "",
        "engine": engine,
    }


# -------- System Prompt --------

SYSTEM_PROMPT = """你是一个正则表达式专家助手。用户会向你提出三种类型的请求：

1. **generate**：根据自然语言描述生成正则表达式
2. **explain**：解释给定正则表达式的含义
3. **optimize**：对给定正则表达式提出优化建议

## 输出规则

你必须返回 JSON 格式，包含以下字段：
- pattern: 字符串，生成/优化后的正则表达式（不含分隔符和标志位）
- explanation: 字符串，对正则的解释说明
- suggestions: 字符串数组，优化建议（无建议时返回空数组）

## 注意事项

- 如果用户指定了 engine，确保生成的正则在该引擎中兼容
- Go 引擎使用 RE2，不支持前瞻/后顾断言和反向引用
- Java 不支持 s (dotAll) 标志（Java 8），但 Java 17+ 支持
- Python 的 \\d 匹配 Unicode 数字，JS 仅匹配 [0-9]
- 优化时注意不要改变正则的匹配语义
- 解释时逐段拆解，用 → 标注每部分的含义
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

    return ""


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
    result.setdefault("pattern", "")
    result.setdefault("explanation", "")
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


# -------- 流式路径 --------

def _stream_invoke(p: dict, llm: ChatOpenAI):
    user_msg = _build_user_message(p)
    yield from _stream_llm(llm, SYSTEM_PROMPT, user_msg)


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
    else:
        result = _do_optimize(llm, p, _check_timeout)

    result["task"] = p["task"]
    # 顶层 answer 便于前端 Try Run（普通模式取 output.answer）直接展示
    result["answer"] = result.get("pattern", "")
    logger.info("Invoke done: task=%s %.2fs", p["task"], time.time() - t0)
    return result

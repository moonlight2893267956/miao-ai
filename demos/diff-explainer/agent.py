"""
diff-explainer Agent — AI 差异解释 Agent

为 miao-toolbox 文本对照工具提供 AI 分析能力，支持两种模式：
1. summary（全局摘要）：对完整 diff 结果生成变更摘要和影响分析
2. explain_selection（选中解释）：对用户选中的差异块生成逐段解释

入口函数 `invoke(input, config)`：
- 默认返回 dict（非流式）
- 如果 config["stream"] = True，返回 generator 逐 token 输出

工程模式复用 json-repairer / translate-agent：
- _build_llm()：max_retries=0 + timeout，禁用 SDK 内部重试
- TOTAL_TIMEOUT + _check_timeout(stage)：agent 总超时守护
- MAX_LLM_CALLS：LLM 调用失败重试
- _normalize_llm_output()：剥离 markdown fence
- 流式：config["stream"] 为 True 时逐 token yield {"token": ...}
"""
import json
import logging
import os
import time

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger("diff-explainer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

MODEL = os.environ.get("LLM_MODEL", os.environ.get("DASHSCOPE_MODEL", "qwen-plus"))
API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("DASHSCOPE_API_KEY", ""))
BASE_URL = os.environ.get("LLM_BASE_URL", os.environ.get("DASHSCOPE_BASE_URL", ""))
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", os.environ.get("DASHSCOPE_TEMPERATURE", "0.0")))
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", os.environ.get("DASHSCOPE_MAX_TOKENS", "4096")))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))

# 一次调用中最多 2 次 LLM 尝试（含首次），避免级联延迟
MAX_LLM_CALLS = 2
# 单次 agent 调用总超时，必须 < 平台 invoke_sync_timeout（默认 180s），留余量取 170
TOTAL_TIMEOUT = int(os.environ.get("AGENT_TOTAL_TIMEOUT", "170"))

logger.info(
    "Config: model=%s, base_url=%s, temp=%s, max_tokens=%s, timeout=%ss",
    MODEL,
    BASE_URL[:30] if BASE_URL else "(empty)",
    TEMPERATURE,
    MAX_TOKENS,
    LLM_TIMEOUT,
)

# --- 语言特定提示词模板 ---
LANGUAGE_HINTS = {
    "java": "这是 Java 代码变更，请关注类结构、方法签名、注解、导入变更。",
    "json": "这是 JSON 配置变更，请关注键值对的增删改，特别是嵌套结构。",
    "yaml": "这是 YAML 配置变更，请关注缩进层级和键值对变更。",
    "sql": "这是 SQL 变更，请关注表结构、索引、查询逻辑变更。",
    "python": "这是 Python 代码变更，请关注函数定义、类结构、导入和逻辑变更。",
    "javascript": "这是 JavaScript 代码变更，请关注函数声明、模块导入、逻辑变更。",
    "typescript": "这是 TypeScript 代码变更，请关注类型定义、接口、泛型变更。",
    "xml": "这是 XML 变更，请关注标签结构、属性变更。",
    "properties": "这是配置文件变更，请关注配置项的增删改。",
    "markdown": "这是 Markdown 文档变更，请关注标题层级、链接、代码块变更。",
}

# --- Summary 模式 System Prompt ---
SUMMARY_SYSTEM = """你是一个专业的代码差异分析助手。你的任务是分析代码/文本的差异，生成清晰的变更摘要和影响分析。

输出格式要求（严格按以下 JSON 格式返回，不要包含 markdown 代码块标记）：
{{
  "summary": "一段简洁的变更摘要（2-3句话）",
  "impact": "变更影响范围分析",
  "details": [
    {{
      "hunk_index": 0,
      "type": "added/removed/modified",
      "explanation": "该差异块的简要解释"
    }}
  ]
}}

分析原则：
1. 摘要要抓重点：什么功能/模块变了，变了什么
2. 影响分析要考虑：上下游依赖、配置变更影响、API 兼容性等
3. 逐块解释要具体：说清楚每个块改了什么、为什么改
4. 使用中文回答"""

# --- Explain Selection 模式 System Prompt ---
EXPLAIN_SYSTEM = """你是一个专业的代码差异解释助手。用户选中了一段代码差异，请给出清晰的解释。

输出格式要求（严格按以下 JSON 格式返回，不要包含 markdown 代码块标记）：
{{
  "explanation": "对选中差异的详细解释",
  "impact": "这段变更可能的影响范围",
  "suggestion": "建议或注意事项（如无则为空字符串）"
}}

解释原则：
1. 先说改了什么（事实）
2. 再说为什么改（推测意图）
3. 最后说影响和建议（风险评估）
4. 使用中文回答"""


def _build_llm() -> ChatOpenAI:
    """构建 LLM 实例。max_retries=0 禁用 SDK 内部重试，agent 自己重试。"""
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


def _build_human_prompt(input_data: dict, mode: str) -> str:
    """根据模式和输入数据构建用户提示词。"""

    language = input_data.get("language", "unknown")
    language_hint = LANGUAGE_HINTS.get(language, "")

    if mode == "summary":
        # 全局摘要模式：传入完整 diff 信息
        statistics = input_data.get("statistics", {})
        hunks = input_data.get("hunks", [])

        parts = []
        if language_hint:
            parts.append(f"[语言上下文] {language_hint}")

        parts.append(f"[变更统计] 新增 {statistics.get('additions', 0)} 行，"
                     f"删除 {statistics.get('deletions', 0)} 行，"
                     f"修改 {statistics.get('modifications', 0)} 处")
        parts.append(f"[差异数量] 共 {len(hunks)} 个差异块")

        # 拼接差异内容（限制总长度避免超 token）
        diff_content = _truncate_hunks(hunks, max_chars=8000)
        parts.append(f"[差异内容]\n{diff_content}")

        return "\n\n".join(parts)

    elif mode == "explain_selection":
        # 选中解释模式：传入选中的差异块
        selected_hunks = input_data.get("selected_hunks", [])
        context_before = input_data.get("context_before", "")
        context_after = input_data.get("context_after", "")

        parts = []
        if language_hint:
            parts.append(f"[语言上下文] {language_hint}")

        if context_before:
            parts.append(f"[前文上下文]\n{context_before[-2000:]}")

        diff_content = json.dumps(selected_hunks, ensure_ascii=False, indent=2)
        parts.append(f"[选中差异]\n{diff_content[:4000]}")

        if context_after:
            parts.append(f"[后文上下文]\n{context_after[:1000]}")

        return "\n\n".join(parts)

    else:
        return f"未知模式: {mode}，请使用 'summary' 或 'explain_selection'"


def _truncate_hunks(hunks: list, max_chars: int = 8000) -> str:
    """截断差异块内容，避免超 token 限制。"""
    result = []
    total = 0
    for i, hunk in enumerate(hunks):
        hunk_str = json.dumps(hunk, ensure_ascii=False)
        if total + len(hunk_str) > max_chars:
            remaining = len(hunks) - i
            result.append(f"... 省略剩余 {remaining} 个差异块 ...")
            break
        result.append(f"--- 差异块 {i} ---\n{hunk_str}")
        total += len(hunk_str)
    return "\n\n".join(result)


def _normalize_llm_output(text: str) -> str:
    """安全剥离 markdown code fence。"""
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    result_lines = lines[1:]
    while result_lines and result_lines[-1].strip() in ("```", "``"):
        result_lines.pop()
    return "\n".join(result_lines).strip()


def _try_parse_json(content: str):
    """尝试从 AI 响应中解析 JSON。"""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None


def _call_llm(llm: ChatOpenAI, system_prompt: str, user_content: str) -> str:
    """调用 LLM 并标准化输出，带一次重试（复用 MAX_LLM_CALLS 模式）。"""
    last_err: Exception | None = None
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
    """流式调用 LLM，逐 token yield {"token": ...}。"""
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    for chunk in llm.stream(messages):
        if chunk.content:
            yield {"token": chunk.content}


def invoke(input: dict, config: dict = None):
    """Miao agent 标准入口。

    Args:
        input: 调用方传入的 dict，包含：
            - mode: "summary" | "explain_selection"
            - language: 文件语言类型（可选）
            - statistics: 变更统计（summary 模式）
            - hunks: 差异块列表（summary 模式）
            - selected_hunks: 选中的差异块（explain_selection 模式）
            - context_before/context_after: 上下文（explain_selection 模式，可选）
        config: 运行时 config（config["stream"]=True 时走流式）

    Returns:
        dict 或 generator
    """
    config = config or {}
    mode = input.get("mode", "summary")
    if mode not in ("summary", "explain_selection"):
        mode = "summary"

    human_prompt = _build_human_prompt(input, mode)
    system_prompt = SUMMARY_SYSTEM if mode == "summary" else EXPLAIN_SYSTEM

    llm = _build_llm()

    # 流式模式
    if config.get("stream"):
        return _stream_llm(llm, system_prompt, human_prompt)

    # 非流式模式：超时守护
    t0 = time.time()

    def _check_timeout(stage: str) -> None:
        if time.time() - t0 > TOTAL_TIMEOUT:
            raise TimeoutError(f"Agent 总超时（{TOTAL_TIMEOUT}s），阶段：{stage}")

    _check_timeout("llm")
    content = _call_llm(llm, system_prompt, human_prompt)
    _check_timeout("parse")
    parsed = _try_parse_json(content)

    result = {
        "mode": mode,
        "analysis": parsed if parsed else content,
        "model": MODEL,
    }
    # 顶层 answer 便于前端 Try Run（普通模式取 output.answer）直接展示；
    # 流式模式仍逐 token 输出，不受影响。analysis 字段保留向后兼容。
    result["answer"] = result["analysis"]
    logger.info("Invoke done: mode=%s %.2fs", mode, time.time() - t0)
    return result

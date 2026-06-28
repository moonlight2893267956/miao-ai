"""
diff-explainer Agent — AI 差异解释 Agent

为 miao-toolbox 文本对照工具提供 AI 分析能力，支持两种模式：
1. summary（全局摘要）：对完整 diff 结果生成变更摘要和影响分析
2. explain_selection（选中解释）：对用户选中的差异块生成逐段解释

入口函数 `invoke(input, config)`：
- 默认返回 dict（非流式）
- 如果 config["stream"] = True，返回 generator 逐 token 输出
"""
import json
import os

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# --- LLM 初始化 ---
_llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL") or os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    temperature=0,
    api_key=os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL"),
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
            parts.append(f"[后文上下文]\n{context_after[:1000:]}")

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


def invoke(input: dict, config: dict):
    """Miao agent 入口。

    Args:
        input: 调用方传入的 dict，包含：
            - mode: "summary" | "explain_selection"
            - language: 文件语言类型（可选）
            - statistics: 变更统计（summary 模式）
            - hunks: 差异块列表（summary 模式）
            - selected_hunks: 选中的差异块（explain_selection 模式）
            - context_before/context_after: 上下文（explain_selection 模式，可选）
        config: 运行时 config

    Returns:
        dict 或 generator
    """
    mode = input.get("mode", "summary")
    human_prompt = _build_human_prompt(input, mode)

    system_prompt = SUMMARY_SYSTEM if mode == "summary" else EXPLAIN_SYSTEM

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
    ])
    chain = prompt | _llm

    # 流式模式
    if config.get("stream"):
        def _stream():
            for chunk in chain.stream({"input": human_prompt}):
                if chunk.content:
                    yield {"token": chunk.content}
        return _stream()

    # 非流式模式
    response = chain.invoke({"input": human_prompt})
    content = response.content

    # 尝试解析为 JSON（AI 可能返回 markdown 包裹的 JSON）
    parsed = _try_parse_json(content)

    return {
        "mode": mode,
        "analysis": parsed if parsed else content,
        "model": os.getenv("LLM_MODEL") or os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    }


def _try_parse_json(content: str):
    """尝试从 AI 响应中解析 JSON，处理 markdown 代码块包裹。"""
    # 去掉 markdown 代码块标记
    text = content.strip()
    if text.startswith("```"):
        # 去掉首行 ```json 和末行 ```
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

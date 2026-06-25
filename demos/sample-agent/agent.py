"""
样例 agent：简单的 LangChain + DashScope（qwen-plus）。

入口函数 `invoke(input, config)`：
- 默认返回 dict（非流式）
- 如果调用方通过 config["stream"] = True 请求流式，返回 generator 逐 token 输出
"""
import os

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

_llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL") or os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    temperature=0,
    api_key=os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL"),
)
_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "你是一个简洁的中文助手。"),
        ("human", "{input}"),
    ]
)
_chain = _prompt | _llm


def invoke(input: dict, config: dict):
    """Miao agent 入口。

    Args:
        input: 调用方传入的 dict，至少包含 'question'
        config: 调用方传入的运行时 config
    Returns:
        dict 或 generator：流式模式下逐 token yield，非流式模式返回完整 dict
    """
    question = input.get("question") or input.get("input") or "你好"

    # 流式模式：返回内部 generator，逐 token yield
    if config.get("stream"):
        def _stream():
            for chunk in _chain.stream({"input": question}):
                if chunk.content:
                    yield {"token": chunk.content}
        return _stream()

    # 非流式模式：返回完整 dict
    response = _chain.invoke({"input": question})
    model = os.getenv("LLM_MODEL") or os.getenv("DASHSCOPE_MODEL", "qwen-plus")
    return {"answer": response.content, "model": model}

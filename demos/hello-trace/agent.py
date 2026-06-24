"""
最小 demo：单轮 LLM 调用 + Langfuse trace（用 DashScope 通义千问 / OpenAI 兼容模式）。

跑这个文件后，去 Langfuse Cloud（https://cloud.langfuse.com）应该看到一条 trace。
"""
import os
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langfuse import get_client
from langfuse.langchain import CallbackHandler

load_dotenv()

langfuse = get_client()
handler = CallbackHandler()

# DashScope OpenAI 兼容模式：直接用 ChatOpenAI + 自定义 base_url
llm = ChatOpenAI(
    model=os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    temperature=0,
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("DASHSCOPE_BASE_URL"),
)
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个简洁的中文助手。"),
    ("human", "{question}"),
])
chain = prompt | llm

if __name__ == "__main__":
    question = "用一句话解释 Langfuse 是做什么的。"
    response = chain.invoke(
        {"question": question},
        config={
            "callbacks": [handler],
            "metadata": {
                "langfuse_user_id": "demo-user",
                "langfuse_session_id": "demo-session-1",
                "langfuse_tags": ["hello-trace", "phase-0", "dashscope"],
            },
        },
    )
    print(f"Q: {question}")
    print(f"A: {response.content}")

    # 短任务务必显式 flush，否则 trace 可能丢
    langfuse.flush()

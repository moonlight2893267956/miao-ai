"""模型接入：配置、客户端、一次调用。

沿用原 agent.py 的三个关键决定（它们都是踩过坑换来的）：
  * 温度强制 0：这是结构化抽取任务，自由发挥只会提高格式/断言出错率。
  * 关闭 SDK 自带重试（max_retries=0）：重试由上层统一控制，否则一次请求内部
    静默重试多次会把超时吃光，最后连错误都来不及返回。
  * 默认关闭「思考」：平台绑的 deepseek-v4-flash 是推理模型，实测它会把预算
    全烧在思维链上、正文为空（表现为「输出不是合法 JSON」→ 重试 → 90s+ 仍失败）。
    可用 LLM_DISABLE_THINKING=0 关闭这个行为。
"""
from __future__ import annotations

import json
import os
import re

MODEL = os.environ.get("LLM_MODEL") or os.environ.get("DASHSCOPE_MODEL") or "qwen-plus"
API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or ""
BASE_URL = os.environ.get("LLM_BASE_URL") or os.environ.get("DASHSCOPE_BASE_URL") or ""
TEMPERATURE = 0.0
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS") or os.environ.get("DASHSCOPE_MAX_TOKENS") or "2048")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "70"))
DISABLE_THINKING = (
    os.environ.get("LLM_DISABLE_THINKING", "1").strip().lower() not in ("0", "false", "no")
)


def build_client():
    """构造模型客户端。langchain 延迟导入：依赖缺失要变成一次可解释的返回，
    而不是平台看到的「进程起不来 / crashed」。"""
    from langchain_openai import ChatOpenAI

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
    if DISABLE_THINKING:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(**kwargs)


def call(client, system_prompt: str, user_prompt: str) -> str:
    """一次调用，返回文本；异常直接抛给上层按错误契约处理。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = client.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    return resp.content or ""


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")[1:]
    while lines and lines[-1].strip() in ("```", "``"):
        lines.pop()
    return "\n".join(lines).strip()


def extract_json(text: str):
    """从模型输出里取出 JSON 对象；取不到返回 None。"""
    cleaned = _strip_fence(text)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(cleaned[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def as_suggestions(raw, limit: int = 4) -> list[str]:
    """归一化模型给的 suggestions。

    提示词里写的是数组，但模型经常把「一句话提醒」直接给成**字符串**。
    此时若照常遍历（`for s in raw`），字符串会被按字符拆开 —— 实测出现过
    「记住‘编’字…」变成易混提醒里的 记 / 住 / ‘ / 编 四条散字。
    所以字符串一律当作**一条**建议；非字符串项直接丢弃（它们是模型的自由发挥，
    不是本 agent 的契约）。
    """
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []

    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def is_timeout(exc: Exception) -> bool:
    """判断是否超时（用于决定要不要重试）。"""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg

"""Resolve per-agent environment variables for agent runtimes.

除了 LLM 相关变量（``LLM_*`` / ``DASHSCOPE_*``），也注入**外部工具服务的 key**
（当前是 Tavily 联网检索）。agent 通过 ``os.environ`` 读取它们 —— 密钥只存在于
agent 子进程/容器的环境里，不写进镜像、不落到 agent 代码包。
"""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..crypto import CryptoError, decrypt_secret
from ..models.agent import Agent
from ..models.llm_model import LlmModel


def _tool_service_env() -> dict[str, str]:
    """外部工具服务的 key（全局级，配一次所有 agent 都能用）。

    空值**不注入**：agent 侧会如实降级（wubi-explainer 会发一条 log 说明
    「未配置 TAVILY_API_KEY，跳过联网」），不会因为缺 key 而起不来。

    ``strip()`` 是必要的：纯空白串若原样注入，agent 端 ``os.environ.get(...).strip()``
    得到空串后行为等同「未配置」，但排查时看容器环境变量却会以为「配过了」——
    这种假象会让排错方向跑偏。
    """
    env: dict[str, str] = {}
    tavily_key = (settings.tavily_api_key or "").strip()
    if tavily_key:
        env["TAVILY_API_KEY"] = tavily_key
    return env


def _global_fallback_env() -> dict[str, str]:
    env = _tool_service_env()
    if settings.dashscope_api_key:
        env["LLM_API_KEY"] = settings.dashscope_api_key
        env["DASHSCOPE_API_KEY"] = settings.dashscope_api_key
    if settings.dashscope_base_url:
        env["LLM_BASE_URL"] = settings.dashscope_base_url
        env["DASHSCOPE_BASE_URL"] = settings.dashscope_base_url
    if settings.dashscope_model:
        env["LLM_MODEL"] = settings.dashscope_model
        env["DASHSCOPE_MODEL"] = settings.dashscope_model
    return env


def _env_from_model(model: LlmModel) -> dict[str, str]:
    try:
        api_key = decrypt_secret(model.provider.api_key_encrypted)
    except CryptoError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    env = {
        "LLM_API_KEY": api_key,
        "LLM_BASE_URL": model.provider.base_url,
        "LLM_MODEL": model.model_id,
        "LLM_MAX_TOKENS": str(model.max_tokens),
        "LLM_TEMPERATURE": str(model.temperature_default),
        # Backward-compatible aliases for existing DashScope-oriented agents.
        "DASHSCOPE_API_KEY": api_key,
        "DASHSCOPE_BASE_URL": model.provider.base_url,
        "DASHSCOPE_MODEL": model.model_id,
        "DASHSCOPE_MAX_TOKENS": str(model.max_tokens),
        "DASHSCOPE_TEMPERATURE": str(model.temperature_default),
    }
    # 工具服务 key 与选哪个模型无关，任何路径都要带上
    env.update(_tool_service_env())
    return env


async def _load_model(session: AsyncSession, model_id: uuid.UUID) -> LlmModel | None:
    result = await session.execute(
        select(LlmModel)
        .options(selectinload(LlmModel.provider))
        .where(LlmModel.id == model_id)
    )
    return result.scalar_one_or_none()


async def resolve_llm_env(agent_id: uuid.UUID, session: AsyncSession) -> dict[str, str]:
    """Resolve selected model env for an agent, falling back to system/global defaults."""
    agent = await session.get(Agent, agent_id)
    if not agent:
        return _global_fallback_env()

    model: LlmModel | None = None
    if agent.model_id:
        model = await _load_model(session, agent.model_id)
    if model is None:
        result = await session.execute(
            select(LlmModel)
            .options(selectinload(LlmModel.provider))
            .where(LlmModel.is_default.is_(True))
            .order_by(LlmModel.created_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()

    if model is None:
        return _global_fallback_env()
    return _env_from_model(model)

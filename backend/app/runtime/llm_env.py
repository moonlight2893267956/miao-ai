"""Resolve per-agent LLM environment variables for agent runtimes."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..crypto import CryptoError, decrypt_secret
from ..models.agent import Agent
from ..models.llm_model import LlmModel


def _global_fallback_env() -> dict[str, str]:
    env: dict[str, str] = {}
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

    return {
        "LLM_API_KEY": api_key,
        "LLM_BASE_URL": model.provider.base_url,
        "LLM_MODEL": model.model_id,
        # Backward-compatible aliases for existing DashScope-oriented agents.
        "DASHSCOPE_API_KEY": api_key,
        "DASHSCOPE_BASE_URL": model.provider.base_url,
        "DASHSCOPE_MODEL": model.model_id,
    }


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

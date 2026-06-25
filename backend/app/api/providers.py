"""LLM provider CRUD API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import CryptoError, encrypt_secret
from ..db import get_session
from ..models.agent import Agent
from ..models.llm_model import LlmModel
from ..models.model_provider import ModelProvider
from ..schemas.model_provider import ProviderCreate, ProviderRead, ProviderUpdate

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=list[ProviderRead])
async def list_providers(session: AsyncSession = Depends(get_session)) -> list[ModelProvider]:
    result = await session.execute(select(ModelProvider).order_by(ModelProvider.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=ProviderRead, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderCreate, session: AsyncSession = Depends(get_session)
) -> ModelProvider:
    existing = await session.execute(select(ModelProvider).where(ModelProvider.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Provider '{payload.name}' already exists")
    try:
        encrypted = encrypt_secret(payload.api_key)
    except CryptoError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    provider = ModelProvider(
        name=payload.name,
        api_key_encrypted=encrypted,
        base_url=str(payload.base_url),
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


@router.put("/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: uuid.UUID,
    payload: ProviderUpdate,
    session: AsyncSession = Depends(get_session),
) -> ModelProvider:
    provider = await session.get(ModelProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if payload.name is not None and payload.name != provider.name:
        existing = await session.execute(select(ModelProvider).where(ModelProvider.name == payload.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Provider '{payload.name}' already exists")
        provider.name = payload.name
    if payload.base_url is not None:
        provider.base_url = str(payload.base_url)
    if payload.api_key is not None:
        try:
            provider.api_key_encrypted = encrypt_secret(payload.api_key)
        except CryptoError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    await session.commit()
    await session.refresh(provider)
    return provider


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> None:
    provider = await session.get(ModelProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider_model_ids = select(LlmModel.id).where(LlmModel.provider_id == provider_id)
    await session.execute(
        update(Agent).where(Agent.model_id.in_(provider_model_ids)).values(model_id=None)
    )
    await session.delete(provider)
    await session.commit()

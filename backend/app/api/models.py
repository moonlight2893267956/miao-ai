"""LLM model CRUD API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models.agent import Agent
from ..models.llm_model import LlmModel
from ..models.model_provider import ModelProvider
from ..schemas.llm_model import LlmModelCreate, LlmModelRead, LlmModelUpdate

router = APIRouter(prefix="/models", tags=["models"])


def _read_model(model: LlmModel) -> LlmModelRead:
    return LlmModelRead(
        id=model.id,
        name=model.name,
        provider_id=model.provider_id,
        model_id=model.model_id,
        max_tokens=model.max_tokens,
        temperature_default=model.temperature_default,
        is_default=model.is_default,
        created_at=model.created_at,
        provider_name=model.provider.name if model.provider else None,
    )


async def _clear_default(session: AsyncSession, except_id: uuid.UUID | None = None) -> None:
    stmt = update(LlmModel).values(is_default=False)
    if except_id is not None:
        stmt = stmt.where(LlmModel.id != except_id)
    await session.execute(stmt)


@router.get("", response_model=list[LlmModelRead])
async def list_models(session: AsyncSession = Depends(get_session)) -> list[LlmModelRead]:
    result = await session.execute(
        select(LlmModel)
        .options(selectinload(LlmModel.provider))
        .order_by(LlmModel.created_at.desc())
    )
    return [_read_model(model) for model in result.scalars().all()]


@router.post("", response_model=LlmModelRead, status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: LlmModelCreate, session: AsyncSession = Depends(get_session)
) -> LlmModelRead:
    provider = await session.get(ModelProvider, payload.provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    model = LlmModel(
        name=payload.name,
        provider_id=payload.provider_id,
        model_id=payload.model_id,
        max_tokens=payload.max_tokens,
        temperature_default=payload.temperature_default,
        is_default=payload.is_default,
    )
    session.add(model)
    if payload.is_default:
        await session.flush()
        await _clear_default(session, model.id)
    await session.commit()
    result = await session.execute(
        select(LlmModel).options(selectinload(LlmModel.provider)).where(LlmModel.id == model.id)
    )
    model = result.scalar_one()
    return _read_model(model)


@router.put("/{model_id}", response_model=LlmModelRead)
async def update_model(
    model_id: uuid.UUID,
    payload: LlmModelUpdate,
    session: AsyncSession = Depends(get_session),
) -> LlmModelRead:
    model = await session.get(LlmModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if payload.name is not None:
        model.name = payload.name
    if payload.model_id is not None:
        model.model_id = payload.model_id
    if payload.max_tokens is not None:
        model.max_tokens = payload.max_tokens
    if payload.temperature_default is not None:
        model.temperature_default = payload.temperature_default
    if payload.is_default is not None:
        model.is_default = payload.is_default
        if payload.is_default:
            await _clear_default(session, model.id)

    await session.commit()
    result = await session.execute(
        select(LlmModel).options(selectinload(LlmModel.provider)).where(LlmModel.id == model.id)
    )
    model = result.scalar_one()
    return _read_model(model)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> None:
    model = await session.get(LlmModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    await session.execute(
        update(Agent).where(Agent.model_id == model_id).values(model_id=None)
    )
    await session.delete(model)
    await session.commit()


@router.post("/{model_id}/set-default", response_model=LlmModelRead)
async def set_default_model(
    model_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> LlmModelRead:
    model = await session.get(LlmModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    await _clear_default(session, model.id)
    model.is_default = True
    await session.commit()
    result = await session.execute(
        select(LlmModel).options(selectinload(LlmModel.provider)).where(LlmModel.id == model.id)
    )
    model = result.scalar_one()
    return _read_model(model)

"""
Agent CRUD API。
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.agent import Agent
from ..models.agent_version import AgentVersion
from ..runtime.registry import AgentRegistry
from ..schemas.agent import AgentCreate, AgentRead

router = APIRouter(prefix="/agents", tags=["agents"])


async def _with_status(
    agent: Agent, registry: AgentRegistry, session: AsyncSession
) -> AgentRead:
    """组装 AgentRead：DB 字段 + 实时 status / active_version（查 DB）。"""
    managed = registry.get(agent.name)
    active_version: str | None = None
    # 查 DB 中 is_active 的 version
    result = await session.execute(
        select(AgentVersion.version).where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.is_active.is_(True),
        )
    )
    row = result.first()
    if row:
        active_version = row[0]
    return AgentRead(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        created_at=agent.created_at,
        status=managed.status if managed else "stopped",
        active_version=active_version,
    )


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate, session: AsyncSession = Depends(get_session)
) -> AgentRead:
    existing = await session.execute(select(Agent).where(Agent.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent '{payload.name}' already exists",
        )
    agent = Agent(name=payload.name, description=payload.description)
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return await _with_status(agent, AgentRegistry.instance(), session)


@router.get("", response_model=list[AgentRead])
async def list_agents(session: AsyncSession = Depends(get_session)) -> list[AgentRead]:
    result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
    agents = list(result.scalars().all())
    registry = AgentRegistry.instance()
    return [await _with_status(a, registry, session) for a in agents]


@router.get("/{name}", response_model=AgentRead)
async def get_agent(
    name: str, session: AsyncSession = Depends(get_session)
) -> AgentRead:
    result = await session.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found"
        )
    return await _with_status(agent, AgentRegistry.instance(), session)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(name: str, session: AsyncSession = Depends(get_session)) -> None:
    # 先停 agent 进程
    registry = AgentRegistry.instance()
    managed = registry.get(name)
    if managed:
        await asyncio.to_thread(managed.stop)
        await registry.remove(name)
    # 再删 DB（CASCADE 清掉 versions 和 keys）
    result = await session.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found"
        )
    await session.delete(agent)
    await session.commit()

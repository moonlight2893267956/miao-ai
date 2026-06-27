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
from ..models.llm_model import LlmModel
from ..runtime.registry import AgentRegistry
from ..schemas.agent import AgentCreate, AgentModelUpdate, AgentRead

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
        model_id=agent.model_id,
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
    if payload.model_id is not None:
        model = await session.get(LlmModel, payload.model_id)
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")
    agent = Agent(
        name=payload.name,
        description=payload.description,
        model_id=payload.model_id,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return await _with_status(agent, AgentRegistry.instance(), session)


@router.put("/{name}/model", response_model=AgentRead)
async def update_agent_model(
    name: str,
    payload: AgentModelUpdate,
    session: AsyncSession = Depends(get_session),
) -> AgentRead:
    result = await session.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found"
        )
    if payload.model_id is not None:
        model = await session.get(LlmModel, payload.model_id)
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")
    agent.model_id = payload.model_id
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


@router.post("/{name}/stop", response_model=AgentRead)
async def stop_agent(
    name: str, session: AsyncSession = Depends(get_session)
) -> AgentRead:
    """停掉 agent 容器/进程，DB 定义（agent / versions / keys）全部保留。

    下次 invoke 会通过 `_try_auto_activate` 自动唤醒（复用 image_exists / needs_build 缓存）。
    幂等：重复调用不报错，直接返回当前 stopped 状态。
    """
    registry = AgentRegistry.instance()

    # 1. 找 DB 定义；不存在 → 404
    result = await session.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found"
        )

    # 2. 如果 agent 在 registry 里（running / building / crashed / idle）→ 杀容器/进程
    #    stop() 内部会把 managed.status 改成 "stopped"，但仍保留在 registry
    managed = registry.get(name)
    if managed is not None:
        await asyncio.to_thread(managed.stop)

    # 3. 同步 agent_versions.status = "stopped"（DB 持久化标记，幂等）
    av_result = await session.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .where(AgentVersion.is_active.is_(True))
    )
    av = av_result.scalar_one_or_none()
    if av is not None and av.status != "stopped":
        av.status = "stopped"
        session.add(av)
        await session.commit()
        await session.refresh(av)

    # 4. 重新读 managed（stop() 后 status="stopped"），返回组装结果
    return await _with_status(agent, registry, session)


@router.post("/{name}/activate", response_model=AgentRead)
async def activate_agent(
    name: str, session: AsyncSession = Depends(get_session)
) -> AgentRead:
    """从 stopped 状态唤醒 agent 容器/进程，DB 定义不动。

    复用 invoke.py 的 `_try_auto_activate` 路径：
    - 场景1：registry 里有该 agent 的 ManagedAgent（常见，stop 后留在 registry）
      → 轻量重启，docker 模式只起容器、venv 模式复用 .venv 缓存
    - 场景2：registry 里没有（很少见，比如 backend 重启后）
      → 新建 ManagedAgent + build_and_start

    幂等：agent 已 running 时再调也 OK（_try_auto_activate 会先查 running_count）。
    """
    # 1. 找 DB 定义；不存在 → 404
    result = await session.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found"
        )

    # 2. 调 _try_auto_activate 拉起 active version
    from .invoke import _try_auto_activate

    managed = await _try_auto_activate(name, session)
    if managed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to activate agent '{name}' (check server logs: build/start error)",
        )

    # 3. 同步 active version.status 到 managed.status
    av_result = await session.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .where(AgentVersion.is_active.is_(True))
    )
    av = av_result.scalar_one_or_none()
    if av is not None and av.status != managed.status:
        av.status = managed.status
        session.add(av)
        await session.commit()
        await session.refresh(av)

    return await _with_status(agent, AgentRegistry.instance(), session)

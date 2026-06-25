"""
Agent Version API。

1b：占位 URL。1c：实际上传 COS + activate 触发 Runtime。
"""
import asyncio
import shutil
import zipfile
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models.agent_version import AgentVersion
from ..runtime.manager import ManagedAgent
from ..runtime.llm_env import resolve_llm_env
from ..runtime.registry import AgentRegistry
from ..runtime.storage import download_zip, get_zip_stream, upload_zip
from ..schemas.agent_version import AgentVersionRead
from ..utils import get_agent_or_404

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/agents/{name}/versions", tags=["versions"])

# 用户 agent 代码解压根目录
WORK_ROOT = Path("/tmp/miao/agents")


def _agent_work_dir(agent_name: str) -> Path:
    d = WORK_ROOT / agent_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cos_key(agent_name: str, version: str) -> str:
    return f"agents/{agent_name}/{version}.zip"


@router.get("", response_model=list[AgentVersionRead])
async def list_versions(
    name: str, session: AsyncSession = Depends(get_session)
) -> list[AgentVersion]:
    agent = await get_agent_or_404(name, session)
    result = await session.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{version}/download")
async def download_version(
    name: str, version: str, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """下载版本 zip 文件（流式传输）。"""
    agent = await get_agent_or_404(name, session)

    result = await session.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent.id, AgentVersion.version == version
        )
    )
    av = result.scalar_one_or_none()
    if not av:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version '{version}' not found for agent '{name}'",
        )

    key = av.artifact_url
    filename = f"{name}-{version}.zip"

    return StreamingResponse(
        get_zip_stream(key),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("", response_model=AgentVersionRead, status_code=status.HTTP_201_CREATED)
async def upload_version(
    name: str,
    version: str = Form(...),
    file: UploadFile = File(...),
    entrypoint: str = Form(default="agent:invoke"),
    session: AsyncSession = Depends(get_session),
) -> AgentVersion:
    """上传 agent 代码包（zip）。"""
    agent = await get_agent_or_404(name, session)

    # 检查 version 重复
    existing = await session.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent.id, AgentVersion.version == version
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version '{version}' already exists for agent '{name}'",
        )

    # 读上传文件到内存，校验是 zip
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        # 简单校验 zip 格式
        import io

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
            if "agent.py" not in names:
                raise HTTPException(
                    status_code=400,
                    detail="zip must contain 'agent.py' (entrypoint)",
                )
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="not a valid zip file")

    # 写临时文件并上传到 COS
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        cos_key = await asyncio.to_thread(upload_zip, tmp_path, _cos_key(name, version))
    finally:
        tmp_path.unlink(missing_ok=True)

    # 存 DB
    av = AgentVersion(
        agent_id=agent.id,
        version=version,
        artifact_url=cos_key,
        entrypoint=entrypoint,
        status="building",
    )
    session.add(av)
    await session.commit()
    await session.refresh(av)
    log.info("version.uploaded", agent=name, version=version, cos_key=cos_key)
    return av


@router.post("/activate", response_model=AgentVersionRead)
async def activate_version(
    name: str, version: str, session: AsyncSession = Depends(get_session)
) -> AgentVersion:
    """激活版本：下载 zip + 构建 venv + 启动子进程。"""
    agent = await get_agent_or_404(name, session)
    result = await session.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent.id, AgentVersion.version == version
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version '{version}' not found for agent '{name}'",
        )
    if target.artifact_url.startswith("pending://"):
        raise HTTPException(status_code=400, detail="version has no artifact")

    # 取消所有 active，同时把旧版本的 status 重置为 stopped（它们不再运行）
    await session.execute(
        update(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .values(is_active=False, status="stopped")
    )
    target.is_active = True
    await session.commit()
    await session.refresh(target)

    # 进程池限制：在停旧 agent 之前检查，避免停了旧的又起不来新的
    registry = AgentRegistry.instance()
    old = registry.get(name)
    # 如果有旧 agent 在跑，停掉后会腾出名额，不需要检查
    # 如果没有旧 agent（或旧 agent 不是 running），需要检查上限
    if not old or old.status != "running":
        if registry.running_count() >= settings.agent_max_concurrent:
            # 回滚 DB 变更
            target.is_active = False
            await session.commit()
            raise HTTPException(
                status_code=429,
                detail=f"max concurrent agents reached ({settings.agent_max_concurrent})",
            )

    # 停掉旧 agent（如果有）
    if old:
        await asyncio.to_thread(old.stop)
        await registry.remove(name)

    # 准备 work_dir：清空旧的，从 COS 下载 zip 并解压
    work_dir = _agent_work_dir(name)
    # 清空 work_dir 里的代码（保留 .venv / .build_hash 缓存给 venv builder 决定）
    for p in work_dir.iterdir():
        if p.name in (".venv", ".build_hash", "agent.log"):
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

    zip_path = work_dir / "source.zip"
    await asyncio.to_thread(download_zip, target.artifact_url, zip_path)
    await asyncio.to_thread(_extract_zip, zip_path, work_dir)
    zip_path.unlink(missing_ok=True)

    # 创建 ManagedAgent 并 build+start
    runner_path = Path(__file__).parents[2] / "agent_templates" / "miao_runner.py"
    llm_env = await resolve_llm_env(agent.id, session)
    managed = ManagedAgent(
        name=name,
        version_id=str(target.id),
        work_dir=work_dir,
        runner_path=runner_path,
        entrypoint=target.entrypoint,
        runtime_mode=settings.agent_runtime_mode,
        llm_env=llm_env,
    )
    ok = await asyncio.to_thread(managed.build_and_start)
    if not ok:
        target.status = "crashed"
        await session.commit()
        await session.refresh(target)
        raise HTTPException(
            status_code=500,
            detail=f"agent failed to start: {managed.last_error}",
        )

    await registry.set(managed)
    target.status = managed.status
    await session.commit()
    await session.refresh(target)
    log.info("version.activated", agent=name, version=version, port=managed.port)
    return target


def _extract_zip(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)

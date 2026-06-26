"""
Miao AI backend entrypoint.

Phase 2: lifespan 启动恢复 + 后台 watchdog（崩溃重启 / 空闲回收）。
"""
import asyncio
import shutil
import zipfile
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, update

from .api.agents import router as agents_router
from .api.health import router as health_router
from .api.invoke import router as invoke_router
from .api.keys import router as keys_router
from .api.models import router as models_router
from .api.providers import router as providers_router
from .api.versions import router as versions_router
from .config import settings
from .db import AsyncSessionLocal, engine
from .logging import configure_logging, get_logger
from .models.agent import Agent
from .models.agent_version import AgentVersion
from .runtime.manager import ManagedAgent
from .runtime.llm_env import resolve_llm_env
from .runtime.registry import AgentRegistry
from .runtime.storage import download_zip
from .services.task_worker import TaskWorker

configure_logging(settings.log_level)
log = get_logger(__name__)

WORK_ROOT = Path("/tmp/miao/agents")


async def _recover_active_agents() -> None:
    """启动时从 DB 恢复所有 is_active 的 agent，启动子进程。

    注意：Docker 模式下 build 较慢（pip install），此函数应在后台执行，
    不阻塞 FastAPI lifespan 启动。
    """
    log.info("miao.recovery.start")
    registry = AgentRegistry.instance()
    runner_path = Path(__file__).parents[1] / "agent_templates" / "miao_runner.py"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentVersion).where(AgentVersion.is_active.is_(True))
        )
        active_versions = result.scalars().all()

    recovered = 0
    for av in active_versions:
        # 进程池限制：启动前检查并发上限
        if registry.running_count() >= settings.agent_max_concurrent:
            log.warning("miao.recovery.limit reached=%d max=%d",
                        registry.running_count(), settings.agent_max_concurrent)
            break
        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Agent).where(Agent.id == av.agent_id))
            agent = r.scalar_one_or_none()
            llm_env = await resolve_llm_env(agent.id, session) if agent else {}
        if not agent:
            log.warning("miao.recovery.agent_not_found version_id=%s", av.id)
            continue

        work_dir = WORK_ROOT / agent.name
        work_dir.mkdir(parents=True, exist_ok=True)
        zip_path = work_dir / "source.zip"
        try:
            await asyncio.to_thread(download_zip, av.artifact_url, zip_path)
        except Exception as e:
            log.warning("miao.recovery.download_failed agent=%s error=%s",
                        agent.name, e)
            # 恢复失败：标记 is_active=False 避免下次启动再次尝试
            async with AsyncSessionLocal() as s:
                await s.execute(
                    update(AgentVersion)
                    .where(AgentVersion.id == av.id)
                    .values(is_active=False)
                )
                await s.commit()
            continue

        # 清空旧代码（保留 .venv/.build_hash，排除刚下载的 source.zip）
        for p in work_dir.iterdir():
            if p.name in (".venv", ".build_hash", "agent.log", "source.zip"):
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(work_dir)
        except Exception as e:
            log.warning("miao.recovery.extract_failed agent=%s error=%s", agent.name, e)
            async with AsyncSessionLocal() as s:
                await s.execute(
                    update(AgentVersion)
                    .where(AgentVersion.id == av.id)
                    .values(is_active=False)
                )
                await s.commit()
            continue
        zip_path.unlink(missing_ok=True)

        managed = ManagedAgent(
            name=agent.name,
            version_id=str(av.id),
            work_dir=work_dir,
            runner_path=runner_path,
            entrypoint=av.entrypoint,
            runtime_mode=settings.agent_runtime_mode,
            max_restarts=settings.agent_max_restarts,
            restart_base_delay=settings.agent_restart_base_delay,
            llm_env=llm_env,
        )
        ok = await asyncio.to_thread(managed.build_and_start)
        if ok:
            await registry.set(managed)
            recovered += 1
            log.info("miao.recovery.ok agent=%s port=%d", agent.name, managed.port)
        else:
            log.warning("miao.recovery.failed agent=%s error=%s",
                        agent.name, managed.last_error)

    log.info("miao.recovery.done recovered=%d total_active=%d",
             recovered, len(active_versions))


async def _agent_watchdog() -> None:
    """后台 watchdog：检查 running agent 是否崩溃/空闲，执行重启/回收。"""
    log.info("miao.watchdog.start interval=%ds", settings.agent_watchdog_interval)
    registry = AgentRegistry.instance()

    while True:
        try:
            await asyncio.sleep(settings.agent_watchdog_interval)
            agents = registry.all()
            if not agents:
                continue

            for a in agents:
                agent_name = a.name

                # 1) 崩溃检测 + 自动重启
                if a.status == "crashed":
                    ok = await asyncio.to_thread(a.try_restart)
                    if ok:
                        log.info("miao.watchdog.restarted agent=%s", agent_name)
                    else:
                        await registry.remove(agent_name)
                        log.warning("miao.watchdog.gave_up agent=%s restarts=%d",
                                    agent_name, a.restart_count)

                elif a.status == "running":
                    # 2) 进程存活检测
                    if not a.is_alive():
                        a.status = "crashed"
                        a.last_error = "process died unexpectedly"
                        log.warning("miao.watchdog.detected_crash agent=%s", agent_name)

                    # 3) 空闲超时回收：停止进程但保留在 registry 中，invoke 时可自动唤醒
                    elif a.idle_seconds() > settings.agent_idle_timeout:
                        log.info("miao.watchdog.idle_stop agent=%s idle=%.0fs",
                                 agent_name, a.idle_seconds())
                        await asyncio.to_thread(a.stop)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("miao.watchdog.error")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("miao.startup", debug=settings.debug)
    # Phase 2: 后台恢复 is_active 的 agent（不阻塞启动，Docker build 较慢）
    recovery_task = asyncio.create_task(_recover_active_agents())
    # 启动后台 watchdog
    watchdog_task = asyncio.create_task(_agent_watchdog())
    # 创建异步任务 worker
    app.state.task_worker = TaskWorker(max_workers=settings.invoke_async_max_workers)
    yield
    log.info("miao.shutdown")
    # 停止 recovery（如果还在跑）
    recovery_task.cancel()
    try:
        await recovery_task
    except asyncio.CancelledError:
        pass
    # 停止 worker（等待运行中任务完成）
    await asyncio.to_thread(app.state.task_worker.shutdown)
    # 停止 watchdog 和所有 agent
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass
    registry = AgentRegistry.instance()
    agents = registry.all()
    for a in agents:
        try:
            await asyncio.to_thread(a.stop)
        except Exception:
            pass
    await engine.dispose()


app = FastAPI(
    title="Miao AI",
    version="0.1.0",
    description="Self-hosted AI agent platform",
    lifespan=lifespan,
)

# CORS：允许本地前端开发（3000 端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api/v1")
app.include_router(agents_router, prefix="/api/v1")
app.include_router(versions_router, prefix="/api/v1")
app.include_router(keys_router, prefix="/api/v1")
app.include_router(providers_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")
app.include_router(invoke_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "miao-ai", "version": "0.1.0"}

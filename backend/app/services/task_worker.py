"""
异步 invoke 任务执行器。

每个任务在**工作线程**里执行 invoke（agent 调用是阻塞的），DB 读写则回到
**主事件循环**执行：engine 与连接池绑定在主循环上，若在工作线程里
`asyncio.new_event_loop()` 后再用同一个池，会抛
``Future attached to a different loop``，而且该连接退回池子后会污染主循环
（下一个复用到它的请求直接 500）。因此这里统一用
``run_coroutine_threadsafe`` 把 DB 操作交回主循环，webhook 这类阻塞 IO
仍留在工作线程。

带会话的任务额外回写会话轮次并释放会话闸门（闸门是 ``threading.Lock``，
可跨线程释放）。
"""
import asyncio
import logging
import time
import uuid as _uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from sqlalchemy import update

from ..models.invoke_task import InvokeTask
from . import conversation as conversation_service
from .conversation import TurnAccounting

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ===== 主事件循环上执行的 DB 步骤 =====


async def _db_mark_running(session_factory, task_uuid: _uuid.UUID) -> None:
    async with session_factory() as session:
        await session.execute(
            update(InvokeTask).where(InvokeTask.id == task_uuid).values(status="running")
        )
        await session.commit()


async def _db_mark_success(
    session_factory,
    task_uuid: _uuid.UUID,
    *,
    output: dict,
    trace_id: str | None,
    accounting: TurnAccounting | None,
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(InvokeTask)
            .where(InvokeTask.id == task_uuid)
            .values(
                status="success",
                output_payload=output,
                trace_id=trace_id,
                completed_at=_now(),
            )
        )
        await session.commit()
        if accounting is not None:
            await conversation_service.finish_turn_by_id(
                session, accounting, succeeded=True, output=output, trace_id=trace_id
            )


async def _db_mark_failed(
    session_factory,
    task_uuid: _uuid.UUID,
    *,
    error_message: str,
    accounting: TurnAccounting | None,
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(InvokeTask)
            .where(InvokeTask.id == task_uuid)
            .values(status="failed", error_message=error_message, completed_at=_now())
        )
        await session.commit()
        if accounting is not None:
            # 失败也要回写轮次并释放闸门，否则该会话会一直 409
            await conversation_service.finish_turn_by_id(
                session, accounting, succeeded=False
            )


async def _db_mark_webhook(session_factory, task_uuid: _uuid.UUID, delivered: bool) -> None:
    async with session_factory() as session:
        await session.execute(
            update(InvokeTask)
            .where(InvokeTask.id == task_uuid)
            .values(webhook_delivered=delivered)
        )
        await session.commit()


class TaskWorker:
    """管理异步 invoke 任务的提交、执行和 webhook 回调。"""

    def __init__(self, max_workers: int = 4, loop: asyncio.AbstractEventLoop | None = None):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="miao-invoke"
        )
        # 注意：_running 在主线程（submit 写入）和工作线程（_execute_task pop）间读写。
        # Python GIL 保证 dict 单操作原子性，跨线程安全。
        # shutdown() 中 len() 读取的快照可能略滞后，但 shutdown 只在进程退出时调用，不影响正确性。
        self._running: dict[str, Future] = {}
        # DB 操作要回到这个循环上跑；缺省在 submit() 首次调用时从运行中的循环捕获
        self._loop = loop

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def submit(
        self,
        task_id: str,
        agent_name: str,
        managed,  # ManagedAgent
        payload: dict,
        config: dict,
        webhook_url: str,
        timeout: float,
        session_factory,  # AsyncSessionLocal
        settings,  # app Settings
        accounting: TurnAccounting | None = None,  # 会话轮次收尾句柄（无会话为 None）
    ) -> None:
        """提交异步任务到线程池（在事件循环内调用）。"""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        future = self._executor.submit(
            self._execute_task,
            task_id, agent_name, managed, payload, config,
            webhook_url, timeout, session_factory, settings, accounting,
        )
        self._running[task_id] = future

    # ===== 工作线程内的执行 =====

    def _execute_task(
        self,
        task_id: str,
        agent_name: str,
        managed,
        payload: dict,
        config: dict,
        webhook_url: str,
        timeout: float,
        session_factory,
        settings,
        accounting: TurnAccounting | None = None,
    ) -> None:
        """工作线程：invoke 同步跑，DB 步骤回主循环，webhook 留本线程。"""
        # Uuid 列的绑定需要 UUID 对象；传字符串会在 bind processor 里抛 AttributeError
        task_uuid = _uuid.UUID(task_id)
        try:
            if not self._run_db(_db_mark_running(session_factory, task_uuid), "mark_running", required=True):
                return

            log.info("task_worker.invoke.start task_id=%s agent=%s", task_id, agent_name)
            try:
                result = managed.invoke(payload, timeout=timeout, config=config)
            except Exception as e:
                error_msg = str(e)
                self._run_db(
                    _db_mark_failed(
                        session_factory, task_uuid, error_message=error_msg, accounting=accounting
                    ),
                    "mark_failed",
                )
                webhook_body = {
                    "request_id": task_id,
                    "agent_name": agent_name,
                    "status": "failed",
                    "output": None,
                    "error": error_msg,
                    "trace_id": None,
                }
                if accounting is not None:
                    webhook_body["conversation_id"] = accounting.conversation_id
                    webhook_body["turn"] = accounting.turn_index
                delivered = self._post_webhook(
                    webhook_url, webhook_body,
                    settings.webhook_max_retries,
                    settings.webhook_retry_base_delay,
                )
                self._run_db(
                    _db_mark_webhook(session_factory, task_uuid, delivered), "mark_webhook"
                )
                log.warning("task_worker.invoke.failed task_id=%s error=%s", task_id, error_msg)
                return

            output = result.get("output", {})
            trace_id = result.get("trace_id")
            self._run_db(
                _db_mark_success(
                    session_factory,
                    task_uuid,
                    output=output,
                    trace_id=trace_id,
                    accounting=accounting,
                ),
                "mark_success",
            )

            webhook_body = {
                "request_id": task_id,
                "agent_name": agent_name,
                "status": "success",
                "output": output,
                "trace_id": trace_id,
            }
            if accounting is not None:
                webhook_body["conversation_id"] = accounting.conversation_id
                webhook_body["turn"] = accounting.turn_index
            delivered = self._post_webhook(
                webhook_url, webhook_body,
                settings.webhook_max_retries,
                settings.webhook_retry_base_delay,
            )
            self._run_db(
                _db_mark_webhook(session_factory, task_uuid, delivered), "mark_webhook"
            )
            log.info("task_worker.invoke.done task_id=%s delivered=%s", task_id, delivered)
        finally:
            self._running.pop(task_id, None)

    def _run_db(self, coro, context: str, *, required: bool = False) -> bool:
        """把 DB 协程交回主事件循环执行。

        :param required: True 时失败向上抛（任务无法被记账，直接放弃执行）
        :returns: 是否成功
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            log.error("task_worker.no_loop context=%s", context)
            if required:
                raise RuntimeError("task worker main event loop is not available")
            return False
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).result()
            return True
        except Exception:
            log.exception("task_worker.db_failed context=%s", context)
            if required:
                raise
            return False

    def _post_webhook(self, url: str, payload: dict, max_retries: int = 3, base_delay: float = 1.0) -> bool:
        """POST webhook，失败指数退避重试。返回是否成功投递。"""
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=10.0) as client:
                    r = client.post(url, json=payload)
                    if r.status_code < 500:
                        return True
            except Exception:
                pass
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                log.warning("webhook.retry url=%s attempt=%d", url, attempt + 1)
        log.error("webhook.failed url=%s", url)
        return False

    def shutdown(self) -> None:
        """优雅关闭：等待所有运行中的任务完成。"""
        log.info("task_worker.shutdown running=%d", len(self._running))
        self._executor.shutdown(wait=True)

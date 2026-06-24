"""
异步 invoke 任务执行器。

每个任务在线程池中执行 invoke → 更新 DB → POST webhook 回调。
"""
import asyncio
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)


class TaskWorker:
    """管理异步 invoke 任务的提交、执行和 webhook 回调。"""

    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        # 注意：_running 在主线程（submit 写入）和工作线程（_execute_task pop）间读写。
        # Python GIL 保证 dict 单操作原子性，跨线程安全。
        # shutdown() 中 len() 读取的快照可能略滞后，但 shutdown 只在进程退出时调用，不影响正确性。
        self._running: dict[str, Future] = {}

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
    ) -> None:
        """提交异步任务到线程池。"""
        future = self._executor.submit(
            self._execute_task,
            task_id, agent_name, managed, payload, config,
            webhook_url, timeout, session_factory, settings,
        )
        self._running[task_id] = future

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
    ) -> None:
        """在线程中执行：invoke → 更新 DB → POST webhook。"""
        # 在线程中创建独立的 event loop + DB session
        loop = asyncio.new_event_loop()

        async def _run():
            async with session_factory() as session:
                from sqlalchemy import select, update
                from app.models.invoke_task import InvokeTask

                # 标记 running
                await session.execute(
                    update(InvokeTask)
                    .where(InvokeTask.id == task_id)
                    .values(status="running")
                )
                await session.commit()

                try:
                    # 执行 invoke（在线程池中已经是线程，直接同步调）
                    result = managed.invoke(payload, timeout=timeout, config=config)
                    output = result.get("output", {})
                    trace_id = result.get("trace_id")

                    await session.execute(
                        update(InvokeTask)
                        .where(InvokeTask.id == task_id)
                        .values(
                            status="success",
                            output_payload=output,
                            trace_id=trace_id,
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()

                    # POST webhook 回调
                    webhook_body = {
                        "request_id": task_id,
                        "agent_name": agent_name,
                        "status": "success",
                        "output": output,
                        "trace_id": trace_id,
                    }
                    delivered = self._post_webhook(
                        webhook_url, webhook_body,
                        settings.webhook_max_retries,
                        settings.webhook_retry_base_delay,
                    )

                    # 记录 webhook 送达状态
                    await session.execute(
                        update(InvokeTask)
                        .where(InvokeTask.id == task_id)
                        .values(webhook_delivered=delivered)
                    )
                    await session.commit()

                except Exception as e:
                    error_msg = str(e)
                    await session.execute(
                        update(InvokeTask)
                        .where(InvokeTask.id == task_id)
                        .values(
                            status="failed",
                            error_message=error_msg,
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()

                    webhook_body = {
                        "request_id": task_id,
                        "agent_name": agent_name,
                        "status": "failed",
                        "output": None,
                        "error": error_msg,
                        "trace_id": None,
                    }
                    delivered = self._post_webhook(
                        webhook_url, webhook_body,
                        settings.webhook_max_retries,
                        settings.webhook_retry_base_delay,
                    )

                    # 记录 webhook 送达状态（即使任务失败，webhook 也可能成功投递）
                    await session.execute(
                        update(InvokeTask)
                        .where(InvokeTask.id == task_id)
                        .values(webhook_delivered=delivered)
                    )
                    await session.commit()

            # 清理
            self._running.pop(task_id, None)

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

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

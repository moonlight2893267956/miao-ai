"""
单 agent 的生命周期管理。

所有方法都是 sync —— 调用方（FastAPI 异步路由）用 `await asyncio.to_thread(...)` 包装。
"""
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .process import (
    find_free_port,
    kill_process,
    spawn_agent_process,
    wait_for_health,
)
from .venv import VenvBuilder

log = logging.getLogger(__name__)


@dataclass
class ManagedAgent:
    name: str
    version_id: str
    work_dir: Path
    runner_path: Path
    entrypoint: str
    venv_dir: Path | None = None
    port: int = 0
    process: subprocess.Popen | None = None
    status: str = "building"  # building / running / crashed / idle
    last_error: str | None = None
    restart_count: int = 0
    max_restarts: int = 5
    restart_base_delay: float = 2.0
    llm_env: dict[str, str] = field(default_factory=dict)
    last_invoke_at: float = field(default_factory=time.time)
    # 令牌桶限流
    rate_limit_qps: float = 10.0
    rate_limit_burst: int = 20
    _tokens: float = field(default=0, init=False)
    _last_refill: float = field(default_factory=time.time, init=False)
    _token_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    # Docker 模式
    runtime_mode: str = "venv"
    _docker: "DockerRunner | None" = field(default=None, init=False)

    def __post_init__(self):
        self._tokens = float(self.rate_limit_burst)

    def build_and_start(self) -> bool:
        if self.runtime_mode == "docker":
            return self._build_and_start_docker()
        return self._build_and_start_venv()

    def _build_and_start_venv(self) -> bool:
        """构建 venv + 启动子进程。"""
        self.status = "building"
        self.last_error = None
        try:
            builder = VenvBuilder(self.work_dir)
            if builder.needs_build():
                log.info("runtime.venv.build agent=%s", self.name)
                builder.build()
            self.venv_dir = builder.venv_dir
        except Exception as e:
            self.status = "crashed"
            self.last_error = f"venv build failed: {e}"
            log.exception("runtime.venv.build_failed agent=%s", self.name)
            return False
        return self._start_process()

    def _build_and_start_docker(self) -> bool:
        """构建 Docker 镜像 + 启动容器。"""
        import os
        from .docker import DockerBuilder, DockerRunner, build_dockerfile, docker_available

        if not docker_available():
            self.status = "crashed"
            self.last_error = "docker not available"
            return False

        self.status = "building"
        self.last_error = None
        image_tag = f"miao-agent:{self.name}-{self.version_id[:8]}"
        container_name = f"miao-{self.name}"

        try:
            # 生成 Dockerfile + .dockerignore + 复制 runner
            # env_vars 不再写入 Dockerfile，改为 docker run -e 传入
            build_dockerfile(self.work_dir, self.runner_path)

            # 每次激活强制构建镜像（--no-cache），避免代码更新后用旧镜像
            builder = DockerBuilder(self.work_dir, image_tag)
            log.info("docker.build agent=%s tag=%s", self.name, image_tag)
            builder.build(no_cache=True)

            # 准备 env_vars（通过 docker run -e 传入，不写入镜像）
            env_vars = {
                "LANGFUSE_PUBLIC_KEY": os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
                "LANGFUSE_SECRET_KEY": os.environ.get("LANGFUSE_SECRET_KEY", ""),
                "LANGFUSE_BASE_URL": os.environ.get("LANGFUSE_BASE_URL", ""),
            }
            env_vars.update(self.llm_env)

            # 启动容器
            self.port = find_free_port()
            runner = DockerRunner(
                image_tag=image_tag,
                container_name=container_name,
                port=self.port,
                cpu="1.0",
                memory="512m",
                env_vars=env_vars,
            )
            runner.start()

            if not wait_for_health(self.port, timeout=60):
                runner.stop()
                self.status = "crashed"
                self.last_error = "docker container health check timeout"
                return False

            self._docker = runner
            self.status = "running"
            self.last_invoke_at = time.time()
            log.info("docker.agent.started agent=%s port=%d", self.name, self.port)
            return True
        except Exception as e:
            self.status = "crashed"
            self.last_error = f"docker build/start failed: {e}"
            log.exception("docker.agent.failed agent=%s", self.name)
            return False

    def _start_process(self) -> bool:
        """启动子进程，端口冲突时自动重试（最多 3 次）。"""
        max_port_retries = 3
        for attempt in range(max_port_retries):
            try:
                try:
                    port = find_free_port()
                except RuntimeError:
                    self.status = "crashed"
                    self.last_error = "no free port available"
                    return False

                venv_python = str(self.venv_dir / "bin" / "python") if self.venv_dir else ""
                log_path = self.work_dir / "agent.log"
                proc = spawn_agent_process(
                    venv_python=venv_python,
                    runner_path=self.runner_path,
                    agent_dir=self.work_dir,
                    entrypoint=self.entrypoint,
                    port=port,
                    log_path=log_path,
                    extra_env=self.llm_env,
                )

                if not wait_for_health(port, timeout=30):
                    # 健康检查失败：关文件句柄 + kill 进程
                    if proc.stdout and hasattr(proc.stdout, "close"):
                        try:
                            proc.stdout.close()
                        except Exception:
                            pass
                    kill_process(proc)
                    # 端口可能冲突，换一个端口重试
                    if attempt < max_port_retries - 1:
                        log.warning("runtime.agent.health_fail agent=%s port=%d attempt=%d",
                                    self.name, port, attempt + 1)
                        continue
                    self.status = "crashed"
                    self.last_error = "health check timeout after retries"
                    return False

                # 启动成功
                self.port = port
                self.process = proc
                self.status = "running"
                self.last_invoke_at = time.time()
                log.info("runtime.agent.started agent=%s port=%d restart=%d",
                         self.name, self.port, self.restart_count)
                return True
            except Exception as e:
                self.status = "crashed"
                self.last_error = f"start failed: {e}"
                log.exception("runtime.agent.start_failed agent=%s", self.name)
                return False

        self.status = "crashed"
        self.last_error = "port retries exhausted"
        return False

    def try_restart(self) -> bool:
        """指数退避重启。连续失败超过 max_restarts 返回 False。"""
        if self.restart_count >= self.max_restarts:
            log.warning("runtime.agent.max_restarts agent=%s count=%d",
                        self.name, self.restart_count)
            return False

        delay = self.restart_base_delay * (2 ** self.restart_count)
        log.info("runtime.agent.restarting agent=%s attempt=%d delay=%.1f",
                 self.name, self.restart_count + 1, delay)
        time.sleep(delay)

        self.restart_count += 1
        # 按 runtime_mode 分发重启
        if self.runtime_mode == "docker":
            ok = self._build_and_start_docker()
        else:
            ok = self._start_process()
        if ok:
            self.restart_count = 0  # 成功则重置计数器
        return ok

    def stop(self) -> None:
        if self._docker:
            self._docker.stop()
            self._docker = None
        elif self.process:
            if self.process.stdout and hasattr(self.process.stdout, "close"):
                try:
                    self.process.stdout.close()
                except Exception:
                    pass
            kill_process(self.process)
            self.process = None
        self.status = "idle"
        self.port = 0

    def _refill_tokens(self) -> None:
        """补充令牌桶（调用方需持锁）。"""
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self.rate_limit_burst),
            self._tokens + elapsed * self.rate_limit_qps,
        )
        self._last_refill = now

    def try_acquire_token(self) -> bool:
        """尝试获取一个令牌。成功返回 True，被限流返回 False。线程安全。"""
        with self._token_lock:
            self._refill_tokens()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def _is_running(self) -> bool:
        """检查 agent 是否在运行（兼容 venv / docker 两种模式）。"""
        if self.runtime_mode == "docker":
            return self.status == "running" and self._docker is not None
        return self.status == "running" and self.process is not None

    def invoke(self, payload: dict, timeout: float = 60.0, config: dict | None = None) -> dict:
        if not self._is_running():
            raise RuntimeError(f"agent {self.name} not running (status={self.status})")
        self.last_invoke_at = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(
                    f"http://127.0.0.1:{self.port}/invoke",
                    json={"input": payload, "config": config or {}},
                )
                r.raise_for_status()
                return r.json()
        except Exception as e:
            if not self.is_alive():
                self.status = "crashed"
                self.last_error = f"process/container died: {e}"
            raise

    def invoke_stream(self, payload: dict, config: dict | None = None, timeout: float = 120.0):
        """流式调用 agent 子进程，返回 SSE 文本行迭代器。

        保留空行（SSE 事件分隔符），调用方需原样转发。
        """
        if not self._is_running():
            raise RuntimeError(f"agent {self.name} not running (status={self.status})")
        self.last_invoke_at = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST",
                    f"http://127.0.0.1:{self.port}/invoke/stream",
                    json={"input": payload, "config": config or {}},
                ) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        yield line
        except Exception as e:
            if not self.is_alive():
                self.status = "crashed"
                self.last_error = f"process/container died: {e}"
            raise

    def idle_seconds(self) -> float:
        """距离上次 invoke 的空闲秒数。"""
        return time.time() - self.last_invoke_at

    def is_alive(self) -> bool:
        """检查子进程/容器是否还在运行（非阻塞 poll/inspect）。"""
        if self._docker:
            return self._docker.is_running()
        return self.process is not None and self.process.poll() is None

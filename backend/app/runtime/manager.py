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
    wait_for_health_url,
)
from .venv import VenvBuilder

log = logging.getLogger(__name__)


def _detect_shared_network() -> str | None:
    """检测 backend 是否在 docker 网络里运行，如果是返回网络名。

    通过检查 hostname 找到当前 container 的网络名（docker-compose 创建的 <project>_default）。
    如果不在 container 里运行（直接在宿主机），返回 None（用默认 bridge + 端口映射）。
    """
    import os
    import subprocess

    try:
        # 找到自己所在的 container（通过 hostname 匹配）
        hostname = os.environ.get("HOSTNAME") or ""
        if not hostname:
            return None
        result = subprocess.run(
            ["docker", "inspect", hostname, "--format", "{{json .NetworkSettings.Networks}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        import json
        networks = json.loads(result.stdout)
        # 优先选择 docker-compose 创建的网络（通常是 miao-ai_default 或类似）
        # 排除 bridge / host / none
        candidates = [k for k in networks.keys() if k not in ("bridge", "host", "none")]
        if candidates:
            return candidates[0]
    except Exception:
        return None
    return None


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
    status: str = "building"  # building / running / crashed / idle / stopped
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
    _image_tag: str | None = field(default=None, init=False)  # 已构建的镜像 tag
    _container_name: str | None = field(default=None, init=False)  # 容器名（用于 DNS 解析）
    _health_url: str | None = field(default=None, init=False)  # agent health check URL

    def __post_init__(self):
        self._tokens = float(self.rate_limit_burst)

    def build_and_start(self) -> bool:
        if self.runtime_mode == "docker":
            return self._build_and_start_docker()
        return self._build_and_start_venv()

    def _start_docker_container(self) -> bool:
        """仅启动容器（不重新 build），用于 idle 唤醒场景。"""
        import os
        from .docker import AGENT_INTERNAL_PORT, DockerRunner, docker_available

        if not docker_available():
            self.status = "crashed"
            self.last_error = "docker not available"
            return False

        if not self._image_tag:
            # 没有已构建的镜像 tag，必须走完整 build 流程
            return self._build_and_start_docker()

        container_name = f"miao-{self.name}"
        self._container_name = container_name
        env_vars = {
            "LANGFUSE_PUBLIC_KEY": os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            "LANGFUSE_SECRET_KEY": os.environ.get("LANGFUSE_SECRET_KEY", ""),
            "LANGFUSE_BASE_URL": os.environ.get("LANGFUSE_BASE_URL", ""),
        }
        env_vars.update(self.llm_env)

        try:
            # 共享 docker 网络（agent 容器 join miao-backend 所在网络，用 container name DNS 解析）
            shared_network = _detect_shared_network()
            runner = DockerRunner(
                image_tag=self._image_tag,
                container_name=container_name,
                cpu="1.0",
                memory="512m",
                env_vars=env_vars,
                shared_network=shared_network,
            )
            runner.start()
            self._health_url = runner.health_url

            if not wait_for_health_url(runner.health_url, timeout=60):
                runner.stop()
                self.status = "crashed"
                self.last_error = f"docker container health check timeout (url={runner.health_url})"
                return False

            self._docker = runner
            self.status = "running"
            self.last_invoke_at = time.time()
            log.info("docker.container.resumed agent=%s health_url=%s image=%s",
                     self.name, runner.health_url, self._image_tag)
            return True
        except Exception as e:
            self.status = "crashed"
            self.last_error = f"docker start failed: {e}"
            log.exception("docker.container.resume_failed agent=%s", self.name)
            return False

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
        from .docker import DockerBuilder, DockerRunner, build_dockerfile, docker_available, image_exists

        if not docker_available():
            self.status = "crashed"
            self.last_error = "docker not available"
            return False

        self.status = "building"
        self.last_error = None
        image_tag = f"miao-agent:{self.name}-{self.version_id[:8]}"
        container_name = f"miao-{self.name}"
        self._container_name = container_name

        try:
            # 生成 Dockerfile + .dockerignore + 复制 runner
            build_dockerfile(self.work_dir, self.runner_path)

            # 检查镜像是否已存在：同 version_id 的镜像如果已经构建过，跳过 build
            if image_exists(image_tag):
                log.info("docker.image.skip_build agent=%s tag=%s (image exists)", self.name, image_tag)
            else:
                builder = DockerBuilder(self.work_dir, image_tag)
                log.info("docker.build agent=%s tag=%s", self.name, image_tag)
                builder.build(no_cache=True)

            # 记录已构建的 image tag，idle 唤醒时可跳过 build
            self._image_tag = image_tag

            # 准备 env_vars（通过 docker run -e 传入，不写入镜像）
            env_vars = {
                "LANGFUSE_PUBLIC_KEY": os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
                "LANGFUSE_SECRET_KEY": os.environ.get("LANGFUSE_SECRET_KEY", ""),
                "LANGFUSE_BASE_URL": os.environ.get("LANGFUSE_BASE_URL", ""),
            }
            env_vars.update(self.llm_env)

            # 共享 docker 网络（agent 容器 join miao-backend 所在网络）
            shared_network = _detect_shared_network()
            runner = DockerRunner(
                image_tag=image_tag,
                container_name=container_name,
                cpu="1.0",
                memory="512m",
                env_vars=env_vars,
                shared_network=shared_network,
            )
            runner.start()
            self._health_url = runner.health_url

            if not wait_for_health_url(runner.health_url, timeout=60):
                runner.stop()
                self.status = "crashed"
                self.last_error = f"docker container health check timeout (url={runner.health_url})"
                return False

            self._docker = runner
            self.status = "running"
            self.last_invoke_at = time.time()
            log.info("docker.agent.started agent=%s health_url=%s", self.name, runner.health_url)
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
            # idle 唤醒 / 崩溃重启：如果有已构建镜像，只启动容器不重新 build
            ok = self._start_docker_container()
        else:
            ok = self._start_process()
        if ok:
            self.restart_count = 0  # 成功则重置计数器
        return ok

    def stop(self) -> None:
        if self._docker:
            self._docker.stop()
            self._docker = None
            # 注意：保留 self._image_tag，idle 唤醒时可直接 docker run 而不重新 build
        elif self.process:
            if self.process.stdout and hasattr(self.process.stdout, "close"):
                try:
                    self.process.stdout.close()
                except Exception:
                    pass
            kill_process(self.process)
            self.process = None
        # stopped 含义：进程/容器已停（可能是用户手动 stop，也可能是 watchdog idle_stop）
        # 下次 invoke 会通过 _try_auto_activate 自动唤醒
        self.status = "stopped"
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

    def _invoke_base_url(self) -> str:
        """获取 invoke 调用的 base URL。

        Docker 模式：用容器名 + 内部端口（共享网络 DNS 解析）
        venv 模式：127.0.0.1:host_port
        """
        if self._docker and self._container_name:
            from .docker import AGENT_INTERNAL_PORT
            return f"http://{self._container_name}:{AGENT_INTERNAL_PORT}"
        return f"http://127.0.0.1:{self.port}"

    def invoke(self, payload: dict, timeout: float = 60.0, config: dict | None = None) -> dict:
        if not self._is_running():
            raise RuntimeError(f"agent {self.name} not running (status={self.status})")
        self.last_invoke_at = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(
                    f"{self._invoke_base_url()}/invoke",
                    json={"input": payload, "config": config or {}},
                )
                r.raise_for_status()
                return r.json()
        except Exception as e:
            if not self.is_alive():
                self.status = "crashed"
                self.last_error = f"process/container died: {e}"
            raise

    async def invoke_stream(self, payload: dict, config: dict | None = None, timeout: float = 120.0):
        """异步流式调用 agent 子进程，yield SSE 文本行。

        用 httpx.AsyncClient 避免线程→asyncio.Queue 桥接（线程内 put_nowait
        不会唤醒事件循环，导致 token 全部缓冲到结束才一次性返回）。
        保留空行（SSE 事件分隔符），调用方原样转发。
        """
        if not self._is_running():
            raise RuntimeError(f"agent {self.name} not running (status={self.status})")
        self.last_invoke_at = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._invoke_base_url()}/invoke/stream",
                    json={"input": payload, "config": config or {}},
                ) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
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

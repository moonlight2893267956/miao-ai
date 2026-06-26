"""
Docker 容器运行时封装。

通过 subprocess 调用 docker CLI（避免 Python SDK 依赖）。
"""
import logging
import os
import shutil
import subprocess
from pathlib import Path

from .process import find_free_port, wait_for_health

log = logging.getLogger(__name__)

DOCKERFILE_TEMPLATE = Path(__file__).parents[2] / "agent_templates" / "Dockerfile.template"

# 需要排除的文件/目录（不进入 Docker 镜像）
_DOCKERIGNORE_LINES = """
.venv/
*.zip
*.log
.build_hash
Dockerfile
.dockerignore
__pycache__/
""".strip()


def docker_available() -> bool:
    """检查 Docker CLI 是否可用且 daemon 可达。"""
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def image_exists(image_tag: str) -> bool:
    """检查 Docker 镜像是否已存在本地。"""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image_tag],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def build_dockerfile(agent_dir: Path, runner_path: Path, env_vars: dict | None = None) -> Path:
    """基于模板生成 Dockerfile 到 agent_dir，并生成 .dockerignore。

    env_vars 不再写入镜像（安全），改为 docker run 时 -e 传入。
    """
    # 生成 .dockerignore（排除垃圾文件）
    dockerignore_path = agent_dir / ".dockerignore"
    dockerignore_path.write_text(_DOCKERIGNORE_LINES + "\n")

    # 复制 Dockerfile 模板（不再做 env 替换）
    template = DOCKERFILE_TEMPLATE.read_text()
    dockerfile_path = agent_dir / "Dockerfile"
    dockerfile_path.write_text(template)

    # 把 runner 复制到 agent_dir 供 Docker COPY
    runner_dest = agent_dir / "miao_runner.py"
    if not runner_dest.exists() or runner_dest.read_text() != runner_path.read_text():
        shutil.copy2(runner_path, runner_dest)
    return dockerfile_path


class DockerBuilder:
    """构建 agent Docker 镜像。"""

    def __init__(self, agent_dir: Path, image_tag: str):
        self.agent_dir = agent_dir
        self.image_tag = image_tag

    def build(self, no_cache: bool = True) -> None:
        """docker build -t <tag> <agent_dir>。

        默认 no_cache=True，确保每次激活都基于最新代码构建。
        """
        cmd = ["docker", "build"]
        if no_cache:
            cmd.append("--no-cache")
        cmd.extend(["-t", self.image_tag, str(self.agent_dir)])

        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker build failed: {result.stderr.strip()}")
        log.info("docker.image.built tag=%s", self.image_tag)


# Agent 容器内部监听的端口（miao_runner.py）
AGENT_INTERNAL_PORT = 8080


class DockerRunner:
    """管理单个 agent 容器的生命周期。

    支持两种网络模式：
    1. host_port + 宿主机端口映射：使用默认 bridge 网络，wait_for_health 用 127.0.0.1:host_port
       （仅在 backend 跑在宿主机时有效）
    2. shared_network + 容器名：agent 容器加入共享网络，wait_for_health 用 container_name:8080
       （推荐，backend 在容器内时使用）
    """

    def __init__(
        self,
        image_tag: str,
        container_name: str,
        cpu: str = "1.0",
        memory: str = "512m",
        env_vars: dict | None = None,
        host_port: int | None = None,
        shared_network: str | None = None,
    ):
        self.image_tag = image_tag
        self.container_name = container_name
        self.cpu = cpu
        self.memory = memory
        self.env_vars = env_vars or {}
        self.host_port = host_port  # 宿主机端口（仅当用端口映射模式时）
        self.shared_network = shared_network  # 共享网络名（推荐用这个）
        # 计算 health check URL
        if shared_network:
            # 通过容器名 + 容器内部端口，DNS 解析
            self.health_url = f"http://{container_name}:{AGENT_INTERNAL_PORT}/health"
        elif host_port is not None:
            # 通过宿主端口（仅当 backend 在宿主机上时有效）
            self.health_url = f"http://127.0.0.1:{host_port}/health"
        else:
            self.health_url = None

    def start(self) -> None:
        """docker run -d ..."""
        # 先清理同名残留容器，避免名称冲突
        try:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass  # 容器不存在时 docker rm 会报错，忽略

        # 构建 env 参数（secret 通过 -e 传入，不写入镜像）
        env_args: list[str] = []
        for k, v in self.env_vars.items():
            if v:  # 只传入非空值
                env_args.extend(["-e", f"{k}={v}"])

        # 构建网络参数
        network_args: list[str] = []
        if self.shared_network:
            network_args = ["--network", self.shared_network]
        else:
            network_args = ["--network", "bridge"]
            if self.host_port is not None:
                network_args.extend(["-p", f"{self.host_port}:{AGENT_INTERNAL_PORT}"])

        result = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self.container_name,
                "--cpus", self.cpu,
                "--memory", self.memory,
                *network_args,
                *env_args,
                "--restart", "no",
                self.image_tag,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker run failed: {result.stderr.strip()}")
        log.info(
            "docker.container.started name=%s network=%s health_url=%s",
            self.container_name,
            self.shared_network or "bridge",
            self.health_url,
        )

    def stop(self) -> None:
        """docker stop + docker rm。"""
        for cmd in [
            ["docker", "stop", self.container_name],
            ["docker", "rm", self.container_name],
        ]:
            try:
                subprocess.run(cmd, capture_output=True, timeout=10)
            except Exception as e:
                log.warning("docker.stop.cmd_failed cmd=%s error=%s", cmd, e)
        log.info("docker.container.stopped name=%s", self.container_name)

    def is_running(self) -> bool:
        """检查容器是否在运行。"""
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() == "true"
        except Exception:
            return False

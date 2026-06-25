"""
子进程管理 helper。
"""
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx


def find_free_port(start: int = 9101, end: int = 9200) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in {start}-{end}")


def spawn_agent_process(
    venv_python: str,
    runner_path: Path,
    agent_dir: Path,
    entrypoint: str,
    port: int,
    log_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "ab")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [
            venv_python,
            str(runner_path),
            str(agent_dir),
            entrypoint,
            str(port),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,  # 自己的进程组，方便 kill
    )


def kill_process(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def wait_for_health(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

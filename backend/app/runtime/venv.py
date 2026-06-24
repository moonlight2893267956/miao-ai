"""
构建/复用 agent 的 venv（用 uv）。

每个 agent 独立 venv。
第一次启动构建；之后 requirements 不变就跳过。
"""
import hashlib
import shutil
import subprocess
from pathlib import Path


class VenvBuilder:
    def __init__(self, agent_dir: Path):
        self.agent_dir = agent_dir
        self.venv_dir = agent_dir / ".venv"
        self.requirements = agent_dir / "requirements.txt"
        self.build_hash_file = agent_dir / ".build_hash"

    def _hash_requirements(self) -> str:
        if not self.requirements.exists():
            return "no-requirements"
        return hashlib.sha256(self.requirements.read_bytes()).hexdigest()

    def needs_build(self) -> bool:
        current = self._hash_requirements()
        if not self.venv_dir.exists() or not self.build_hash_file.exists():
            return True
        return self.build_hash_file.read_text().strip() != current

    def build(self) -> None:
        """构建或重建 venv。失败时抛出 RuntimeError 包含 stderr。"""
        if self.venv_dir.exists():
            shutil.rmtree(self.venv_dir)
        # 1) uv venv
        try:
            subprocess.run(
                ["uv", "venv", str(self.venv_dir)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"uv venv failed: {e.stderr.strip() if e.stderr else str(e)}"
            ) from e
        # 2) 装用户 requirements
        if self.requirements.exists():
            try:
                subprocess.run(
                    [
                        "uv", "pip", "install",
                        "-p", str(self.venv_dir / "bin" / "python"),
                        "-r", str(self.requirements),
                    ],
                    check=True, capture_output=True, text=True,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"uv pip install (requirements) failed: {e.stderr.strip() if e.stderr else str(e)}"
                ) from e
        # 3) 装 miao_runner 需要的依赖（fastapi + uvicorn + pydantic + langfuse）
        try:
            subprocess.run(
                [
                    "uv", "pip", "install",
                    "-p", str(self.venv_dir / "bin" / "python"),
                    "fastapi", "uvicorn", "pydantic", "langfuse", "socksio",
                ],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"uv pip install (miao_runner deps) failed: {e.stderr.strip() if e.stderr else str(e)}"
            ) from e
        # 4) 记录哈希
        self.build_hash_file.write_text(self._hash_requirements())

    def python(self) -> str:
        return str(self.venv_dir / "bin" / "python")

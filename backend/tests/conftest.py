"""
Pytest fixtures。
"""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# 测试启动时也加载根 .env
_ROOT_ENV = Path(__file__).parents[2] / ".env"
if _ROOT_ENV.exists():
    load_dotenv(_ROOT_ENV)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"

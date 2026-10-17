"""agent 运行时环境变量解析测试（含外部工具服务 key 注入）。

覆盖三条路径都必须带上 ``TAVILY_API_KEY``：

1. agent 绑定了模型（``_env_from_model``）；
2. 回退到全局 DashScope 配置（``_global_fallback_env``）；
3. agent 不存在。

以及**空值不注入**这条纪律 —— 没配 key 时 agent 侧会如实降级，
而不是收到一个空字符串后以为「配了但值为空」。
"""
from __future__ import annotations

import uuid

import pytest

from app.runtime import llm_env
from app.runtime.llm_env import resolve_llm_env

from .conftest import *  # noqa: F403  （沿用仓库既有的 conftest fixtures）

pytestmark = pytest.mark.no_db


@pytest.fixture
def _tavily(monkeypatch):
    """临时给 settings 塞一个 tavily key，退出复原。"""
    original = llm_env.settings.tavily_api_key
    llm_env.settings.tavily_api_key = "tvly-test-key-123"
    yield "tvly-test-key-123"
    llm_env.settings.tavily_api_key = original


def test_tool_service_env_injected_when_configured(_tavily):
    env = llm_env._tool_service_env()
    assert env == {"TAVILY_API_KEY": "tvly-test-key-123"}


def test_tool_service_env_absent_when_not_configured(monkeypatch):
    monkeypatch.setattr(llm_env.settings, "tavily_api_key", "")
    assert llm_env._tool_service_env() == {}


def test_tool_service_env_absent_when_blank(monkeypatch):
    """空白串等同未配置：不注入（否则 agent 会以为配了 key）。"""
    monkeypatch.setattr(llm_env.settings, "tavily_api_key", "   ")
    assert llm_env._tool_service_env() == {}


def test_global_fallback_includes_tavily(_tavily, monkeypatch):
    monkeypatch.setattr(llm_env.settings, "dashscope_api_key", "sk-dash")
    env = llm_env._global_fallback_env()
    assert env["TAVILY_API_KEY"] == "tvly-test-key-123"
    assert env["LLM_API_KEY"] == "sk-dash"      # 原有行为不变


def test_global_fallback_without_tavily_keeps_llm(monkeypatch):
    monkeypatch.setattr(llm_env.settings, "tavily_api_key", "")
    monkeypatch.setattr(llm_env.settings, "dashscope_api_key", "sk-dash")
    env = llm_env._global_fallback_env()
    assert "TAVILY_API_KEY" not in env
    assert env["LLM_API_KEY"] == "sk-dash"


def test_env_from_model_includes_tavily(_tavily):
    """agent 绑了模型时也要带上工具 key —— 与选哪个模型无关。"""

    class _Provider:
        base_url = "https://example.com/v1"
        api_key_encrypted = "encrypted-placeholder"

    class _Model:
        model_id = "some-model"
        max_tokens = 2048
        temperature_default = 0.0
        provider = _Provider()

    from unittest.mock import patch

    with patch.object(llm_env, "decrypt_secret", return_value="sk-model"):
        env = llm_env._env_from_model(_Model())

    assert env["TAVILY_API_KEY"] == "tvly-test-key-123"
    assert env["LLM_API_KEY"] == "sk-model"     # 原有行为不变


@pytest.mark.asyncio
async def test_resolve_llm_env_agent_not_found_includes_tavily(_tavily, monkeypatch):
    """agent 不存在 → 走全局回退，也要带工具 key。"""

    class _FakeSession:
        async def get(self, *a, **k):
            return None

    env = await resolve_llm_env(uuid.uuid4(), _FakeSession())  # type: ignore[arg-type]
    assert env.get("TAVILY_API_KEY") == "tvly-test-key-123"


def test_resolve_does_not_raise_when_tavily_unset(monkeypatch):
    """未配置时全链路不得抛异常（密钥缺失不该影响 agent 启动）。"""
    monkeypatch.setattr(llm_env.settings, "tavily_api_key", "")
    env = llm_env._global_fallback_env()
    assert "TAVILY_API_KEY" not in env

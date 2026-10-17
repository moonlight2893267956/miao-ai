"""
应用配置。

启动时自动加载根 .env（如果存在），方便 backend 和 demo 共享一份凭证。
"""
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 加载根目录的 .env（如果存在），后端和 demo 共用凭证
# 加载顺序：
#   1. 先加载根 .env（生产凭证，docker-compose.prod.yml 用的同一份）
#   2. 再加载 .env.local（本地开发凭证，gitignore）→ override=True 覆盖生产值
# 优先级：os.environ > .env.local > 根 .env
_ROOT_ENV = Path(__file__).parents[2] / ".env"
_LOCAL_ENV = Path(__file__).parents[2] / ".env.local"
if _ROOT_ENV.exists():
    load_dotenv(_ROOT_ENV)
if _LOCAL_ENV.exists():
    load_dotenv(_LOCAL_ENV, override=True)


class Settings(BaseSettings):
    # pydantic-settings v2 多 env_file 时按 list 顺序加载，**后者覆盖前者**。
    # 官方文档："The files will be loaded in order, with each file overriding the previous one."
    # 所以 .env.local 放最后一个，优先级最高。
    _env_files = (
        [str(_ROOT_ENV), str(_LOCAL_ENV)]
        if _LOCAL_ENV.exists() and _ROOT_ENV.exists()
        else ([str(_LOCAL_ENV)] if _LOCAL_ENV.exists() else (str(_ROOT_ENV) if _ROOT_ENV.exists() else None))
    )
    model_config = SettingsConfigDict(
        env_file=_env_files,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== App =====
    app_name: str = "Miao AI"
    debug: bool = False
    log_level: str = "INFO"

    # ===== Database（MySQL） =====
    database_url: str

    # ===== Langfuse（trace） =====
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_base_url: str

    # ===== 腾讯云 COS =====
    tencent_secret_id: str
    tencent_secret_key: str
    tencent_region: str
    tencent_bucket: str
    cos_endpoint: str

    # ===== DashScope（LLM provider） =====
    dashscope_api_key: str
    dashscope_base_url: str
    dashscope_model: str = "qwen-plus"
    encryption_key: str = ""

    # ===== 外部工具服务（注入给 agent 运行时） =====
    # 联网检索 key。**全局级**：配一次，所有 agent 运行时都能读到
    # `TAVILY_API_KEY`。留空则不注入，agent 侧会如实降级为「跳过联网」
    # （wubi-explainer 的 facts.py 会发一条 log 说明）。
    # 若将来不同 agent 需要不同 key，再改成 agent 级配置（DB 字段 + UI）。
    tavily_api_key: str = ""

    # ===== Phase 2: Agent 运行时健壮性 =====
    agent_max_restarts: int = 5
    agent_restart_base_delay: float = 2.0
    agent_idle_timeout: int = 300
    agent_max_concurrent: int = 10
    agent_health_check_interval: int = 30
    agent_watchdog_interval: int = 15
    # 限流：per-agent QPS（令牌桶）
    agent_rate_limit_qps: float = 10.0
    agent_rate_limit_burst: int = 20

    # ===== Invoke Timeout =====
    invoke_sync_timeout: float = 180.0
    invoke_stream_timeout: float = 120.0

    # ===== Async Invoke / Webhook =====
    invoke_async_max_workers: int = 4
    invoke_async_default_timeout: float = 300.0
    webhook_max_retries: int = 3
    webhook_retry_base_delay: float = 1.0

    # ===== Conversation（多轮会话，需求文档 §7） =====
    # 单会话最大轮次：超出后 invoke 返回 429
    conversation_max_turns: int = 50
    # 未声明 conversation.max_history_turns 时，注入历史的默认轮数上限
    conversation_default_max_history_turns: int = 10
    # 注入历史的字节上限（两级裁剪的第二级）
    conversation_max_history_bytes: int = 32 * 1024
    # 新建会话未指定 ttl_seconds 时的默认有效期
    conversation_default_ttl_seconds: int = 3600
    # ttl_seconds 上限（24 小时）
    conversation_max_ttl_seconds: int = 86400
    webhook_callback_timeout: float = 10.0

    # ===== Docker Runtime =====
    agent_runtime_mode: str = "venv"
    agent_docker_cpu_limit: str = "1.0"
    agent_docker_memory_limit: str = "512m"
    agent_docker_network: str = "bridge"
    agent_docker_image_prefix: str = "miao-agent"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()

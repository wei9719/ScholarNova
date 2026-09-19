"""
应用配置管理

使用 pydantic-settings 管理配置，支持从环境变量和 .env 文件加载
"""

from typing import Any, List, Optional
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""

    # =============================================================================
    # 应用配置
    # =============================================================================
    APP_NAME: str = "ScholarNova"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    RUNTIME_DIR: Optional[str] = None

    # =============================================================================
    # 数据库配置
    # =============================================================================
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "scholar"
    POSTGRES_PASSWORD: str = "scholar_password"
    POSTGRES_DB: str = "scholar_agent"
    DATABASE_URL: Optional[str] = None

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str], info) -> str:
        """组装数据库连接字符串"""
        if isinstance(v, str) and v:
            return v
        data = info.data
        return (
            f"postgresql+asyncpg://{quote_plus(str(data.get('POSTGRES_USER', 'scholar')))}"
            f":{quote_plus(str(data.get('POSTGRES_PASSWORD', 'scholar_password')))}"
            f"@{data.get('POSTGRES_HOST', 'localhost')}"
            f":{data.get('POSTGRES_PORT', 5432)}"
            f"/{data.get('POSTGRES_DB', 'scholar_agent')}"
        )

    # =============================================================================
    # Redis 配置
    # =============================================================================
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_URL: Optional[str] = None

    @field_validator("REDIS_URL", mode="before")
    @classmethod
    def assemble_redis_connection(cls, v: Optional[str], info) -> Optional[str]:
        """组装 Redis 连接字符串"""
        if v is not None and isinstance(v, str) and v.strip():
            return v
        # 不自动生成 Redis URL，让 cache.py 自行处理
        return None

    # =============================================================================
    # LLM 模型配置
    # =============================================================================
    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_DEFAULT_MODEL: str = "gpt-4o"

    # Anthropic
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_DEFAULT_MODEL: str = "claude-3-5-sonnet-20241022"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_DEFAULT_MODEL: str = "qwen2.5:14b"

    # SenseNova
    SENSENOVA_API_KEY: str = ""
    SENSENOVA_API_BASE: str = "https://token.sensenova.cn/v1"
    SENSENOVA_DEFAULT_MODEL: str = "sensenova-u1-fast"

    # 默认 LLM 提供商
    DEFAULT_LLM_PROVIDER: str = "openai"

    # =============================================================================
    # 学术数据源 API Keys
    # =============================================================================
    SEMANTIC_SCHOLAR_API_KEY: Optional[str] = None
    OPENALEX_EMAIL: Optional[str] = None
    OPENALEX_API_KEY: Optional[str] = None
    CROSSREF_EMAIL: Optional[str] = None
    HF_ACCESS_TOKEN: Optional[str] = None
    HF_TOKEN: Optional[str] = None

    # =============================================================================
    # CORS 配置
    # =============================================================================
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # =============================================================================
    # 日志配置
    # =============================================================================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # =============================================================================
    # 缓存配置
    # =============================================================================
    CACHE_TTL: int = 3600  # 默认缓存 1 小时
    CACHE_PREFIX: str = "scholar:"

    # =============================================================================
    # 数据源配置
    # =============================================================================
    DATA_SOURCE_TIMEOUT: int = 10  # 单个数据源请求超时（秒）
    DATA_SOURCE_MAX_RESULTS: int = 50  # 单个数据源默认最大结果数

    # =============================================================================
    # 搜索配置
    # =============================================================================
    MAX_SEARCH_RESULTS: int = 500
    DEFAULT_SEARCH_RESULTS: int = 50
    SEARCH_TIMEOUT: int = 120  # 搜索超时（秒）

    # =============================================================================
    # LLM 配置
    # =============================================================================
    LLM_TIMEOUT: int = 60  # LLM 调用超时（秒）
    LLM_MAX_RETRIES: int = 2
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 4096

    # =============================================================================
    # 安全配置
    # =============================================================================
    ALLOWED_HOSTS: List[str] = ["localhost", "127.0.0.1"]
    RATE_LIMIT_SEARCH_PER_MINUTE: int = 30  # 每 IP 每分钟搜索请求数
    RATE_LIMIT_ANALYSIS_PER_MINUTE: int = 10  # 每 IP 每分钟分析请求数
    RATE_LIMIT_AGENT_PER_MINUTE: int = 20  # 每 IP 每分钟科研问答请求数
    MAX_REQUEST_SIZE: int = 10 * 1024 * 1024  # 最大请求体 10MB
    MAX_QUERY_LENGTH: int = 2000  # 查询字符串最大长度
    MAX_LIST_ITEMS: int = 100  # 列表字段最大元素数

    # SSRF 防护
    ALLOW_PRIVATE_IPS: bool = False  # 是否允许访问内网 IP
    ALLOW_HTTP: bool = False  # 是否允许 HTTP（非 HTTPS）

    # LLM 响应限制
    LLM_MAX_RESPONSE_SIZE: int = 10 * 1024 * 1024  # LLM 响应最大 10MB
    LLM_REQUEST_TIMEOUT: int = 30  # LLM 请求超时 30 秒

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        # Docker's shared .env also contains frontend/Compose-only variables.
        "extra": "ignore",
    }


# 全局配置实例
settings = Settings()


def runtime_path(filename: str) -> Path:
    """Return a writable persistent path for local or container runtime state."""
    root = (
        Path(settings.RUNTIME_DIR).expanduser()
        if settings.RUNTIME_DIR
        else Path(__file__).resolve().parent.parent
    )
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


# 多模型配置（按任务类型分配模型）
# 未配置的任务回退到全局默认（由 load_saved_model_config 设置）
MODEL_PROFILES = {
    "analysis": {"provider": None, "model": None, "api_key": None, "base_url": None},
    "query_planning": {"provider": None, "model": None, "api_key": None, "base_url": None},
    "translation": {"provider": None, "model": None, "api_key": None, "base_url": None},
    "vision": {"provider": None, "model": None, "api_key": None, "base_url": None},
    "recommendation": {"provider": None, "model": None, "api_key": None, "base_url": None},
    "assistant": {"provider": None, "model": None, "api_key": None, "base_url": None},
    "diagram": {
        "provider": "sensenova",
        "model": "sensenova-u1-fast",
        "api_key": "ENV",
        "base_url": "https://token.sensenova.cn/v1",
    },
}

# 默认配置（所有任务用同一个模型）
DEFAULT_MODEL_CONFIG = {
    "provider": "openai",
    "api_key": None,
    "base_url": None,
    "model_name": None,
    "temperature": 0.7,
    "max_tokens": 4096,
}

PROVIDER_DEFAULTS = {
    "local": ("http://127.0.0.1:8766/v1", "Qwen2.5-1.5B-Instruct"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com", "claude-3-5-sonnet-20241022"),
    "ollama": ("http://localhost:11434", "qwen2.5:14b"),
    "mimo": ("https://token-plan-cn.xiaomimimo.com/v1", "mimo-v2.5-pro"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-5.2"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "siliconflow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen3-8B"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-32k"),
    "sensenova": ("https://token.sensenova.cn/v1", "sensenova-6.7-flash-lite"),
}


def load_saved_model_config():
    """从本地文件加载保存的多模型配置"""
    import json

    config_path = runtime_path("model_config.json")
    try:
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                config = json.load(f)

            # 兼容旧格式（单模型）
            if "tasks" not in config:
                # 旧格式：所有任务用同一个模型
                api_key = config.get("api_key")
                base_url = config.get("base_url")
                model_name = config.get("model_name")
                provider = config.get("provider", "openai")

                for task in MODEL_PROFILES:
                    MODEL_PROFILES[task] = {
                        "provider": provider,
                        "model": model_name,
                        "api_key": api_key,
                        "base_url": base_url,
                    }

                # 更新全局设置
                if api_key:
                    settings.OPENAI_API_KEY = api_key
                settings.DEFAULT_LLM_PROVIDER = provider
                if base_url:
                    settings.OPENAI_API_BASE = base_url
                if model_name:
                    settings.OPENAI_DEFAULT_MODEL = model_name
            else:
                # 新格式：任务级空字段继承主配置。不同提供商的任务
                # 不继承主提供商的 API Key，避免把 MiMo Key 传给 SenseNova 等服务。
                default_provider = config.get("provider", "openai")
                default_model = config.get("model_name")
                default_api_key = config.get("api_key")
                default_base_url = config.get("base_url")
                task_configs = config.get("tasks") or {}
                for task_name, existing in MODEL_PROFILES.items():
                    # 任务完全未在 tasks 中显式配置时，保留其默认 provider 与
                    # 凭据（如 diagram 默认走 sensenova），避免被主配置覆盖。
                    if task_name not in task_configs or not task_configs[task_name]:
                        continue
                    task_config = task_configs[task_name]
                    task_provider = task_config.get("provider") or default_provider
                    same_provider = task_provider == default_provider
                    MODEL_PROFILES[task_name] = {
                        "provider": task_provider,
                        "model": (
                            task_config.get("model")
                            or task_config.get("model_name")
                            or (default_model if same_provider else existing.get("model"))
                        ),
                        "api_key": task_config.get("api_key")
                        or (default_api_key if same_provider else (
                            existing.get("api_key")
                            if existing.get("provider") == task_provider
                            and existing.get("api_key") != "ENV"
                            else None
                        )),
                        "base_url": task_config.get("base_url")
                        or (default_base_url if same_provider else existing.get("base_url")),
                    }

                if default_api_key:
                    settings.OPENAI_API_KEY = default_api_key
                settings.DEFAULT_LLM_PROVIDER = default_provider
                if default_base_url:
                    settings.OPENAI_API_BASE = default_base_url
                if default_model:
                    settings.OPENAI_DEFAULT_MODEL = default_model

            return config
    except Exception:
        pass
    return None


def get_model_for_task(task: str) -> dict:
    """获取指定任务的模型配置，回退到默认配置"""
    profile = MODEL_PROFILES.get(task, {})
    provider = profile.get("provider") or settings.DEFAULT_LLM_PROVIDER or "openai"
    same_as_default = provider == settings.DEFAULT_LLM_PROVIDER
    provider_base_url, provider_model = PROVIDER_DEFAULTS.get(
        provider,
        (None, None),
    )

    # 处理 API Key：如果值是 "ENV" 或空，从 settings 读取
    api_key = profile.get("api_key")
    if not api_key or api_key == "ENV":
        if provider == "local":
            api_key = None  # A local service never inherits a cloud credential.
        elif provider == "sensenova":
            api_key = settings.SENSENOVA_API_KEY
        elif same_as_default:
            api_key = settings.OPENAI_API_KEY
        else:
            api_key = None

    base_url = profile.get("base_url")
    if not base_url:
        base_url = settings.OPENAI_API_BASE if same_as_default else provider_base_url

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": profile.get("model")
        or (settings.OPENAI_DEFAULT_MODEL if same_as_default else provider_model),
    }


def get_fallback_model_config() -> dict:
    """Return the explicitly enabled text-model fallback without key inheritance."""
    import json

    defaults = {
        "enabled": False,
        "provider": "qwen",
        "model": "qwen-plus",
        "api_key": None,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    default_urls = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://localhost:11434",
        "mimo": "https://token-plan-cn.xiaomimimo.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "moonshot": "https://api.moonshot.cn/v1",
        "sensenova": "https://token.sensenova.cn/v1",
    }
    config_path = runtime_path("model_config.json")
    try:
        if not config_path.exists():
            return defaults
        with config_path.open("r", encoding="utf-8") as file:
            saved = json.load(file)
        fallback = saved.get("fallback")
        if not isinstance(fallback, dict):
            return defaults
        provider = str(fallback.get("provider") or defaults["provider"])
        return {
            "enabled": bool(fallback.get("enabled")),
            "provider": provider,
            "model": fallback.get("model_name") or defaults["model"],
            "api_key": fallback.get("api_key"),
            "base_url": fallback.get("base_url") or default_urls.get(provider),
        }
    except (OSError, TypeError, ValueError):
        return defaults


def get_embedding_config() -> dict:
    """Return the explicitly configured semantic-retrieval profile.

    Embeddings intentionally do not inherit the default chat profile. Many chat
    providers do not expose an embeddings endpoint, and silently reusing those
    credentials would turn an optional retrieval enhancement into a failure path.
    """
    import json

    defaults = {
        "enabled": False,
        "provider": "ollama",
        "model": "nomic-embed-text",
        "api_key": None,
        "base_url": "http://localhost:11434",
    }
    default_urls = {
        "ollama": "http://localhost:11434",
        "openai": "https://api.openai.com/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
    }
    config_path = runtime_path("model_config.json")
    try:
        if not config_path.exists():
            return defaults
        with config_path.open("r", encoding="utf-8") as file:
            saved = json.load(file)
        embedding = saved.get("embedding")
        if not isinstance(embedding, dict):
            return defaults
        provider = str(embedding.get("provider") or defaults["provider"])
        return {
            "enabled": bool(embedding.get("enabled")),
            "provider": provider,
            "model": embedding.get("model_name") or defaults["model"],
            "api_key": embedding.get("api_key"),
            "base_url": embedding.get("base_url") or default_urls.get(provider),
        }
    except (OSError, TypeError, ValueError):
        return defaults


# 启动时加载保存的配置
load_saved_model_config()

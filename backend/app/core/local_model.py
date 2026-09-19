"""Narrow, explicit policy for ScholarNova's optional local text service."""

from urllib.parse import urlsplit

LOCAL_MODEL_URL = "http://127.0.0.1:8766/v1"
LOCAL_MODEL_NAME = "Qwen2.5-1.5B-Instruct"
LOCAL_MAX_TOKENS = 256
LOCAL_TIMEOUT_SECONDS = 75.0


def validate_local_model_url(url: str) -> tuple[bool, str | None]:
    """No DNS, proxies, credentials, redirects, LAN addresses or other URL paths."""
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
            and parsed.port is not None and 1024 <= parsed.port <= 65535
            and parsed.path.rstrip("/") == "/v1"
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
        )
    except (ValueError, TypeError):
        valid = False
    return (True, None) if valid else (False, "本机文字模型仅允许 http://127.0.0.1:端口/v1 或 IPv6 回环地址，不允许外网或局域网地址")


def validate_model_endpoint(provider: str, url: str | None) -> tuple[bool, str | None]:
    if provider == "local":
        return validate_local_model_url(url or LOCAL_MODEL_URL)
    from app.core.ssrf import validate_base_url
    return validate_base_url(url)

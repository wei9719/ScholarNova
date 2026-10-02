"""Rate-limit buckets must not interfere across user workflows."""

from types import SimpleNamespace

from app.config import settings
from app.core import rate_limiter


def test_forwarded_headers_cannot_bypass_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_SEARCH_PER_MINUTE", 1)
    monkeypatch.setattr(rate_limiter, "_rate_limiter", rate_limiter.RateLimiter())
    request = SimpleNamespace(
        headers={"X-Forwarded-For": "198.51.100.1", "X-Real-IP": "198.51.100.2"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert rate_limiter.get_client_ip(request) == "127.0.0.1"
    assert rate_limiter.check_rate_limit(request) is None
    request.headers["X-Forwarded-For"] = "203.0.113.1"
    assert rate_limiter.check_rate_limit(request).status_code == 429


def test_missing_asgi_peer_does_not_trust_forwarded_header() -> None:
    request = SimpleNamespace(headers={"X-Forwarded-For": "198.51.100.1"}, client=None)
    assert rate_limiter.get_client_ip(request) == "unknown"


def test_endpoint_types_have_independent_rate_limit_buckets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_SEARCH_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_ANALYSIS_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_AGENT_PER_MINUTE", 1)
    monkeypatch.setattr(rate_limiter, "_rate_limiter", rate_limiter.RateLimiter())
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))

    assert rate_limiter.check_rate_limit(request, "search") is None
    assert rate_limiter.check_rate_limit(request, "analysis") is None
    assert rate_limiter.check_rate_limit(request, "agent") is None

    assert rate_limiter.check_rate_limit(request, "search").status_code == 429
    assert rate_limiter.check_rate_limit(request, "analysis").status_code == 429
    assert rate_limiter.check_rate_limit(request, "agent").status_code == 429

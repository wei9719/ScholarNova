"""
内存速率限制器

基于滑动窗口算法，使用内存存储实现简单的速率限制。
适用于 MVP 阶段，生产环境建议替换为 Redis 实现。
"""

import time
from collections import defaultdict
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings


class RateLimiter:
    """
    滑动窗口速率限制器

    使用内存字典存储每个 IP 的请求时间戳，
    通过滑动窗口算法判断是否超过限制。
    """

    def __init__(self):
        # {ip_address: [timestamp1, timestamp2, ...]}
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup: float = time.time()
        self._cleanup_interval: float = 60.0  # 每 60 秒清理一次过期记录

    def _cleanup_expired(self, window_seconds: int) -> None:
        """清理过期的请求记录"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        cutoff = now - window_seconds
        expired_ips = []
        for ip, timestamps in self._requests.items():
            # 过滤掉过期的时间戳
            self._requests[ip] = [t for t in timestamps if t > cutoff]
            if not self._requests[ip]:
                expired_ips.append(ip)

        for ip in expired_ips:
            del self._requests[ip]

        self._last_cleanup = now

    def is_allowed(
        self,
        client_ip: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> tuple[bool, Optional[int]]:
        """
        检查请求是否允许

        Args:
            client_ip: 客户端 IP 地址
            max_requests: 窗口内最大请求数
            window_seconds: 时间窗口（秒）

        Returns:
            (is_allowed, retry_after_seconds) 元组
            如果被限制，retry_after_seconds 表示需要等待的秒数
        """
        now = time.time()
        cutoff = now - window_seconds

        # 清理当前 IP 的过期记录
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > cutoff
        ]

        # 检查是否超过限制
        if len(self._requests[client_ip]) >= max_requests:
            # 计算需要等待的时间：最早的请求何时过期
            oldest = self._requests[client_ip][0]
            retry_after = int(oldest + window_seconds - now) + 1
            return False, max(retry_after, 1)

        # 记录当前请求
        self._requests[client_ip].append(now)

        # 定期清理
        self._cleanup_expired(window_seconds)

        return True, None


# 全局速率限制器实例
_rate_limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    """Use the ASGI peer, not spoofable request headers.

    Deployments behind a proxy must configure trusted proxy addresses at the
    ASGI server. That server, not this application, resolves forwarded headers.
    """
    if request.client:
        return request.client.host

    return "unknown"


def check_rate_limit(
    request: Request,
    endpoint_type: str = "search",
) -> Optional[JSONResponse]:
    """
    检查请求速率限制

    Args:
        request: FastAPI 请求对象
        endpoint_type: 端点类型（search、analysis 或 agent）

    Returns:
        如果被限制返回 429 JSONResponse，否则返回 None
    """
    client_ip = get_client_ip(request)

    # 根据端点类型选择限制
    if endpoint_type == "analysis":
        max_requests = settings.RATE_LIMIT_ANALYSIS_PER_MINUTE
    elif endpoint_type == "agent":
        max_requests = settings.RATE_LIMIT_AGENT_PER_MINUTE
    else:
        max_requests = settings.RATE_LIMIT_SEARCH_PER_MINUTE

    is_allowed, retry_after = _rate_limiter.is_allowed(
        client_ip=f"{endpoint_type}:{client_ip}",
        max_requests=max_requests,
        window_seconds=60,
    )

    if not is_allowed:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "请求过于频繁，请稍后再试",
                "code": "RATE_LIMIT_ERROR",
                "retry_after": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(max_requests),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(time.time()) + retry_after),
            },
        )

    return None

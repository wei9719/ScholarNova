"""
数据源适配器基类

提供统一接口、HTTP 客户端管理、限流重试（指数退避）等公共能力。
"""

import asyncio
import email.utils
import logging
import random
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from app.schemas.paper import Paper
from app.core.tls import load_ssl_context

logger = logging.getLogger(__name__)

# 用于从 source+paper_id 确定性生成 UUID 的命名空间
_PAPER_UUID_NS = uuid.UUID("a3f8b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c")


def make_paper_uuid(source: str, paper_id: str) -> str:
    """根据来源名和原始 paper_id 生成确定性 UUID 字符串"""
    return str(uuid.uuid5(_PAPER_UUID_NS, f"{source}:{paper_id}"))


class BaseSource(ABC):
    """
    数据源适配器基类

    所有数据源适配器必须继承此类并实现抽象方法。

    提供:
    - 统一的 httpx.AsyncClient 生命周期管理
    - 429 限流自动重试（指数退避 + 抖动）
    - 可配置的最大重试次数和基础退避时间
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ):
        """
        初始化数据源

        Args:
            api_key: API Key
            base_url: API 基础 URL
            timeout: 请求超时时间（秒）
            max_retries: 429 限流最大重试次数
            base_delay: 指数退避基础延迟（秒）
        """
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()
        self._client_generation = 0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # 抽象属性
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称，如 'semantic_scholar'"""
        pass

    @property
    @abstractmethod
    def base_api_url(self) -> str:
        """API 基础 URL"""
        pass

    # ------------------------------------------------------------------
    # HTTP 客户端
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """One client per source; trust-store I/O must not delay async deadlines."""
        generation = self._client_generation
        async with asyncio.timeout(self.timeout), self._client_lock:
            if generation != self._client_generation:
                raise RuntimeError("Source closed during connection initialization")
            if self._client is None or self._client.is_closed:
                # Native certificate loading may outlive a cancelled waiter, but
                # no HTTP client is constructed by the worker or after cancellation.
                context = await load_ssl_context()
                if generation != self._client_generation:
                    raise RuntimeError("Source closed during connection initialization")
                self._client = httpx.AsyncClient(
                    base_url=self.base_url or self.base_api_url,
                    timeout=self.timeout,
                    headers=self._get_headers(),
                    follow_redirects=True,
                    verify=context,
                )
            return self._client

    def _get_headers(self) -> dict:
        """获取默认请求头"""
        headers = {
            "User-Agent": "ScholarNova/1.0 (https://github.com/scholar-nova)",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ------------------------------------------------------------------
    # 限流重试
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """
        发送 HTTP 请求，遇到 429 或短暂 5xx 时自动指数退避重试。

        Args:
            method: HTTP 方法（'GET', 'POST' 等）
            url: 请求 URL（相对或绝对）
            **kwargs: 传递给 httpx client 的额外参数

        Returns:
            httpx.Response

        Raises:
            httpx.HTTPStatusError: 非 429 的 HTTP 错误
            httpx.RequestError: 网络错误
        """
        self.last_error = None
        try:
            client = await self._get_client()
        except Exception as exc:
            # Adapters may return [] on failure. Preserve an honest source status
            # even when the request failed before reaching the HTTP transport.
            self.last_error = (
                "Secure connection initialization timeout"
                if isinstance(exc, TimeoutError)
                else "Secure connection initialization failed"
            )
            raise
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                await self._before_request()
                response = await client.request(method, url, **kwargs)
                await self._after_response(response)

                if response.status_code == 429 or 500 <= response.status_code < 600:
                    delay = self._retry_delay(response, attempt)
                    if attempt < self.max_retries:
                        logger.warning(
                            f"[{self.name}] HTTP {response.status_code}，"
                            f"第 {attempt + 1}/{self.max_retries} 次重试，"
                            f"等待 {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"[{self.name}] HTTP {response.status_code}，已耗尽重试次数"
                        )
                        response.raise_for_status()

                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as exc:
                self.last_error = f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
                raise
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    delay += random.uniform(0, 0.5 * delay)
                    logger.warning(
                        f"[{self.name}] 请求异常 ({exc.__class__.__name__})，"
                        f"第 {attempt + 1}/{self.max_retries} 次重试，等待 {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    self.last_error = str(exc)
                    raise

        # 理论上不会到这里，但安全起见
        if last_exc:
            self.last_error = str(last_exc)
            raise last_exc
        self.last_error = f"[{self.name}] 请求失败且无异常记录"
        raise RuntimeError(f"[{self.name}] 请求失败且无异常记录")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """解析 Retry-After（秒数或 HTTP 日期），否则使用指数退避。"""
        retry_after = response.headers.get("Retry-After")
        delay: Optional[float] = None
        if retry_after:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = email.utils.parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = max(
                        0.0,
                        (retry_at - datetime.now(timezone.utc)).total_seconds(),
                    )
                except (TypeError, ValueError, OverflowError):
                    delay = None
        if delay is None:
            delay = self.base_delay * (2 ** attempt)
        return delay + random.uniform(0, min(1.0, 0.25 * max(delay, 0.1)))

    async def _before_request(self) -> None:
        """数据源可覆盖此钩子，在每一次真实 HTTP 请求前执行配额控制。"""
        return None

    async def _after_response(self, response: httpx.Response) -> None:
        """数据源可覆盖此钩子，将服务端限流状态同步给其他请求者。"""
        return None

    # ------------------------------------------------------------------
    # 抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    async def search(self, query: str, max_results: int = 50) -> List[Paper]:
        """
        搜索论文

        Args:
            query: 查询字符串
            max_results: 最大结果数

        Returns:
            论文列表
        """
        pass

    @abstractmethod
    async def get_paper(self, paper_id: str) -> Optional[Paper]:
        """
        获取单篇论文详情

        Args:
            paper_id: 论文 ID（各来源平台的原始 ID）

        Returns:
            论文详情，未找到返回 None
        """
        pass

    @abstractmethod
    async def get_pdf_url(self, paper_id: str) -> Optional[str]:
        """
        获取论文 PDF 下载链接

        Args:
            paper_id: 论文 ID

        Returns:
            PDF URL，不可用返回 None
        """
        pass

    @abstractmethod
    async def health_check(self) -> dict:
        """
        数据源健康检查

        Returns:
            包含 status ('ok'/'error') 和可选 message 的字典
        """
        pass

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self):
        """关闭 HTTP 客户端"""
        # Invalidate pending initialization without waiting for native TLS work.
        # A later explicit request can still reopen this reusable source.
        self._client_generation += 1
        client, self._client = self._client, None
        if client and not client.is_closed:
            await client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

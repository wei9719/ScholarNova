"""Bounded shared caching, with Redis preferred and an in-process fallback."""

import asyncio
import json
import logging
import time
import weakref
from collections import OrderedDict
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None


class InMemoryCache:
    """TTL/LRU cache bounded by entries and encoded key/value bytes.

    Operations do not suspend, so updates are atomic within the backend event
    loop. These limits bound payloads; they are not a process-RSS measurement.
    """

    def __init__(self, max_entries: int = 512, max_bytes: int = 32 * 1024 * 1024):
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("Cache limits must be positive")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._store: OrderedDict[str, tuple[str, float, int]] = OrderedDict()
        self._bytes = 0

    def _remove(self, key: str) -> bool:
        item = self._store.pop(key, None)
        if item is None:
            return False
        self._bytes -= item[2]
        return True

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for key, (_, expires, _) in list(self._store.items()):
            if expires <= now:
                self._remove(key)

    async def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires, _ = item
        if expires <= time.monotonic():
            self._remove(key)
            return None
        self._store.move_to_end(key)
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        size = len(key.encode("utf-8")) + len(value.encode("utf-8"))
        if ttl <= 0:
            raise ValueError("Cache TTL must be positive")
        if size > self.max_bytes:
            raise ValueError("Cache value exceeds the memory budget")
        self._purge_expired()
        self._remove(key)
        while self._store and (
            len(self._store) >= self.max_entries or self._bytes + size > self.max_bytes
        ):
            self._remove(next(iter(self._store)))
        self._store[key] = (value, time.monotonic() + ttl, size)
        self._bytes += size

    async def delete(self, *keys: str) -> int:
        self._purge_expired()
        return sum(self._remove(key) for key in keys)

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def scan_iter(self, pattern: str):
        """The application uses prefix scans, not arbitrary glob patterns."""
        self._purge_expired()
        prefix = pattern.rstrip("*")
        for key in list(self._store):
            if key.startswith(prefix):
                yield key

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._store.clear()
        self._bytes = 0


_global_memory_cache: InMemoryCache | None = None


def _memory_cache() -> InMemoryCache:
    global _global_memory_cache
    if _global_memory_cache is None:
        _global_memory_cache = InMemoryCache()
    return _global_memory_cache


class _SharedBackend:
    def __init__(self):
        self.client: Any = None
        self.lock = asyncio.Lock()


# Redis connections and locks must never migrate between asyncio event loops.
# Request-scoped managers borrow this pool; only application shutdown owns it.
_shared_backends: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def _close_redis(client: Any) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is not None:
        try:
            await asyncio.wait_for(close(), timeout=0.25)
        except Exception:
            logger.warning("Cache connection cleanup failed")


class CacheManager:
    """Borrow a shared Redis backend or bounded memory cache.

    A Redis outage switches the shared backend to memory until application
    restart, avoiding reconnect storms. Memory fallback is process-local: it
    must not be advertised as distributed/multi-worker persistence.
    """

    def __init__(self, redis_url: str | None = None, *, operation_timeout: float = 1.0):
        if operation_timeout <= 0:
            raise ValueError("Cache timeout must be positive")
        self.redis_url = (
            str(settings.REDIS_URL) if settings.REDIS_URL else None
        ) if redis_url is None else redis_url or None
        self.operation_timeout = operation_timeout
        self._use_memory = False
        self._client: Any = None
        self._backend: _SharedBackend | None = None

    async def _get_client(self):
        if not self.redis_url or not REDIS_AVAILABLE:
            self._client = _memory_cache()
            self._use_memory = True
            return self._client

        loop = asyncio.get_running_loop()
        pool = _shared_backends.setdefault(loop, {})
        backend = pool.setdefault(self.redis_url, _SharedBackend())
        self._backend = backend
        if backend.client is None:
            async with backend.lock:
                if backend.client is None:
                    candidate = None
                    try:
                        candidate = redis.from_url(
                            self.redis_url,
                            encoding="utf-8",
                            decode_responses=True,
                            socket_connect_timeout=self.operation_timeout,
                            socket_timeout=self.operation_timeout,
                            retry_on_timeout=False,
                            max_connections=16,
                        )
                        await asyncio.wait_for(candidate.ping(), timeout=self.operation_timeout)
                    except asyncio.CancelledError:
                        if candidate is not None:
                            await _close_redis(candidate)
                        raise
                    except Exception as exc:
                        if candidate is not None:
                            await _close_redis(candidate)
                        logger.warning("Redis unavailable (%s); using bounded memory cache", type(exc).__name__)
                        backend.client = _memory_cache()
                    else:
                        backend.client = candidate
        self._client = backend.client
        self._use_memory = isinstance(self._client, InMemoryCache)
        return self._client

    async def _fallback(self, failed_client: Any) -> InMemoryCache:
        backend = self._backend
        if backend is not None:
            async with backend.lock:
                if backend.client is failed_client:
                    backend.client = _memory_cache()
                    await _close_redis(failed_client)
        self._client = _memory_cache()
        self._use_memory = True
        return self._client

    async def _call(self, method: str, *args):
        client = await self._get_client()
        if isinstance(client, InMemoryCache):
            return await getattr(client, method)(*args)
        try:
            return await asyncio.wait_for(
                getattr(client, method)(*args), timeout=self.operation_timeout
            )
        except Exception as exc:
            logger.warning("Redis %s failed (%s); using memory cache", method, type(exc).__name__)
            client = await self._fallback(client)
            return await getattr(client, method)(*args)

    async def get(self, key: str) -> Any | None:
        try:
            value = await self._call("get", f"{settings.CACHE_PREFIX}{key}")
            return json.loads(value) if value is not None else None
        except Exception as exc:
            logger.warning("Cache get failed (%s)", type(exc).__name__)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        try:
            await self._call(
                "setex", f"{settings.CACHE_PREFIX}{key}",
                settings.CACHE_TTL if ttl is None else ttl, json.dumps(value),
            )
            return True
        except Exception as exc:
            logger.warning("Cache set failed (%s)", type(exc).__name__)
            return False

    async def delete(self, key: str) -> bool:
        try:
            await self._call("delete", f"{settings.CACHE_PREFIX}{key}")
            return True
        except Exception as exc:
            logger.warning("Cache delete failed (%s)", type(exc).__name__)
            return False

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._call("exists", f"{settings.CACHE_PREFIX}{key}"))
        except Exception as exc:
            logger.warning("Cache exists failed (%s)", type(exc).__name__)
            return False

    async def clear_prefix(self, prefix: str) -> int:
        async def clear(client) -> int:
            # Bound temporary scan batches even for a large Redis namespace.
            count = 0
            keys = []
            async for key in client.scan_iter(f"{settings.CACHE_PREFIX}{prefix}*"):
                keys.append(key)
                if len(keys) >= 128:
                    count += await client.delete(*keys)
                    keys.clear()
            if keys:
                count += await client.delete(*keys)
            return count

        client = await self._get_client()
        try:
            return await asyncio.wait_for(clear(client), timeout=self.operation_timeout)
        except Exception as exc:
            logger.warning("Cache prefix clear failed (%s)", type(exc).__name__)
            if not isinstance(client, InMemoryCache):
                await self._fallback(client)
            return 0

    async def close(self) -> None:
        """Release this borrow without deleting another request's cache."""
        self._client = None
        self._backend = None

    @classmethod
    async def close_all(cls) -> None:
        """Application lifespan shutdown: close this loop's pools and memory."""
        pool = _shared_backends.pop(asyncio.get_running_loop(), {})
        for backend in pool.values():
            async with backend.lock:
                client, backend.client = backend.client, None
                if client is not None and not isinstance(client, InMemoryCache):
                    await _close_redis(client)
        if _global_memory_cache is not None:
            await _global_memory_cache.close()

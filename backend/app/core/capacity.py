"""Single-process, bounded admission for expensive work, not provider RPM quotas."""

import asyncio

from starlette.responses import JSONResponse


class CapacityExceeded(Exception):
    pass


def busy_response():
    return JSONResponse(
        status_code=429,
        content={
            "code": "SERVICE_BUSY",
            "detail": "当前任务较多，请稍后重试；本次未调用模型。",
            "retry_after": 2,
        },
        headers={"Retry-After": "2"},
    )


class WorkPool:
    """Reserve before allocating a job; a semaphore alone leaves an unbounded queue.

    All methods run on the application's event loop. Separate workers have
    separate budgets; this is deliberately not a distributed quota service.
    """

    def __init__(self, active: int, queued: int, wait_seconds: float):
        if active < 1 or queued < 0 or wait_seconds <= 0:
            raise ValueError("Invalid work capacity")
        self.limit = active
        self.queue_limit = queued
        self.wait_seconds = wait_seconds
        self._semaphore = asyncio.Semaphore(active)
        self.reserved = 0
        self.active = 0
        self.peak_active = 0
        self.rejected = 0
        self.timed_out = 0

    def reserve(self):
        # No await between checking and reserving: atomic on the ASGI event loop.
        if self.reserved >= self.limit + self.queue_limit:
            self.rejected += 1
            raise CapacityExceeded()
        self.reserved += 1
        return WorkLease(self)

    def snapshot(self):
        return {
            "active": self.active,
            # Includes reserved jobs that the event loop has not started yet.
            "queued": self.reserved - self.active,
            "active_limit": self.limit,
            "queue_limit": self.queue_limit,
            "peak_active": self.peak_active,
            "rejected": self.rejected,
            "queue_timeouts": self.timed_out,
        }


class WorkLease:
    def __init__(self, pool):
        self.pool = pool
        self.acquired = False
        self.released = False

    async def __aenter__(self):
        try:
            await asyncio.wait_for(self.pool._semaphore.acquire(), self.pool.wait_seconds)
            self.acquired = True
            self.pool.active += 1
            self.pool.peak_active = max(self.pool.peak_active, self.pool.active)
            return self
        except TimeoutError as exc:
            self.pool.timed_out += 1
            self.release()
            raise CapacityExceeded() from exc
        except BaseException:
            self.release()
            raise

    def release(self):
        if self.released:
            return
        self.released = True
        self.pool.reserved -= 1
        if self.acquired:
            self.pool.active -= 1
            self.pool._semaphore.release()

    async def __aexit__(self, *_):
        self.release()


class ExpensiveRequestMiddleware:
    """Keep read/status/cancel requests responsive while AI requests saturate.

    Admission happens before reading bodies or opening DB sessions. Search POSTs
    reserve their own background-job budget in the endpoint instead.
    """

    def __init__(self, app, pool):
        self.app = app
        self.pool = pool

    @staticmethod
    def applies(scope):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT"}:
            return False
        path = scope.get("path", "").rstrip("/")
        return (
            path == "/api/v1/agent/chat"
            or path == "/api/v1/recommendations"
            or path.startswith("/api/v1/model/")
            or path == "/api/v1/knowledge"
            or path in {"/api/v1/knowledge/ai-analyze", "/api/v1/knowledge/recommend"}
            or (path.startswith("/api/v1/knowledge/routes/") and "/ai-generate" in path)
            or (path.startswith("/api/v1/papers/") and path.endswith(("/analyze", "/fulltext", "/translate")))
        )

    async def __call__(self, scope, receive, send):
        if not self.applies(scope):
            await self.app(scope, receive, send)
            return
        try:
            lease = self.pool.reserve()
            await lease.__aenter__()
        except CapacityExceeded:
            await busy_response()(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            lease.release()

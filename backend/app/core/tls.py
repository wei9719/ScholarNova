"""Bounded HTTPX certificate preparation shared by model and academic sources."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore

import httpx

SSL_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="http-tls")
SSL_SLOTS = BoundedSemaphore(4)  # Two running certificate loads, two queued.


async def load_ssl_context(*, workers=None, slots=None):
    """Keep HTTPX CA/environment defaults, without blocking the event loop.

    Workers create only an SSLContext, never a client. Cancelling a waiter cannot
    stop a running native trust-store load, so its slot lasts until real completion.
    """
    workers = workers if workers is not None else SSL_WORKERS
    slots = slots if slots is not None else SSL_SLOTS
    if not slots.acquire(blocking=False):
        raise RuntimeError("Secure connection initialization is busy; retry shortly")
    try:
        work = workers.submit(httpx.create_ssl_context)
    except BaseException:
        slots.release()
        raise
    work.add_done_callback(lambda _: slots.release())
    pending = asyncio.wrap_future(work)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        work.cancel()  # Only queued, not already running, work can be removed.
        pending.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        raise

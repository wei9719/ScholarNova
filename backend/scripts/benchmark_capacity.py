"""Offline ASGI admission benchmark; never calls models, Redis or search APIs.

Run from backend: python scripts/benchmark_capacity.py --output ../docs/reports/capacity-benchmark-2026-10-02.json
This measures resource bounds, not real-provider throughput or desktop UI FPS.
"""

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.capacity import ExpensiveRequestMiddleware, WorkPool  # noqa: E402


def percentile(values, ratio):
    return round(sorted(values)[max(0, math.ceil(len(values) * ratio) - 1)], 3)


async def measure(*, bounded, stagger=0.0, requests=100):
    app = FastAPI()
    pool = WorkPool(active=4, queued=8, wait_seconds=2)
    if bounded:
        app.add_middleware(ExpensiveRequestMiddleware, pool=pool)
    active = peak = completed = 0

    @app.post("/api/v1/agent/chat")
    async def fake_work():
        nonlocal active, peak, completed
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.1)
            completed += 1
            return {"mock": True}
        finally:
            active -= 1

    @app.get("/api/v1/health/live")
    async def health():
        return {"status": "ok"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://offline") as client:
        async def request(index):
            if stagger:
                await asyncio.sleep(index * stagger)
            started = time.perf_counter()
            response = await client.post("/api/v1/agent/chat", json={"question": "synthetic"})
            if response.status_code == 429:
                assert response.headers.get("retry-after") == "2"
                assert response.json()["code"] == "SERVICE_BUSY"
            return response.status_code, (time.perf_counter() - started) * 1000

        async def probe_health():
            timings = []
            for _ in range(25):
                await asyncio.sleep(0.015)
                started = time.perf_counter()
                response = await client.get("/api/v1/health/live")
                assert response.status_code == 200
                timings.append((time.perf_counter() - started) * 1000)
            return timings

        started = time.perf_counter()
        outcomes, probes = await asyncio.gather(
            asyncio.gather(*(request(i) for i in range(requests))), probe_health(),
        )
        wall_ms = (time.perf_counter() - started) * 1000
    successful = [ms for status, ms in outcomes if status == 200]
    statuses = {str(status): sum(code == status for code, _ in outcomes) for status, _ in outcomes}
    assert active == 0
    assert completed == len(successful)
    assert pool.reserved == pool.active == 0
    if bounded:
        assert peak <= 4
    return {
        "bounded": bounded, "requests": requests, "stagger_ms": stagger * 1000,
        "mock_work_ms": 100, "statuses": statuses, "handler_peak_active": peak,
        "success_latency_p50_ms": percentile(successful, 0.5),
        "success_latency_p95_ms": percentile(successful, 0.95),
        "health_probes": len(probes), "health_p95_ms": percentile(probes, 0.95),
        "wall_ms": round(wall_ms, 3), "capacity_after": pool.snapshot(),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "scope": "Offline, single-process in-memory ASGI; synthetic 100 ms upstream wait. No real API calls.",
        "limitation": "Not an HTTP/network/SQLite/PDF/GPU benchmark and not a multi-user throughput claim.",
        "cases": [
            await measure(bounded=False),
            await measure(bounded=True),
            await measure(bounded=True, stagger=0.03),
        ],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())

"""The liveness endpoint exposes aggregate capacity, never request/provider data."""

import pytest

from app.api.v1 import health


@pytest.mark.asyncio
async def test_live_capacity_schema_contains_only_aggregate_counters(client, monkeypatch):
    def no_upstream(*_args, **_kwargs):
        raise AssertionError("Liveness must not call providers or models")

    monkeypatch.setattr(health, "_check_llm", no_upstream)
    monkeypatch.setattr(health, "_check_redis", no_upstream)
    monkeypatch.setattr(health, "_probe_data_source", no_upstream)
    monkeypatch.setattr(health, "_probe_semantic_scholar", no_upstream)
    response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"status", "version", "timestamp", "services", "capacity"}
    capacity = payload["capacity"]
    assert set(capacity) == {"scope", "search", "ai"}
    assert capacity["scope"] == "process"
    expected = {
        "active", "queued", "active_limit", "queue_limit", "peak_active",
        "rejected", "queue_timeouts",
    }
    for name in ("search", "ai"):
        assert set(capacity[name]) == expected
        assert all(type(value) is int and value >= 0 for value in capacity[name].values())
    # Exact allowlists prevent later accidental API keys, queries, paths,
    # conversation identifiers or endpoint URLs from being added unnoticed.
    assert set(payload["services"]) == {"database"}

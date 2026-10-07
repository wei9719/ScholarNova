import asyncio
import ssl
import time
from unittest.mock import AsyncMock, Mock

import pytest

from app.schemas.paper import PaperQuality
from app.services import journal_quality


def test_import_and_apply_licensed_quartiles(tmp_path, monkeypatch):
    data_path = tmp_path / "journal_rankings.json"
    monkeypatch.setattr(journal_quality, "_DATA_PATH", data_path)
    csv_text = "Journal,JCR Quartile,中科院分区,SJR Best Quartile,Year,Source\nTest Journal,Q1,2区,Q2,2025,licensed test file\n"

    status = journal_quality.import_ranking_content(csv_text, "rankings.csv")
    quality = journal_quality.apply_local_ranking(PaperQuality(), "Test Journal")

    assert status["entry_count"] == 1
    assert quality.jcr_quartile == "Q1"
    assert quality.cas_quartile == "2区"
    assert quality.sjr_quartile == "Q2"
    assert quality.partition_status == "verified_import"
    assert quality.partition_source == "licensed test file"


def test_import_rejects_files_without_quartiles(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_quality, "_DATA_PATH", tmp_path / "rankings.json")
    try:
        journal_quality.import_ranking_content("Journal,Year\nTest Journal,2025\n", "bad.csv")
    except ValueError as exc:
        assert "没有识别到分区记录" in str(exc)
    else:
        raise AssertionError("missing quartiles should be rejected")


@pytest.mark.parametrize("stage", ["semaphore", "tls", "http"])
async def test_optional_metrics_share_one_budget_and_preserve_existing_quality(
    tmp_path, monkeypatch, stage,
):
    monkeypatch.setattr(journal_quality, "_DATA_PATH", tmp_path / "rankings.json")
    monkeypatch.setattr(journal_quality, "_OPENALEX_CACHE", {})
    monkeypatch.setattr(journal_quality, "_OPENALEX_BUDGET_SECONDS", 0.02)
    gate = asyncio.Semaphore(0 if stage == "semaphore" else 1)
    monkeypatch.setattr(journal_quality, "_OPENALEX_SEMAPHORE", gate)
    cancelled = asyncio.Event()

    async def block(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    loader = AsyncMock(side_effect=block if stage == "tls" else None, return_value=context)
    monkeypatch.setattr(journal_quality, "load_ssl_context", loader)
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = block
    factory = Mock(return_value=client)
    monkeypatch.setattr(journal_quality.httpx, "AsyncClient", factory)
    base = PaperQuality(jcr_quartile="Q1", partition_status="verified_import")
    result = await asyncio.wait_for(journal_quality.lookup_journal_quality("Test Journal", base), 0.3)
    assert result == base
    assert not journal_quality._OPENALEX_CACHE  # No failed/partial response is cached.
    if stage == "semaphore":
        loader.assert_not_called()
    else:
        assert cancelled.is_set() and gate._value == 1
    if stage == "http":
        client.__aexit__.assert_awaited_once()
        assert factory.call_args.kwargs["verify"] is context
    else:
        factory.assert_not_called()


async def test_metrics_cache_hit_does_not_wait_for_tls_or_network(monkeypatch):
    cached = {"openalex_h_index": 90, "partition_status": "open_metrics"}
    monkeypatch.setattr(journal_quality, "_OPENALEX_CACHE", {
        "testjournal": (time.monotonic(), cached),
    })
    monkeypatch.setattr(journal_quality, "_OPENALEX_SEMAPHORE", asyncio.Semaphore(0))
    loader, factory = AsyncMock(), Mock()
    monkeypatch.setattr(journal_quality, "load_ssl_context", loader)
    monkeypatch.setattr(journal_quality.httpx, "AsyncClient", factory)
    result = await asyncio.wait_for(journal_quality.lookup_openalex_metrics("Test Journal"), 0.1)
    assert result == cached and result is not cached
    loader.assert_not_called()
    factory.assert_not_called()


async def test_tls_initialization_busy_keeps_quality_without_500(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_quality, "_DATA_PATH", tmp_path / "rankings.json")
    monkeypatch.setattr(journal_quality, "_OPENALEX_CACHE", {})
    gate = asyncio.Semaphore(1)
    monkeypatch.setattr(journal_quality, "_OPENALEX_SEMAPHORE", gate)
    monkeypatch.setattr(journal_quality, "load_ssl_context", AsyncMock(
        side_effect=RuntimeError("Secure connection initialization is busy; retry shortly"),
    ))
    factory = Mock()
    monkeypatch.setattr(journal_quality.httpx, "AsyncClient", factory)
    base = PaperQuality(quality_score=0.6, openalex_h_index=80)
    assert await journal_quality.lookup_journal_quality("Test Journal", base) == base
    assert gate._value == 1
    factory.assert_not_called()

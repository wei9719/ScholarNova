"""Regression tests for optional BM25 + embedding retrieval."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.retrieval import RetrievalEmbedding
from app.services.retrieval.bm25 import rank_chunks
from app.services.retrieval.contracts import RetrievalChunk
from app.services.retrieval.embeddings import EmbeddingBatch
from app.services.retrieval.hybrid import (
    rank_chunks_by_vector,
    rank_chunks_hybrid,
    reciprocal_rank_fusion,
)


def _chunk(chunk_id: str, title: str, content: str) -> RetrievalChunk:
    return RetrievalChunk(
        chunk_id=chunk_id,
        document_id=f"document:{chunk_id}",
        source="knowledge",
        title=title,
        content=content,
    )


@pytest_asyncio.fixture
async def cache_engine(tmp_path):
    # Production-style independent connections. In-memory StaticPool cannot
    # establish isolation or reliably reproduce SQLite writer contention.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'embedding-cache.db'}",
        pool_size=2,
        max_overflow=0,
    )
    async with engine.begin() as connection:
        await connection.run_sync(RetrievalEmbedding.__table__.create)
        await connection.execute(text("CREATE TABLE business_probe (id INTEGER PRIMARY KEY)"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def cache_db(cache_engine):
    async with AsyncSession(cache_engine, expire_on_commit=False) as session:
        yield session


def _configure_embedding(monkeypatch, gateway):
    from app.services.retrieval import hybrid

    monkeypatch.setattr(hybrid, "get_embedding_config", lambda: {
        "enabled": True, "provider": "custom", "model": "test-embedding",
    })
    monkeypatch.setattr(hybrid, "EmbeddingGateway", lambda _config: gateway)


def _privacy_chunks():
    return [_chunk("privacy", "Privacy", "Privacy preserving aggregation")]


def test_golden_set_hybrid_top1_is_not_worse_than_bm25() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "retrieval_golden.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    bm25_hits = 0
    hybrid_hits = 0
    for case in cases:
        chunks = [
            _chunk(item["id"], item["title"], item["content"])
            for item in case["candidates"]
        ]
        vectors = {
            item["id"]: item["vector"]
            for item in case["candidates"]
        }
        lexical = rank_chunks(case["query"], chunks, limit=3, max_per_document=1)
        semantic = rank_chunks_by_vector(
            case["query_vector"], chunks, vectors, limit=3
        )
        fused = reciprocal_rank_fusion(
            [lexical, semantic],
            weights=[1.15, 1.0],
            limit=1,
            max_per_document=1,
        )
        bm25_hits += bool(lexical and lexical[0].chunk.chunk_id == case["relevant"])
        hybrid_hits += bool(fused and fused[0].chunk.chunk_id == case["relevant"])

    assert bm25_hits == 3
    assert hybrid_hits == 4


class FakeEmbeddingGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts) -> EmbeddingBatch:
        self.calls += 1
        vectors = [
            [1.0, 0.0] if "privacy" in text.casefold() else [0.0, 1.0]
            for text in texts
        ]
        return EmbeddingBatch(vectors=vectors, input_tokens=len(texts) * 3)


@pytest.mark.asyncio
async def test_hybrid_embeddings_are_cached_locally(
    cache_db,
    monkeypatch,
) -> None:
    from app.services.retrieval import hybrid

    fake = FakeEmbeddingGateway()
    monkeypatch.setattr(
        hybrid,
        "get_embedding_config",
        lambda: {
            "enabled": True,
            "provider": "custom",
            "model": "test-embedding",
            "api_key": None,
            "base_url": "https://embedding.example/v1",
        },
    )
    monkeypatch.setattr(hybrid, "EmbeddingGateway", lambda _config: fake)
    chunks = [
        _chunk("privacy", "Privacy", "Privacy preserving aggregation"),
        _chunk("traffic", "Traffic", "Road speed forecasting"),
    ]

    first = await rank_chunks_hybrid(cache_db, "privacy safeguards", chunks)
    second = await rank_chunks_hybrid(cache_db, "privacy safeguards", chunks)

    assert first.mode == "hybrid"
    assert first.cache_misses == 3
    assert first.embedding_tokens == 9
    assert second.mode == "hybrid"
    assert second.cache_hits == 3
    assert second.cache_misses == 0
    assert second.embedding_tokens == 0
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_hybrid_failure_falls_back_to_bm25(
    db_session,
    monkeypatch,
) -> None:
    from app.services.retrieval import hybrid

    class BrokenGateway:
        def __init__(self, config) -> None:
            pass

        async def embed(self, _texts):
            raise TimeoutError

    monkeypatch.setattr(
        hybrid,
        "get_embedding_config",
        lambda: {
            "enabled": True,
            "provider": "custom",
            "model": "broken",
            "base_url": "https://embedding.example/v1",
        },
    )
    monkeypatch.setattr(hybrid, "EmbeddingGateway", BrokenGateway)
    chunks = [_chunk("trace", "Traceable retrieval", "citation evidence retrieval")]

    result = await rank_chunks_hybrid(db_session, "citation retrieval", chunks)

    assert result.mode == "bm25"
    assert result.semantic_status == "unavailable"
    assert result.ranked[0].chunk.chunk_id == "trace"
    assert result.embedding_tokens == 0


@pytest.mark.asyncio
async def test_concurrent_cache_misses_do_not_poison_either_user_session(cache_engine, monkeypatch):
    barrier = asyncio.Event()

    class SimultaneousGateway(FakeEmbeddingGateway):
        async def embed(self, texts):
            batch = await super().embed(texts)
            if self.calls == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=2)
            return batch

    gateway = SimultaneousGateway()
    _configure_embedding(monkeypatch, gateway)
    sessions = async_sessionmaker(cache_engine, expire_on_commit=False)
    async with sessions() as first, sessions() as second:
        results = await asyncio.gather(
            rank_chunks_hybrid(first, "privacy safeguards", _privacy_chunks()),
            rank_chunks_hybrid(second, "privacy safeguards", _privacy_chunks()),
        )
        assert all(result.mode == "hybrid" and result.embedding_tokens == 6 for result in results)
        assert first.is_active and second.is_active
        await first.commit()
        await second.commit()
    async with sessions() as check:
        assert await check.scalar(select(func.count()).select_from(RetrievalEmbedding)) == 2
    async with sessions() as third:
        cached = await rank_chunks_hybrid(third, "privacy safeguards", _privacy_chunks())
        assert cached.cache_hits == 2
        assert cached.embedding_tokens == 0
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_caller_write_lock_skips_cache_quickly_without_committing_or_rolling_back(cache_db, cache_engine, monkeypatch):
    gateway = FakeEmbeddingGateway()
    _configure_embedding(monkeypatch, gateway)
    await cache_db.execute(text("INSERT INTO business_probe (id) VALUES (1)"))
    started = time.monotonic()
    result = await rank_chunks_hybrid(cache_db, "privacy safeguards", _privacy_chunks())
    assert time.monotonic() - started < 2
    assert result.mode == "hybrid"
    assert result.embedding_tokens == 6
    assert cache_db.is_active and cache_db.in_transaction()
    assert await cache_db.scalar(text("SELECT count(*) FROM business_probe")) == 1
    async with cache_engine.connect() as observer:
        assert await observer.scalar(text("SELECT count(*) FROM business_probe")) == 0
        assert await observer.scalar(text("PRAGMA busy_timeout")) == 5000
    await cache_db.rollback()
    assert await cache_db.scalar(text("SELECT count(*) FROM business_probe")) == 0


@pytest.mark.asyncio
async def test_cache_storage_failure_preserves_generated_vectors_and_usage(cache_db, cache_engine, monkeypatch):
    gateway = FakeEmbeddingGateway()
    _configure_embedding(monkeypatch, gateway)
    async with cache_engine.begin() as connection:
        await connection.execute(text("DROP TABLE retrieval_embeddings"))
    result = await rank_chunks_hybrid(cache_db, "privacy safeguards", _privacy_chunks())
    assert result.mode == "hybrid"
    assert result.semantic_status == "completed"
    assert result.embedding_tokens == 6
    assert result.ranked[0].chunk.chunk_id == "privacy"
    assert cache_db.is_active
    await cache_db.execute(text("INSERT INTO business_probe (id) VALUES (1)"))
    await cache_db.commit()
    async with cache_engine.connect() as observer:
        assert await observer.scalar(text("PRAGMA busy_timeout")) == 5000
        assert await observer.scalar(text("SELECT count(*) FROM business_probe")) == 1


@pytest.mark.asyncio
async def test_cache_connections_are_closed_before_waiting_for_embeddings(cache_db, cache_engine, monkeypatch):
    class ConnectionCheckingGateway(FakeEmbeddingGateway):
        async def embed(self, texts):
            assert cache_engine.pool.checkedout() == 0
            return await super().embed(texts)

    _configure_embedding(monkeypatch, ConnectionCheckingGateway())
    result = await rank_chunks_hybrid(cache_db, "privacy safeguards", _privacy_chunks())
    assert result.mode == "hybrid"
    assert cache_engine.pool.checkedout() == 0
    assert not cache_db.in_transaction()


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_bound", [False, True])
async def test_shared_connection_caches_are_skipped_without_changing_business_transaction(connection_bound, cache_engine, monkeypatch):
    gateway = FakeEmbeddingGateway()
    _configure_embedding(monkeypatch, gateway)
    engine = cache_engine if connection_bound else create_async_engine("sqlite+aiosqlite:///:memory:")
    if not connection_bound:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE business_probe (id INTEGER PRIMARY KEY)"))
    try:
        async with engine.connect() as connection:
            bind = connection if connection_bound else engine
            async with AsyncSession(bind=bind) as caller:
                await caller.execute(text("INSERT INTO business_probe (id) VALUES (1)"))
                result = await rank_chunks_hybrid(caller, "privacy safeguards", _privacy_chunks())
                assert result.mode == "hybrid"
                assert result.embedding_tokens == 6
                assert caller.is_active and caller.in_transaction()
                await caller.rollback()
                assert await caller.scalar(text("SELECT count(*) FROM business_probe")) == 0
    finally:
        if not connection_bound:
            await engine.dispose()

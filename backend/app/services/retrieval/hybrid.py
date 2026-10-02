"""Optional semantic retrieval with persistent cache and BM25 fallback."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import StaticPool

from app.config import get_embedding_config
from app.models.retrieval import RetrievalEmbedding
from app.services.retrieval.bm25 import RankedChunk, rank_chunks
from app.services.retrieval.embeddings import EmbeddingGateway

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from app.services.retrieval.contracts import RetrievalChunk

logger = logging.getLogger(__name__)

MAX_SEMANTIC_CANDIDATES = 256
EMBEDDING_BATCH_SIZE = 64
CACHE_DB_TIMEOUT_SECONDS = 0.75


@asynccontextmanager
async def _cache_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Own a short cache transaction, never the caller's business transaction."""
    engine = db.bind
    # A connection-bound session or in-memory StaticPool cannot provide an
    # independent connection. Cache persistence is optional in these cases.
    if not isinstance(engine, AsyncEngine) or isinstance(engine.pool, StaticPool):
        raise RuntimeError("Embedding cache requires an independent connection")
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError("Embedding cache dialect is unsupported")
    async with asyncio.timeout(CACHE_DB_TIMEOUT_SECONDS):
        async with engine.connect() as connection:
            previous_busy_timeout = None
            try:
                if engine.dialect.name == "sqlite":
                    previous_busy_timeout = (
                        await connection.exec_driver_sql("PRAGMA busy_timeout")
                    ).scalar_one()
                    await connection.exec_driver_sql("PRAGMA busy_timeout=100")
                    await connection.rollback()
                async with AsyncSession(bind=connection, expire_on_commit=False) as cache:
                    yield cache
            finally:
                if previous_busy_timeout is not None:
                    try:
                        # A failed INSERT may have invalidated the transaction.
                        # End it before restoring connection-local pool settings.
                        await connection.rollback()
                        await connection.exec_driver_sql(
                            f"PRAGMA busy_timeout={int(previous_busy_timeout)}"
                        )
                        await connection.rollback()
                    except BaseException:
                        await connection.invalidate()
                        raise


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    ranked: list[RankedChunk]
    mode: Literal["bm25", "hybrid"]
    semantic_status: Literal["completed", "skipped", "unavailable"]
    detail: str
    embedding_tokens: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)


def rank_chunks_by_vector(
    query_vector: Sequence[float],
    chunks: Sequence[RetrievalChunk],
    vectors: dict[str, Sequence[float]],
    *,
    limit: int = 40,
    minimum_similarity: float = 0.28,
) -> list[RankedChunk]:
    scored = [
        RankedChunk(chunk=chunk, score=round(cosine_similarity(query_vector, vector), 6))
        for chunk in chunks
        if (vector := vectors.get(chunk.chunk_id)) is not None
    ]
    scored = [item for item in scored if item.score >= minimum_similarity]
    scored.sort(
        key=lambda item: (
            item.score,
            item.chunk.source,
            item.chunk.document_id,
            -item.chunk.position,
        ),
        reverse=True,
    )
    return scored[:limit]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RankedChunk]],
    *,
    weights: Sequence[float] | None = None,
    limit: int = 6,
    max_per_document: int = 2,
    rank_constant: int = 60,
) -> list[RankedChunk]:
    """Fuse rank positions instead of incomparable BM25/cosine scores."""
    if limit <= 0 or max_per_document <= 0:
        return []
    applied_weights = list(weights or [1.0] * len(rankings))
    if len(applied_weights) != len(rankings):
        raise ValueError("RRF 权重数量必须与排名列表数量一致")
    scores: defaultdict[str, float] = defaultdict(float)
    chunks: dict[str, RetrievalChunk] = {}
    for ranking, weight in zip(rankings, applied_weights, strict=True):
        for rank, item in enumerate(ranking, start=1):
            scores[item.chunk.chunk_id] += weight / (rank_constant + rank)
            chunks[item.chunk.chunk_id] = item.chunk
    ordered = sorted(
        scores,
        key=lambda chunk_id: (
            scores[chunk_id],
            chunks[chunk_id].source,
            chunks[chunk_id].document_id,
            -chunks[chunk_id].position,
        ),
        reverse=True,
    )
    selected: list[RankedChunk] = []
    per_document: Counter[str] = Counter()
    for chunk_id in ordered:
        chunk = chunks[chunk_id]
        if per_document[chunk.document_id] >= max_per_document:
            continue
        selected.append(RankedChunk(chunk=chunk, score=round(scores[chunk_id], 8)))
        per_document[chunk.document_id] += 1
        if len(selected) >= limit:
            break
    return selected


def _input_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _balanced_semantic_pool(
    chunks: Sequence[RetrievalChunk],
    lexical: Sequence[RankedChunk],
) -> list[RetrievalChunk]:
    selected: list[RetrievalChunk] = []
    seen: set[str] = set()
    for item in lexical:
        if item.chunk.chunk_id not in seen:
            selected.append(item.chunk)
            seen.add(item.chunk.chunk_id)
    groups: defaultdict[str, list[RetrievalChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.chunk_id not in seen:
            groups[chunk.source].append(chunk)
    sources = sorted(groups)
    index = 0
    while len(selected) < MAX_SEMANTIC_CANDIDATES and sources:
        next_sources: list[str] = []
        for source in sources:
            if index < len(groups[source]):
                selected.append(groups[source][index])
                if len(selected) >= MAX_SEMANTIC_CANDIDATES:
                    break
            if index + 1 < len(groups[source]):
                next_sources.append(source)
        sources = next_sources
        index += 1
    return selected[:MAX_SEMANTIC_CANDIDATES]


async def _cached_vectors(
    db: AsyncSession,
    *,
    provider: str,
    model: str,
    texts: dict[str, str],
    gateway: EmbeddingGateway,
) -> tuple[dict[str, list[float]], int, int, int]:
    hashes = list(texts)
    existing: dict[str, list[float]] = {}
    try:
        async with _cache_session(db) as cache:
            existing_result = await cache.execute(
                select(RetrievalEmbedding).where(
                    RetrievalEmbedding.provider == provider,
                    RetrievalEmbedding.model == model,
                    RetrievalEmbedding.input_hash.in_(hashes),
                )
            )
            existing = {
                item.input_hash: item.as_vector()
                for item in existing_result.scalars().all()
                if item.vector and item.dimensions == len(item.vector)
            }
    except Exception as exc:
        logger.info("Embedding cache read skipped (%s)", type(exc).__name__)
    missing = [input_hash for input_hash in hashes if input_hash not in existing]
    generated: dict[str, list[float]] = {}
    input_tokens = 0
    for offset in range(0, len(missing), EMBEDDING_BATCH_SIZE):
        batch_hashes = missing[offset:offset + EMBEDDING_BATCH_SIZE]
        batch = await gateway.embed([texts[input_hash] for input_hash in batch_hashes])
        input_tokens += batch.input_tokens
        generated.update(zip(batch_hashes, batch.vectors, strict=True))
    if generated:
        rows = [
            {
                "id": RetrievalEmbedding.build_id(provider, model, input_hash),
                "provider": provider,
                "model": model,
                "input_hash": input_hash,
                "dimensions": len(vector),
                "vector": vector,
            }
            for input_hash, vector in generated.items()
        ]
        try:
            async with _cache_session(db) as cache:
                insert = sqlite_insert if cache.bind.dialect.name == "sqlite" else postgres_insert
                await cache.execute(insert(RetrievalEmbedding).on_conflict_do_nothing(), rows)
                await cache.commit()
        except Exception as exc:
            # Cache contention must neither invalidate the user's session nor
            # discard already-paid vectors and their returned token usage.
            logger.info("Embedding cache write skipped (%s)", type(exc).__name__)
    return {**existing, **generated}, input_tokens, len(existing), len(generated)


async def rank_chunks_hybrid(
    db: AsyncSession,
    query: str,
    chunks: Sequence[RetrievalChunk],
    *,
    limit: int = 6,
    max_per_document: int = 2,
) -> HybridRetrievalResult:
    lexical = rank_chunks(
        query,
        chunks,
        limit=40,
        max_per_document=6,
    )
    fallback = rank_chunks(
        query,
        chunks,
        limit=limit,
        max_per_document=max_per_document,
    )
    if not chunks:
        return HybridRetrievalResult(
            ranked=[],
            mode="bm25",
            semantic_status="skipped",
            detail="没有本地候选材料，未调用 Embedding",
        )
    config = get_embedding_config()
    if not config.get("enabled"):
        return HybridRetrievalResult(
            ranked=fallback,
            mode="bm25",
            semantic_status="skipped",
            detail="语义检索未启用，使用零额外成本的 BM25",
        )

    provider = str(config.get("provider") or "").casefold()
    model = str(config.get("model") or "").strip()
    try:
        gateway = EmbeddingGateway(config)
        pool = _balanced_semantic_pool(chunks, lexical)
        query_hash = _input_hash(query)
        chunk_hashes = {chunk.chunk_id: _input_hash(chunk.search_text) for chunk in pool}
        texts = {query_hash: query}
        texts.update({chunk_hashes[chunk.chunk_id]: chunk.search_text for chunk in pool})
        cached, tokens, hits, misses = await _cached_vectors(
            db,
            provider=provider,
            model=model,
            texts=texts,
            gateway=gateway,
        )
        query_vector = cached[query_hash]
        vectors = {
            chunk.chunk_id: cached[input_hash]
            for chunk in pool
            if (input_hash := chunk_hashes[chunk.chunk_id]) in cached
        }
        semantic = rank_chunks_by_vector(query_vector, pool, vectors)
        fused = reciprocal_rank_fusion(
            [lexical, semantic],
            weights=[1.15, 1.0],
            limit=limit,
            max_per_document=max_per_document,
        )
        ranked = fused or fallback
        return HybridRetrievalResult(
            ranked=ranked,
            mode="hybrid" if fused else "bm25",
            semantic_status="completed" if fused else "unavailable",
            detail=(
                f"{provider}/{model} 对 {len(pool)} 个候选执行语义排序；"
                f"缓存命中 {hits}，新生成 {misses}，Embedding Token {tokens}"
            ),
            embedding_tokens=tokens,
            cache_hits=hits,
            cache_misses=misses,
        )
    except Exception as exc:
        logger.warning(
            "Semantic retrieval unavailable; falling back to BM25 (%s)",
            type(exc).__name__,
        )
        return HybridRetrievalResult(
            ranked=fallback,
            mode="bm25",
            semantic_status="unavailable",
            detail="语义检索暂时不可用，已自动降级为 BM25，不影响回答",
        )

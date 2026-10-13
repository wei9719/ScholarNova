"""Versioned, deterministic text features for knowledge-base retrieval."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeBase, KnowledgeChunk
from app.services.features.reading_notes import reading_note_parts

KNOWLEDGE_FEATURE_VERSION = "knowledge-chunker-v2"
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", ". ", "; ", "；")


def _normalized_text(text: str) -> str:
    lines = [_WHITESPACE.sub(" ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_knowledge_text(
    text: str,
    *,
    target_chars: int = 1200,
    overlap_chars: int = 160,
) -> list[str]:
    """Split text near paragraph/sentence boundaries with deterministic overlap."""
    normalized = _normalized_text(text)
    if not normalized:
        return []
    if target_chars < 200:
        raise ValueError("target_chars must be at least 200")
    if overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("overlap_chars must be between 0 and target_chars")

    chunks: list[str] = []
    start = 0
    text_length = len(normalized)
    while start < text_length:
        proposed_end = min(text_length, start + target_chars)
        end = proposed_end
        if proposed_end < text_length:
            minimum_end = start + int(target_chars * 0.6)
            candidates: list[int] = []
            window = normalized[start:proposed_end]
            for boundary in _BOUNDARIES:
                index = window.rfind(boundary)
                if index >= 0:
                    candidates.append(start + index + len(boundary))
            valid = [candidate for candidate in candidates if candidate >= minimum_end]
            if valid:
                end = max(valid)

        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        next_start = max(0, end - overlap_chars)
        start = next_start if next_start > start else end

    return chunks


def _chunk_id(
    knowledge_id: str,
    content_hash: str,
    position: int,
) -> str:
    identity = f"{KNOWLEDGE_FEATURE_VERSION}\0{knowledge_id}\0{content_hash}\0{position}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _build_chunks(knowledge: KnowledgeBase) -> list[KnowledgeChunk]:
    if knowledge.card_type == "reading_note" and isinstance(knowledge.card_data, dict):
        parts = reading_note_parts(knowledge.card_data)
    else:
        parts = [("知识库材料（用户整理，未独立核验，非论文原文）", knowledge.content or "")]
    if knowledge.notes:
        parts.append(("个人备注，非论文事实", knowledge.notes))
    texts = [
        f"【{label}】\n{chunk}"
        for label, text in parts
        for chunk in split_knowledge_text(text, target_chars=1100)
    ]
    content_hash = _content_hash("\n\n".join(texts))
    return [
        KnowledgeChunk(
            id=_chunk_id(knowledge.id, content_hash, position),
            knowledge_id=knowledge.id,
            position=position,
            content=content,
            content_hash=content_hash,
            feature_version=KNOWLEDGE_FEATURE_VERSION,
            char_count=len(content),
        )
        for position, content in enumerate(texts)
    ]


async def rebuild_knowledge_features(
    db: AsyncSession,
    knowledge: KnowledgeBase,
) -> list[KnowledgeChunk]:
    """Replace the feature rows for one knowledge item atomically in the session."""
    await db.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_id == knowledge.id)
    )
    chunks = _build_chunks(knowledge)
    db.add_all(chunks)
    await db.flush()
    return chunks


async def ensure_knowledge_features(
    db: AsyncSession,
    knowledge_items: Iterable[KnowledgeBase],
) -> list[KnowledgeChunk]:
    """Lazily backfill missing/stale features and return current chunks."""
    items = list(knowledge_items)
    if not items:
        return []
    knowledge_ids = [item.id for item in items]
    existing = list((await db.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.knowledge_id.in_(knowledge_ids))
    )).scalars().all())
    by_knowledge: dict[str, list[KnowledgeChunk]] = defaultdict(list)
    for chunk in existing:
        by_knowledge[chunk.knowledge_id].append(chunk)

    for item in items:
        expected = _build_chunks(item)
        current = sorted(by_knowledge.get(item.id, []), key=lambda chunk: chunk.position)
        is_current = (
            len(current) == len(expected)
            and all(
                old.id == new.id
                and old.feature_version == KNOWLEDGE_FEATURE_VERSION
                for old, new in zip(current, expected)
            )
        )
        if not is_current:
            await rebuild_knowledge_features(db, item)

    return list((await db.execute(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.knowledge_id.in_(knowledge_ids))
        .order_by(KnowledgeChunk.knowledge_id, KnowledgeChunk.position)
    )).scalars().all())


async def delete_knowledge_features(db: AsyncSession, knowledge_id: str) -> None:
    await db.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_id == knowledge_id)
    )

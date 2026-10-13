"""Search the user's locally imported Zotero metadata."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select

from app.database import async_session_factory
from app.models.paper import PaperEntity
from app.schemas.paper import Paper
from app.services.sources.base import BaseSource


class LocalLibrarySource(BaseSource):
    """A zero-network source backed by ScholarNova's local paper table."""

    def __init__(self, session_factory: Any = async_session_factory) -> None:
        super().__init__(timeout=5, max_retries=0)
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "zotero"

    @property
    def base_api_url(self) -> str:
        return "local://scholarnova-library"

    @staticmethod
    def _terms(query: str) -> list[str]:
        terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9._+-]*|[\u4e00-\u9fff]{2,}", query.casefold())
        seen: set[str] = set()
        unique: list[str] = []
        for term in terms:
            if term not in seen:
                seen.add(term)
                unique.append(term)
        return unique[:8]

    @staticmethod
    def _belongs_to_zotero(entity: PaperEntity) -> bool:
        return entity.source == "zotero" or (
            isinstance(entity.extra_metadata, dict) and "zotero" in entity.extra_metadata
        )

    @staticmethod
    def _paper(entity: PaperEntity, score: float) -> Paper:
        return Paper(
            id=entity.id,
            title=entity.title,
            authors=entity.author_names,
            abstract=entity.abstract,
            year=entity.year,
            publication_date=entity.publication_date,
            venue=entity.venue,
            citation_count=entity.citation_count,
            doi=entity.doi,
            url=entity.url,
            pdf_url=entity.pdf_url,
            source="zotero",
            relevance_score=min(max(score, 0.0), 1.0),
            is_open_access=entity.is_open_access,
        )

    async def search(
        self, query: str, max_results: int = 50, *,
        date_from: str | None = None, date_to: str | None = None,
    ) -> list[Paper]:
        terms = self._terms(query)
        if not terms:
            return []

        filters = []
        for term in terms:
            pattern = f"%{term}%"
            filters.extend(
                [
                    func.lower(PaperEntity.title).like(pattern),
                    func.lower(func.coalesce(PaperEntity.abstract, "")).like(pattern),
                    func.lower(func.coalesce(PaperEntity.venue, "")).like(pattern),
                ]
            )

        candidate_limit = min(max(max_results * 10, 100), 1000)
        statement = select(PaperEntity).where(or_(*filters))
        if date_from:
            start = date.fromisoformat(date_from)
            statement = statement.where(or_(
                PaperEntity.publication_date >= start,
                (PaperEntity.publication_date.is_(None)) & (PaperEntity.year >= start.year),
            ))
        if date_to:
            end = date.fromisoformat(date_to)
            statement = statement.where(or_(
                PaperEntity.publication_date <= end,
                (PaperEntity.publication_date.is_(None)) & (PaperEntity.year <= end.year),
            ))
        async with self._session_factory() as db:
            result = await db.execute(
                statement
                .order_by(PaperEntity.updated_at.desc())
                .limit(candidate_limit)
            )
            entities = [
                entity for entity in result.scalars().all() if self._belongs_to_zotero(entity)
            ]

        normalised_query = " ".join(query.casefold().split())
        scored: list[tuple[float, PaperEntity]] = []
        for entity in entities:
            title = entity.title.casefold()
            abstract = (entity.abstract or "").casefold()
            venue = (entity.venue or "").casefold()
            keywords = " ".join(entity.keywords or []).casefold()
            raw_score = sum(
                (3.0 if term in title else 0.0)
                + (1.0 if term in abstract else 0.0)
                + (0.75 if term in venue else 0.0)
                + (0.75 if term in keywords else 0.0)
                for term in terms
            )
            if normalised_query and normalised_query in f"{title} {abstract}":
                raw_score += 5.0
            if raw_score > 0:
                maximum = max(len(terms) * 5.5 + 5.0, 1.0)
                scored.append((raw_score / maximum, entity))

        scored.sort(key=lambda item: (item[0], item[1].year or 0), reverse=True)
        return [self._paper(entity, score) for score, entity in scored[:max_results]]

    async def get_paper(self, paper_id: str) -> Paper | None:
        async with self._session_factory() as db:
            entity = (
                await db.execute(
                    select(PaperEntity).where(
                        or_(
                            PaperEntity.id == paper_id,
                            PaperEntity.external_id == f"zotero:{paper_id}",
                        ),
                    )
                )
            ).scalar_one_or_none()
        return self._paper(entity, 1.0) if entity and self._belongs_to_zotero(entity) else None

    async def get_pdf_url(self, paper_id: str) -> str | None:
        paper = await self.get_paper(paper_id)
        return paper.pdf_url if paper else None

    async def health_check(self) -> dict:
        async with self._session_factory() as db:
            entities = (await db.execute(select(PaperEntity))).scalars().all()
        count = sum(1 for entity in entities if self._belongs_to_zotero(entity))
        return {"status": "ok", "source": self.name, "paper_count": count}

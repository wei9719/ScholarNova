"""Resolve external paper identities before exposing actionable local IDs."""

import re

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import PaperEntity
from app.schemas.paper import Paper


def normalize_doi(value: str | None) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", (value or "").strip().casefold())


async def _existing_paper(db: AsyncSession, paper: Paper) -> PaperEntity | None:
    conditions = [PaperEntity.id == str(paper.id)]
    doi = normalize_doi(paper.doi)
    if doi:
        conditions.extend([
            func.lower(PaperEntity.canonical_doi) == doi,
            func.lower(func.trim(PaperEntity.doi)).in_([
                doi, "https://doi.org/" + doi, "http://doi.org/" + doi,
                "http://dx.doi.org/" + doi, "https://dx.doi.org/" + doi, "doi:" + doi,
            ]),
        ])
    if paper.corpus_id:
        conditions.append(PaperEntity.external_id == paper.corpus_id)
    return (await db.execute(
        select(PaperEntity).where(or_(*conditions))
        .order_by(PaperEntity.id != str(paper.id)).limit(1)
    )).scalar_one_or_none()


async def persist_papers(db: AsyncSession, papers: list[Paper]) -> list[Paper]:
    """Flush records and return their local IDs; the caller must commit before publishing."""
    resolved = []
    for paper in papers:
        existing = await _existing_paper(db, paper)
        if existing is None:
            try:
                async with db.begin_nested():
                    db.add(PaperEntity(
                        id=str(paper.id), title=paper.title, abstract=paper.abstract,
                        authors=[{"name": name} for name in paper.authors],
                        year=paper.year, venue=paper.venue, doi=normalize_doi(paper.doi) or None,
                        canonical_doi=normalize_doi(paper.doi) or None,
                        external_id=paper.corpus_id or None,
                        url=paper.url, pdf_url=paper.pdf_url, source=paper.source,
                        citation_count=paper.citation_count, is_open_access=paper.is_open_access,
                    ))
                    await db.flush()
            except IntegrityError:
                # Another search/recommendation may have saved this identity first.
                existing = await _existing_paper(db, paper)
                if existing is None:
                    raise
        if existing is not None:
            paper = paper.model_copy(update={"id": existing.id})
            for field in ("abstract", "venue", "url", "pdf_url"):
                if not getattr(existing, field) and getattr(paper, field):
                    setattr(existing, field, getattr(paper, field))
        resolved.append(paper)
    await db.flush()
    return resolved

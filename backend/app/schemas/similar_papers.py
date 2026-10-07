"""Seed-paper recommendations describe retrieved evidence, never invented papers."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.paper import Paper


SimilarMode = Literal["topic", "structure", "journal"]


class SimilarPapersRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=255)
    mode: SimilarMode = "topic"
    limit: int = Field(default=6, ge=1, le=12)

    @field_validator("paper_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("paper_id cannot be blank")
        return value.strip()


class SimilarPaperItem(BaseModel):
    paper: Paper
    reason: str
    matched_terms: list[str] = Field(default_factory=list)
    structure_labels: list[str] = Field(default_factory=list)


class TermFrequency(BaseModel):
    term: str
    count: int = Field(ge=0)


class SimilarStatistics(BaseModel):
    basis: Literal["retrieved_candidates"] = "retrieved_candidates"
    candidate_count: int = 0
    matched_count: int = 0
    returned_count: int = 0
    same_venue_count: int = 0
    term_sample_count: int = 0
    term_sample_scope: Literal["same_venue", "retrieved_candidates"] = "retrieved_candidates"
    terms: list[TermFrequency] = Field(default_factory=list)


class SimilarSourceStatus(BaseModel):
    source: str
    success: bool
    paper_count: int = 0
    elapsed_ms: float = 0
    error: str | None = None


class SimilarPapersResponse(BaseModel):
    seed_paper_id: str
    mode: SimilarMode
    items: list[SimilarPaperItem] = Field(default_factory=list)
    scope: str
    warnings: list[str] = Field(default_factory=list)
    statistics: SimilarStatistics = Field(default_factory=SimilarStatistics)
    source_statuses: list[SimilarSourceStatus] = Field(default_factory=list)

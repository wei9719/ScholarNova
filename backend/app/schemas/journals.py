"""Journal discovery is metadata retrieval, not a journal-ranking prediction."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.paper import Paper


class JournalCandidate(BaseModel):
    id: str = Field(max_length=100)
    title: str = Field(max_length=2000)
    venue: str | None = Field(default=None, max_length=500)


class JournalDiscoveryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    candidates: list[JournalCandidate] = Field(default_factory=list, max_length=100)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("检索词不能为空")
        return value.strip()


class JournalMetric(BaseModel):
    name: str
    value: float | str
    year: int | None = None
    source: str
    source_url: str | None = None
    basis: str


class JournalRecommendation(BaseModel):
    name: str
    source_id: str | None = None
    issns: list[str] = Field(default_factory=list)
    verification: Literal["verified_journal", "pending"] = "pending"
    homepage_url: str | None = None
    author_guidelines_url: str | None = None
    reason: str
    candidate_match_count: int = 0
    candidate_paper_ids: list[str] = Field(default_factory=list)
    discovery_match_count: int = 0
    metrics: list[JournalMetric] = Field(default_factory=list)


class JournalDiscoveryResponse(BaseModel):
    query: str
    journals: list[JournalRecommendation] = Field(default_factory=list)
    candidate_count: int
    discovery_candidate_count: int = 0
    scope: str = "候选命中数仅来自当前搜索结果；补充发现数仅来自本次 OpenAlex 返回样本，不代表全刊发文占比或期刊质量排名。"
    warnings: list[str] = Field(default_factory=list)
    checked_at: datetime
    cached: bool = False


class JournalPapersRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=15, ge=1, le=30)
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        return JournalDiscoveryRequest.nonblank_query(value)


class JournalPapersResponse(BaseModel):
    source_id: str
    query: str
    papers: list[Paper] = Field(default_factory=list)
    scope: str = "OpenAlex Works API 按已核验的期刊 Source ID 与检索词查询；这是返回样本，不是全刊遍历。"
    warnings: list[str] = Field(default_factory=list)

"""Page-anchored, unsaved reading assistance for an authorized local PDF."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReaderMode = Literal[
    "translate", "explain", "math", "figure", "prerequisite", "writing", "extension",
]


class ReaderDocument(BaseModel):
    paper_id: str
    title: str
    available: bool
    page_count: int = 0
    content_hash: str | None = None
    pdf_url: str | None = None
    source: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ReaderPage(BaseModel):
    paper_id: str
    page_number: int
    page_count: int
    content_hash: str
    text: str
    text_truncated: bool
    text_verified: bool
    image_url: str
    coverage: str
    warnings: list[str] = Field(default_factory=list)


class ReaderAssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: ReaderMode
    page_number: int = Field(ge=1, le=2000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection: str = Field(default="", max_length=6000)
    question: str = Field(default="", max_length=2000)


class ReaderSource(BaseModel):
    label: str
    page_number: int
    content_hash: str
    excerpt: str
    paper_id: str | None = None
    paper_title: str | None = None
    venue: str | None = None


class ReaderLibraryItem(BaseModel):
    paper_id: str
    title: str
    venue: str | None = None
    year: int | None = None
    source: str


class ReaderLibraryResponse(BaseModel):
    papers: list[ReaderLibraryItem]
    limit: int = 50
    warnings: list[str] = Field(default_factory=list)


class WritingSamplesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paper_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(min_length=2, max_length=5)
    question: str = Field(default="", max_length=2000)
    content_hashes: dict[str, Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]] = Field(default_factory=dict, max_length=5)

    @field_validator("paper_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        ids = [item.strip() for item in value]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("请选择 2 至 5 篇不同论文")
        return ids

    @model_validator(mode="after")
    def hashes_belong_to_papers(self):
        if any(key not in self.paper_ids for key in self.content_hashes):
            raise ValueError("材料版本只能对应本次选择的论文")
        return self


class ReaderCalculationCheck(BaseModel):
    """Local numeric check, never an endorsement of a paper's formula or proof."""

    metric: str
    status: Literal["computed", "matched", "mismatch", "unsupported"]
    y_true: list[float] = Field(default_factory=list)
    y_pred: list[float] = Field(default_factory=list)
    mask: list[int] | None = None
    claimed_result: float | None = None
    computed_result: float | None = None
    detail: str


class ReaderAssistResponse(BaseModel):
    answer: str
    source_notes: str = ""
    teaching_notes: str = ""
    exercise: str = ""
    sources: list[ReaderSource]
    coverage: str
    warnings: list[str] = Field(default_factory=list)
    model_completed: bool
    provider: str | None = None
    model: str | None = None
    total_tokens: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    model_attempts: list[dict] = Field(default_factory=list)
    fallback_used: bool = False
    calculation_checks: list[ReaderCalculationCheck] = Field(default_factory=list)

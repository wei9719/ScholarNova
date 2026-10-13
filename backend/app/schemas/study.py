"""Bounded, unsaved study drafts from one paper's local evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StudySection = Literal[
    "overview", "experiments", "resources", "math", "prerequisites",
    "writing", "figures", "translation", "formatting",
]


class StudyDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paper_id: str = Field(min_length=1, max_length=128)
    section: StudySection
    question: str | None = Field(default=None, max_length=2000)


class StudySource(BaseModel):
    label: str
    id: str
    content_hash: str
    section: str
    page: int | None = Field(default=None, ge=1)
    excerpt: str


class StudyDraftResponse(BaseModel):
    section: StudySection
    content: str
    material_scope: Literal["abstract", "fulltext_excerpt"]
    sources: list[StudySource]
    model_completed: bool = Field(
        description="模型草稿已返回且通过基本来源编号检查；不代表事实已人工核验。资源提取不调用模型，此值为 false。",
    )
    provider: str | None = None
    model: str | None = None
    total_tokens: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)

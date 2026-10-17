"""
研究知识库 Pydantic Schemas

用于 API 请求/响应验证
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ReadingSectionName = Literal[
    "overview", "experiments", "resources", "math", "prerequisites",
    "writing", "figures", "translation", "formatting",
]


class ReadingNoteSection(BaseModel):
    """Separate source excerpts, model suggestions and the reader's own ideas."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paper_notes: str = Field(default="", max_length=20_000)
    ai_suggestions: str = Field(default="", max_length=20_000)
    user_notes: str = Field(default="", max_length=20_000)


class ReadingNoteData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    status: Literal["to_read", "reading", "reproducing", "reviewed"]
    source_query: str = Field(default="", max_length=2000)
    sections: dict[ReadingSectionName, ReadingNoteSection] = Field(default_factory=dict)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version 必须为整数 1")
        return value


def validate_reading_note_data(data: dict | None) -> dict:
    return ReadingNoteData.model_validate(data).model_dump()


# ============================================================
# 知识库 Schemas
# ============================================================

class KnowledgeCreate(BaseModel):
    """创建知识条目请求"""
    title: str = Field(..., description="知识点标题")
    category: str = Field(..., description="分类")
    content: str = Field(..., description="AI分析的详细内容")
    source_paper_id: Optional[str] = Field(None, description="来源论文ID")
    source_paper_title: Optional[str] = Field(None, description="来源论文标题")
    source_paper_doi: Optional[str] = Field(None, description="来源论文DOI")
    research_points: List[str] = Field(default=[], description="研究点列表")
    tags: List[str] = Field(default=[], description="标签列表")
    notes: Optional[str] = Field(None, description="用户备注")
    auto_polish: bool = Field(default=False, description="是否生成待确认润色草稿；始终保存原文，不自动覆盖")
    card_type: Optional[str] = Field(None, max_length=50, description="卡片类型，兼容旧类型；新增 reading_note/foundation/skill")
    card_data: Optional[dict] = Field(None, description="结构化卡片数据")

    @model_validator(mode="after")
    def validate_reading_note(self):
        if self.card_type == "reading_note":
            self.card_data = validate_reading_note_data(self.card_data)
        return self


class KnowledgeUpdate(BaseModel):
    """更新知识条目请求"""
    title: Optional[str] = Field(None, description="知识点标题")
    category: Optional[str] = Field(None, description="分类")
    content: Optional[str] = Field(None, description="AI分析的详细内容")
    source_paper_id: Optional[str] = Field(None, description="来源论文ID")
    source_paper_title: Optional[str] = Field(None, description="来源论文标题")
    source_paper_doi: Optional[str] = Field(None, description="来源论文DOI")
    research_points: Optional[List[str]] = Field(None, description="提取的研究点列表")
    tags: Optional[List[str]] = Field(None, description="标签列表")
    notes: Optional[str] = Field(None, description="用户备注")
    card_type: Optional[str] = Field(None, max_length=50, description="卡片类型")
    card_data: Optional[dict] = Field(None, description="结构化数据；阅读笔记不得清空")
    expected_updated_at: Optional[datetime] = Field(None, description="编辑时读取的更新时间；不一致返回 409")


class KnowledgePolishDraft(BaseModel):
    """Unconfirmed model suggestion returned once; never persisted or indexed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    content: str = Field(min_length=1, description="未确认的 AI 润色草稿，不代表事实已核验")
    research_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class KnowledgeResponse(BaseModel):
    """知识条目响应"""
    id: str
    title: str
    category: str
    content: str
    source_paper_id: Optional[str] = None
    source_paper_title: Optional[str] = None
    source_paper_doi: Optional[str] = None
    research_points: List[str] = []
    tags: List[str] = []
    notes: Optional[str] = None
    card_type: Optional[str] = None
    card_data: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    polish_status: Optional[Literal["skipped", "completed", "invalid_response", "model_unavailable"]] = Field(None, description="仅本次创建请求的润色状态；completed表示生成待确认草稿，不表示已覆盖原文，非持久化字段")
    polish_draft: KnowledgePolishDraft | None = Field(None, description="仅本次创建返回的未确认草稿；未保存、未索引，原文仍在数据库中，须用户明确确认后通过更新接口采用")
    model_completed: Optional[bool] = Field(None, description="本次润色模型是否返回结果；未调用时为空")
    provider: Optional[str] = None
    model: Optional[str] = None
    fallback_used: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    class Config:
        from_attributes = True


class CategoryCount(BaseModel):
    """分类统计"""
    name: str
    count: int


class KnowledgeListResponse(BaseModel):
    """知识列表响应"""
    items: List[KnowledgeResponse]
    total: int
    categories: List[CategoryCount]


# ============================================================
# 研究路线 Schemas
# ============================================================

class RouteCreate(BaseModel):
    """创建研究路线请求"""
    title: str = Field(..., description="路线标题")
    description: str = Field(..., description="路线描述")
    knowledge_ids: List[str] = Field(default=[], description="关联的知识点ID列表")


class RouteUpdate(BaseModel):
    """更新研究路线请求"""
    title: Optional[str] = Field(None, description="路线标题")
    description: Optional[str] = Field(None, description="路线描述")
    knowledge_ids: Optional[List[str]] = Field(None, description="关联的知识点ID列表")
    status: Optional[str] = Field(None, description="状态：active/completed/archived")


class RouteResponse(BaseModel):
    """研究路线响应"""
    id: str
    title: str
    description: str
    knowledge_ids: List[str] = []
    ai_analysis: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RouteListResponse(BaseModel):
    """研究路线列表响应"""
    items: List[RouteResponse]
    total: int


# ============================================================
# AI 分析 Schemas
# ============================================================

class AIAnalyzeRequest(BaseModel):
    """AI分析研究推进方向请求"""
    knowledge_ids: List[str] = Field(..., min_length=1, max_length=50, description="基于哪些知识点进行分析")
    query: Optional[str] = Field(None, max_length=2000, description="用户额外的分析要求")


class AIAnalyzeResponse(BaseModel):
    """AI分析研究推进方向响应"""
    analysis: str = Field(..., description="AI生成的分析结果（中文）")
    architecture_json: Optional[dict] = Field(None, description="AI评判提炼的统一架构 JSON（层/模块/公式），用于确定性 SVG 渲染")
    knowledge_count: int = Field(..., description="分析的知识点数量")
    provider: Optional[str] = Field(None, description="实际使用的模型提供商")
    model: Optional[str] = Field(None, description="实际使用的模型")
    model_completed: bool = Field(True, description="模型是否完成分析")
    fallback_used: bool = Field(False, description="是否由备用模型接管")
    prompt_tokens: int = Field(0, description="输入 Token")
    completion_tokens: int = Field(0, description="输出 Token")
    total_tokens: int = Field(0, description="总 Token")
    created_at: datetime


class RecommendRequest(BaseModel):
    """基于知识库推荐论文请求"""
    knowledge_ids: List[str] = Field(..., description="基于哪些知识点推荐")
    limit: int = Field(default=10, ge=1, le=50, description="推荐数量")


class RecommendResponse(BaseModel):
    """基于知识库推荐论文响应"""
    recommendations: str = Field(..., description="AI生成的推荐结果（中文）")
    knowledge_count: int = Field(..., description="基于的知识点数量")
    provider: Optional[str] = Field(None, description="实际使用的模型提供商")
    model: Optional[str] = Field(None, description="实际使用的模型")
    model_completed: bool = Field(True, description="模型是否完成推荐规划")
    fallback_used: bool = Field(False, description="是否由备用模型接管")
    prompt_tokens: int = Field(0, description="输入 Token")
    completion_tokens: int = Field(0, description="输出 Token")
    total_tokens: int = Field(0, description="总 Token")
    created_at: datetime

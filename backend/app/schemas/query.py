"""
查询相关的 Pydantic Schemas
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DataSource(str, Enum):
    """学术数据源"""

    SEMANTIC_SCHOLAR = "semantic_scholar"
    OPENALEX = "openalex"
    CROSSREF = "crossref"
    ARXIV = "arxiv"
    ZOTERO = "zotero"


class SubQuery(BaseModel):
    """子查询"""

    query: str = Field(..., description="查询字符串")
    source: DataSource = Field(..., description="目标数据源")
    rationale: str = Field(..., description="查询理由")


class Constraint(BaseModel):
    """查询约束条件"""

    key: str = Field(..., description="约束键（如 year_range, min_citations, open_access）")
    operator: str = Field(..., description="操作符（如 gte, lte, eq, in）")
    value: Any = Field(..., description="约束值")
    description: Optional[str] = Field(None, description="约束描述")


class QueryParseResult(BaseModel):
    """查询解析结果"""

    original_query: str = Field(..., description="原始查询")
    sub_queries: List[SubQuery] = Field(..., description="子查询列表")
    strategy: str = Field(..., description="检索策略说明")
    intent: Optional[str] = Field(None, description="查询意图")
    keywords: List[str] = Field(default_factory=list, description="关键词列表")
    constraints: List[Constraint] = Field(default_factory=list, description="解析出的约束条件")
    entities: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="从查询中识别的主题、方法、数据集、领域和发表 venue",
    )
    expanded_queries: List[str] = Field(
        default_factory=list,
        description="用于低召回场景二轮检索的有界查询扩展",
    )


class AnalysisType(str, Enum):
    """分析类型"""

    SUMMARY = "summary"
    METHODOLOGY = "methodology"
    FINDINGS = "findings"
    PROS_CONS = "pros_cons"
    FULL = "full"


class AnalysisRequest(BaseModel):
    """分析请求"""

    query: str = Field(..., description="分析上下文/问题", min_length=1, max_length=2000)
    analysis_type: AnalysisType = Field(AnalysisType.FULL, description="分析类型")


class AnalysisResult(BaseModel):
    """分析结果"""

    paper_id: str = Field(..., description="论文 ID")
    analysis_type: AnalysisType = Field(..., description="分析类型")
    summary: str = Field(..., description="总结")
    methodology: Optional[str] = Field(None, description="方法论")
    key_findings: List[str] = Field(default_factory=list, description="关键发现")
    strengths: List[str] = Field(default_factory=list, description="优点")
    weaknesses: List[str] = Field(default_factory=list, description="缺点")
    relevance_to_query: Optional[str] = Field(None, description="与查询的相关性")
    document_coverage: str = Field("abstract", description="材料来源级别；fulltext 表示取得 PDF，不代表全部文字或图表已发送模型")
    document_source: Optional[str] = Field(None, description="全文材料来源")
    document_error: Optional[str] = Field(None, description="全文获取失败原因或材料选择、截断提示")
    visual_pages_read: int = Field(0, description="提供给视觉模型的 PDF 页面数")
    model_completed: bool = Field(True, description="模型是否完成本次分析")
    prompt_tokens: int = Field(0, description="本次分析的输入 Token")
    completion_tokens: int = Field(0, description="本次分析的输出 Token")
    total_tokens: int = Field(0, description="本次分析的总 Token")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")


class CompareRequest(BaseModel):
    """对比请求"""

    paper_ids: List[str] = Field(
        ...,
        description="要对比的论文 ID 列表",
        min_length=2,
        max_length=10,
    )
    query: str = Field(..., description="对比的上下文/问题", min_length=1, max_length=2000)


class CompareResult(BaseModel):
    """对比结果"""

    papers: List[dict] = Field(..., description="对比的论文列表")
    comparison: dict = Field(..., description="对比结果")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")

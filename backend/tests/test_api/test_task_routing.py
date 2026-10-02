from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import analysis as analysis_api
from app.api.v1 import knowledge as knowledge_api
from app.schemas.knowledge import AIAnalyzeRequest, RecommendRequest
from app.schemas.query import AnalysisRequest
from app.services.diagram import architecture_judge
from app.services.inference import AllModelsUnavailableError


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _KnowledgeDB:
    def __init__(self, item):
        self.item = item

    async def execute(self, _query):
        return _ScalarResult(self.item)


def _knowledge_item():
    return SimpleNamespace(
        id="knowledge-1",
        title="Grounded retrieval",
        category="Method",
        content="The saved note describes evidence-grounded retrieval.",
        research_points=["citation verification"],
        tags=["RAG"],
    )


@pytest.mark.asyncio
async def test_paper_text_analysis_uses_routed_usage(monkeypatch):
    async def find_paper(_paper_id, _db):
        return {
            "title": "Traceable paper",
            "authors": "Researcher",
            "year": 2026,
            "venue": "Test Venue",
            "abstract": "A supported abstract.",
            "doi": None,
            "url": None,
            "pdf_url": None,
            "source": "test",
        }

    async def load_context(_paper_id, _paper_info, _db):
        return "", [], "abstract", None

    async def routed(**kwargs):
        assert kwargs["task"] == "analysis"
        return SimpleNamespace(
            content="材料覆盖：摘要。仅依据摘要分析。",
            usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        )

    monkeypatch.setattr(analysis_api, "_find_paper_info", find_paper)
    monkeypatch.setattr(analysis_api, "_load_document_context", load_context)
    monkeypatch.setattr(analysis_api, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analysis_api, "chat_with_fallback", routed)

    result = await analysis_api.analyze_paper(
        "paper-1",
        AnalysisRequest(query="What is supported?"),
        object(),
        SimpleNamespace(commit=AsyncMock()),
    )

    assert result.model_completed is True
    assert result.total_tokens == 150
    assert result.visual_pages_read == 0


@pytest.mark.asyncio
async def test_visual_paper_analysis_stays_on_vision_task(monkeypatch):
    async def find_paper(_paper_id, _db):
        return {
            "title": "Visual paper",
            "authors": "Researcher",
            "year": 2026,
            "venue": "Test Venue",
            "abstract": "A supported abstract.",
            "doi": None,
            "url": None,
            "pdf_url": None,
            "source": "test",
        }

    async def load_context(_paper_id, _paper_info, _db):
        return "Methods and verified figure caption.", ["data:image/jpeg;base64,AA=="], "fulltext:test", None

    class VisionGateway:
        def __init__(self, provider):
            assert provider == "vision-provider"
            self.last_usage = {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100}

        def configure(self, **_kwargs):
            return None

        async def chat(self, **kwargs):
            assert isinstance(kwargs["messages"][1]["content"], list)
            return "材料覆盖：全文。已读取图表页面。"

    async def routed(**_kwargs):
        raise AssertionError("visual input must not enter the text fallback router")

    monkeypatch.setattr(analysis_api, "_find_paper_info", find_paper)
    monkeypatch.setattr(analysis_api, "_load_document_context", load_context)
    monkeypatch.setattr(analysis_api, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analysis_api, "chat_with_fallback", routed)
    monkeypatch.setattr("app.services.llm.gateway.LLMGateway", VisionGateway)
    monkeypatch.setattr(
        "app.config.get_model_for_task",
        lambda task: {
            "provider": "vision-provider",
            "model": "vision-model",
            "api_key": "test",
            "base_url": "https://example.test/v1",
        },
    )

    result = await analysis_api.analyze_paper(
        "paper-visual",
        AnalysisRequest(query="Read the figure"),
        object(),
        SimpleNamespace(commit=AsyncMock()),
    )

    assert result.model_completed is True
    assert result.visual_pages_read == 1
    assert result.total_tokens == 100


@pytest.mark.asyncio
async def test_knowledge_analysis_uses_fallback_model_metadata(monkeypatch):
    async def routed(**kwargs):
        assert kwargs["task"] == "analysis"
        return SimpleNamespace(
            content="Grounded analysis",
            profile={"provider": "qwen", "model": "qwen-plus"},
            fallback_used=True,
            usage={"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
        )

    monkeypatch.setattr(knowledge_api, "chat_with_fallback", routed)
    judge = AsyncMock(return_value=None)
    monkeypatch.setattr(knowledge_api, "judge_architecture", judge)
    result = await knowledge_api.ai_analyze_research(
        AIAnalyzeRequest(knowledge_ids=["knowledge-1"]),
        _KnowledgeDB(_knowledge_item()),
    )

    assert result.provider == "qwen"
    assert result.fallback_used is True
    assert result.total_tokens == 100
    judge.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("judge_unavailable", [False, True])
async def test_knowledge_analysis_counts_primary_and_judge_usage(monkeypatch, judge_unavailable):
    primary = AsyncMock(return_value=SimpleNamespace(
        content="Grounded analysis",
        profile={"provider": "test", "model": "analysis"},
        fallback_used=False,
        usage={"prompt_tokens": 2000, "completion_tokens": 476, "total_tokens": 2476},
    ))
    reported = {"prompt_tokens": 2500, "completion_tokens": 785, "total_tokens": 3285}
    judge = (
        AsyncMock(side_effect=AllModelsUnavailableError([], reported))
        if judge_unavailable else AsyncMock(return_value=SimpleNamespace(
            content='{"layers": [{"name": "编码层", "modules": [{"name": "频域编码"}]}]}',
            usage=reported,
        ))
    )
    monkeypatch.setattr(knowledge_api, "chat_with_fallback", primary)
    monkeypatch.setattr(architecture_judge, "chat_with_fallback", judge)

    result = await knowledge_api.ai_analyze_research(
        AIAnalyzeRequest(knowledge_ids=["knowledge-1"]), _KnowledgeDB(_knowledge_item()),
    )

    assert result.model_completed is True
    assert result.prompt_tokens == 4500
    assert result.completion_tokens == 1261
    assert result.total_tokens == 5761
    assert (result.architecture_json is None) is judge_unavailable
    primary.assert_awaited_once()
    judge.assert_awaited_once()


@pytest.mark.asyncio
async def test_knowledge_analysis_has_grounded_offline_fallback(monkeypatch):
    async def unavailable(**_kwargs):
        raise AllModelsUnavailableError(
            [],
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "requests": 0},
        )

    monkeypatch.setattr(knowledge_api, "chat_with_fallback", unavailable)
    result = await knowledge_api.ai_analyze_research(
        AIAnalyzeRequest(knowledge_ids=["knowledge-1"]),
        _KnowledgeDB(_knowledge_item()),
    )

    assert result.model_completed is False
    assert "Grounded retrieval" in result.analysis
    assert "不新增论文" in result.analysis


@pytest.mark.asyncio
async def test_recommendation_uses_its_own_task_and_refuses_fabrication(monkeypatch):
    async def unavailable(**kwargs):
        assert kwargs["task"] == "recommendation"
        raise AllModelsUnavailableError(
            [],
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "requests": 0},
        )

    monkeypatch.setattr(knowledge_api, "chat_with_fallback", unavailable)
    result = await knowledge_api.recommend_papers(
        RecommendRequest(knowledge_ids=["knowledge-1"], limit=5),
        _KnowledgeDB(_knowledge_item()),
    )

    assert result.model_completed is False
    assert "不会在缺少学术检索结果时编造" in result.recommendations
    assert "citation verification" in result.recommendations

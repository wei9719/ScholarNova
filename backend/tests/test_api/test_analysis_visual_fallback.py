"""Visual service failures must not be reported as missing document figures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import analysis
from app.schemas.query import AnalysisRequest
from app.services.inference import AllModelsUnavailableError


@pytest.mark.asyncio
@pytest.mark.parametrize("visual_count,vision_fails,text_fails", [
    (2, True, False), (2, True, True), (2, False, False), (0, False, False),
])
async def test_visual_coverage_discloses_fallback_without_raw_provider_error(
    monkeypatch, visual_count, vision_fails, text_fails,
):
    usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    prior_note = "正文仅提供章节摘录"
    monkeypatch.setattr(analysis, "_find_paper_info", AsyncMock(return_value={
        "title": "Visual fixture", "authors": "Researcher", "year": 2026,
        "venue": "Fixture", "abstract": "Supported abstract.",
    }))
    monkeypatch.setattr(analysis, "_load_document_context", AsyncMock(return_value=(
        "Figure 1 caption and verified text.",
        ["data:image/jpeg;base64,AA=="] * visual_count, "fulltext:uploaded", prior_note,
    )))
    monkeypatch.setattr(analysis, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.config.get_model_for_task", lambda _task: {
        "provider": "fake", "model": "fake-vision", "api_key": "", "base_url": "",
    })

    vision_calls = []

    class VisionGateway:
        provider = "fake"

        def __init__(self, **_kwargs):
            self.usage = {key: 0 for key in usage}
            self.last_usage = usage

        def configure(self, **_kwargs):
            pass

        async def chat(self, **kwargs):
            vision_calls.append(kwargs)
            assert len(kwargs["messages"][1]["content"]) == visual_count + 1
            if vision_fails:
                raise RuntimeError("429 raw_provider_response PRIVATE_KEY private_endpoint")
            return "图中有三个蓝色圆形。"

    monkeypatch.setattr("app.services.llm.gateway.LLMGateway", VisionGateway)
    routed = AsyncMock(side_effect=AllModelsUnavailableError([], usage) if text_fails else None)
    routed.return_value = SimpleNamespace(content="仅依据文字回答，未读取图片。", usage=usage)
    monkeypatch.setattr(analysis, "chat_with_fallback", routed)

    result = await analysis.analyze_paper(
        "visual-fixture", AnalysisRequest(query="描述图一的形状与颜色"), None,
        SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
    )
    assert result.document_coverage == "fulltext"
    assert result.model_completed is (not text_fails)
    assert result.visual_pages_read == (visual_count if not vision_fails else 0)
    assert result.total_tokens == 5
    assert prior_note in result.document_error
    assert len(vision_calls) == (1 if visual_count else 0)
    assert routed.await_count == (1 if vision_fails or not visual_count else 0)
    if vision_fails:
        assert f"已提取 {visual_count} 个图表页面，但视觉模型未完成读取" in result.document_error
        text_prompt = routed.await_args.kwargs["messages"][1]["content"]
        assert isinstance(text_prompt, str)
        assert "本次仅依据正文、图注和表格文字分析" in text_prompt
        assert "另附" not in text_prompt
        if not text_fails:
            assert result.document_error in result.summary
    else:
        assert "视觉模型未完成" not in result.document_error
    assert "PRIVATE_KEY" not in result.model_dump_json()
    assert "raw_provider_response" not in result.model_dump_json()
    assert "private_endpoint" not in result.model_dump_json()

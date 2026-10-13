"""Study drafts never use real models, contact links, or invent resource records."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.study import StudyDraftRequest, StudySource
from app.services import study
from app.services.inference import AllModelsUnavailableError
from app.services.llm.gateway import LLMGateway


@pytest.fixture(autouse=True)
def forbid_real_gateway(monkeypatch):
    monkeypatch.setattr(LLMGateway, "chat", AsyncMock(side_effect=AssertionError("No real model in study tests")))


@pytest.fixture
def material():
    return study.StudyMaterial(
        "Local paper", [StudySource(
            label="S1", id="chunk-one", content_hash="a" * 64,
            section="Experiments", page=4,
            excerpt="We evaluated two settings. Code: https://example.org/code. Contact: author+paper@example.org.",
        )], "fulltext_excerpt", ["本次仅使用节选，未联网核验。"],
    )


def result(content="材料报告了两种设置 [S1]。\n\nAI学习/操作建议：先核对运行环境。", **kwargs):
    return SimpleNamespace(
        content=content, profile={"provider": "fake", "model": "mock-model"},
        usage=kwargs.get("usage", {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}),
        fallback_used=kwargs.get("fallback_used", False),
    )


@pytest.mark.parametrize("fields", [
    {"paper_id": "", "section": "overview"},
    {"paper_id": "p", "section": "all"},
    {"paper_id": "p", "section": ["overview", "math"]},
    {"paper_id": "p", "section": "math", "question": "x" * 2001},
    {"paper_id": "p", "section": "math", "download_pdf": True},
])
def test_invalid_requests_rejected(fields):
    with pytest.raises(ValidationError):
        StudyDraftRequest(**fields)


@pytest.mark.asyncio
async def test_resources_are_literal_local_extraction_without_model(material, monkeypatch):
    model = AsyncMock(side_effect=AssertionError("Resources must not call a model"))
    monkeypatch.setattr(study, "chat_with_fallback", model)
    draft = await study.draft_study_section(
        StudyDraftRequest(paper_id="p", section="resources", question="Invent github.com/new-repo"), material,
    )
    model.assert_not_called()
    assert "`https://example.org/code` [S1]" in draft.content
    assert "`author+paper@example.org` [S1]" in draft.content
    assert "github.com/new-repo" not in draft.content
    assert "Experiments，解析页码 4" in draft.content
    assert "短引文" in draft.content
    assert "未联网验证" in draft.content
    assert "无需调用模型" in draft.content
    assert not draft.model_completed
    assert draft.provider is draft.model is None
    assert draft.total_tokens == draft.prompt_tokens == draft.completion_tokens == 0
    assert draft.sources[0].id == "chunk-one"
    assert draft.sources[0].content_hash == "a" * 64


@pytest.mark.asyncio
async def test_resources_absent_means_not_found_not_unpublished(material, monkeypatch):
    material.sources[0].excerpt = "No resource strings occur in this excerpt. fake_at_mail and fakedlmmessages are not emails."
    monkeypatch.setattr(study, "chat_with_fallback", AsyncMock(side_effect=AssertionError))
    draft = await study.draft_study_section(StudyDraftRequest(paper_id="p", section="resources"), material)
    assert "本次已读取材料未发现" in draft.content
    assert "不代表论文没有公开资源" in draft.content
    assert "邮箱：`" not in draft.content


@pytest.mark.asyncio
@pytest.mark.parametrize("section", [
    "overview", "experiments", "math", "prerequisites", "writing", "figures", "translation", "formatting",
])
async def test_one_section_bounded_model_call_with_untrusted_sources(section, material, monkeypatch):
    model = AsyncMock(return_value=result())
    monkeypatch.setattr(study, "chat_with_fallback", model)
    draft = await study.draft_study_section(
        StudyDraftRequest(paper_id="p", section=section, question="Explain the supplied evidence."), material,
    )
    assert draft.model_completed
    assert draft.total_tokens == 120
    assert draft.provider == "fake"
    call = model.call_args.kwargs
    assert call["task"] == "analysis"
    assert call["max_tokens"] == 1500
    assert call["timeout_seconds"] == 30
    system = call["messages"][0]["content"]
    assert "不可信" in system
    assert "不替用户填写个人想法" in system
    assert "不得补造实验、数值、数学推导、邮箱、GitHub或数据链接" in system
    payload = json.loads(call["messages"][1]["content"])
    assert payload["section"] == section
    assert payload["sources"] == [{
        "label": "S1", "section": "Experiments", "page": 4,
        "excerpt": material.sources[0].excerpt,
    }]
    assert "人工核对" in " ".join(draft.warnings)


@pytest.mark.asyncio
async def test_math_does_not_ask_model_to_invent_missing_derivation(material, monkeypatch):
    model = AsyncMock(return_value=result("本次材料未提供公式，无法核对推导 [S1]。"))
    monkeypatch.setattr(study, "chat_with_fallback", model)
    await study.draft_study_section(StudyDraftRequest(paper_id="p", section="math"), material)
    prompt = model.call_args.kwargs["messages"][0]["content"]
    assert "未提供公式时明确缺少证据" in prompt


@pytest.mark.asyncio
async def test_invented_links_and_emails_are_removed(material, monkeypatch):
    model = AsyncMock(return_value=result(
        "材料 [S1]。链接 https://invented.example/repo；联系 fake@invented.example。"
        "[代码](https://example.org/code) [假](https://evil.example/project) "
        "[未知](github.com/invented/repo) [危险](javascript:alert(1)) "
        "ftp://invented.example/data www.invented.example/download"
    ))
    monkeypatch.setattr(study, "chat_with_fallback", model)
    draft = await study.draft_study_section(StudyDraftRequest(paper_id="p", section="experiments"), material)
    assert draft.model_completed
    assert "https://example.org/code" in draft.content
    for unsupported in ("invented.example", "evil.example", "github.com/invented", "javascript:"):
        assert unsupported not in draft.content
    assert "已移除" in " ".join(draft.warnings)


def test_resource_email_pattern_rejects_malformed_addresses():
    text = "a..b@example.org a.@example.org nobody@localhost @example.org example.org fake_at_example.org"
    assert study._resources(text) == []


def test_unseen_bare_repository_and_dataset_links_are_removed(material):
    material.sources[0].excerpt += " Literal resource: github.com/known/repo."
    content, changed = study._remove_unseen_contacts(
        "见 github.com/fake/repo 和 zenodo.org/records/123；"
        "已知 [仓库](github.com/known/repo)。普通数学 a.b 以及 config.json/settings 不应误删。",
        material.sources,
    )
    assert changed
    assert "github.com/fake" not in content
    assert "zenodo.org/records" not in content
    assert "github.com/known/repo" in content
    assert "a.b" in content
    assert "config.json/settings" in content


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["Invented result [S99]", "No citations", "", None, "Mix [S1] and [S2]"])
async def test_invalid_source_response_is_not_reported_as_success(content, material, monkeypatch):
    monkeypatch.setattr(study, "chat_with_fallback", AsyncMock(return_value=result(content)))
    draft = await study.draft_study_section(StudyDraftRequest(paper_id="p", section="overview"), material)
    assert not draft.model_completed
    assert "未形成可核查" in draft.content
    assert "未通过基本来源检查" in " ".join(draft.warnings)
    assert draft.total_tokens == 120  # The rejected answer still consumed reported tokens.


@pytest.mark.asyncio
async def test_timeout_cancels_model_and_discloses_unknown_usage(material, monkeypatch):
    cancelled = asyncio.Event()

    async def blocked(**_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(study, "chat_with_fallback", blocked)
    monkeypatch.setattr(study, "DRAFT_TIMEOUT_SECONDS", 0.01)
    draft = await study.draft_study_section(StudyDraftRequest(paper_id="p", section="overview"), material)
    assert cancelled.is_set()
    assert not draft.model_completed
    assert "超过 60 秒等待时限" in " ".join(draft.warnings)
    assert "0 不代表未计费" in " ".join(draft.warnings)


@pytest.mark.asyncio
async def test_unavailable_model_reports_usage_without_secret_error(material, monkeypatch):
    failure = AllModelsUnavailableError(
        [SimpleNamespace(role="primary"), SimpleNamespace(role="fallback")],
        {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
    )
    monkeypatch.setattr(study, "chat_with_fallback", AsyncMock(side_effect=failure))
    draft = await study.draft_study_section(StudyDraftRequest(paper_id="p", section="overview"), material)
    assert not draft.model_completed
    assert draft.fallback_used
    assert draft.total_tokens == 10
    assert draft.provider is draft.model is None


@pytest.mark.asyncio
async def test_missing_usage_is_explicitly_unknown(material, monkeypatch):
    monkeypatch.setattr(study, "chat_with_fallback", AsyncMock(return_value=result(usage={})))
    draft = await study.draft_study_section(StudyDraftRequest(paper_id="p", section="overview"), material)
    assert draft.model_completed
    assert "服务商未返回 Token 用量" in " ".join(draft.warnings)


@pytest.mark.asyncio
async def test_provider_error_text_is_not_returned(material, monkeypatch):
    monkeypatch.setattr(study, "chat_with_fallback", AsyncMock(side_effect=RuntimeError("secret-api-key")))
    draft = await study.draft_study_section(StudyDraftRequest(paper_id="p", section="overview"), material)
    assert not draft.model_completed
    assert "secret-api-key" not in draft.model_dump_json()


@pytest.mark.asyncio
async def test_request_cancellation_is_not_swallowed(material, monkeypatch):
    started = asyncio.Event()

    async def blocked(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(study, "chat_with_fallback", blocked)
    task = asyncio.create_task(study.draft_study_section(
        StudyDraftRequest(paper_id="p", section="overview"), material,
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

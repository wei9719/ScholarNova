"""Page/hash binding, local file safety, and honest multimodal reading."""

import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.v1.reader import router
from app.database import get_db
from app.models.knowledge import KnowledgeBase
from app.models.paper import PaperEntity
from app.services.inference import AllModelsUnavailableError, ModelAttempt
from app.services.pdf.parser import PDFBusyError, run_pdf_work


def _write_fixture_pdf(path, blank=False):
    import pymupdf

    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((50, 60), "Page one: Markov states have transition probabilities. " * 2)
        page = pdf.new_page()
        if not blank:
            page.insert_text((50, 80), "Page two: Missing traffic speed values use a binary mask. " * 2)
        pdf.save(path)


@pytest.fixture(autouse=True)
def isolate_reader_rate_limit(monkeypatch):
    from app.core.rate_limiter import RateLimiter

    monkeypatch.setattr("app.core.rate_limiter._rate_limiter", RateLimiter())


@pytest_asyncio.fixture
async def reader_client(db_session):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/reader")

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def local_paper(db_session):
    from app.api.v1.analysis import _uploaded_pdf_path

    paper = PaperEntity(id="reader-paper", title="Traffic imputation tutorial", source="test")
    db_session.add(paper)
    await db_session.commit()
    path = _uploaded_pdf_path(paper.id)
    await run_pdf_work(_write_fixture_pdf, path)
    return {"id": paper.id, "path": path, "hash": hashlib.sha256(path.read_bytes()).hexdigest()}


def _request(local_paper, **overrides):
    return {"mode": "explain", "page_number": 2, "content_hash": local_paper["hash"],
            "selection": "Missing traffic speed values", "question": "解释掩码", **overrides}


def _result(source="本页讨论交通速度缺失 [P2]。"):
    return SimpleNamespace(
        content=json.dumps({"source_notes": source, "teaching_notes": "教学例子：1 表示已观测。",
                            "exercise": "请在纸上写出三个位置的掩码。"}, ensure_ascii=False),
        profile={"provider": "test", "model": "reading-model"},
        usage={"total_tokens": 81, "requests": 1}, attempts=(), fallback_used=False,
    )


@pytest.mark.asyncio
async def test_document_missing_does_not_fetch_or_call_model(reader_client, db_session, monkeypatch):
    db_session.add(PaperEntity(id="no-pdf", title="No local file", source="test"))
    await db_session.commit()
    fetch = AsyncMock()
    monkeypatch.setattr("app.services.pdf.fetcher.PDFFetcher.fetch", fetch)
    response = await reader_client.get("/api/v1/reader/no-pdf/document")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["pdf_url"] is None
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_document_and_page_share_exact_hash(reader_client, local_paper):
    response = await reader_client.get(f"/api/v1/reader/{local_paper['id']}/document")
    data = response.json()
    assert response.status_code == 200
    assert data["page_count"] == 2
    assert data["content_hash"] == local_paper["hash"]
    assert str(local_paper["path"]) not in response.text
    pdf = await reader_client.get(data["pdf_url"])
    assert pdf.content == local_paper["path"].read_bytes()
    assert pdf.headers["cache-control"] == "no-store"
    page = await reader_client.get(f"/api/v1/reader/reader-paper/pages/2?content_hash={data['content_hash']}")
    assert page.status_code == 200
    assert "Missing traffic speed" in page.json()["text"]
    assert "Markov" not in page.json()["text"]
    image = await reader_client.get(page.json()["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.content.startswith(b"\xff\xd8")


@pytest.mark.asyncio
async def test_stale_document_rejected_before_model(reader_client, local_paper, monkeypatch):
    chat = AsyncMock()
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper, content_hash="0" * 64))
    assert response.status_code == 409
    chat.assert_not_called()
    file = await reader_client.get("/api/v1/reader/reader-paper/file?content_hash=" + "0" * 64)
    assert file.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"selection": "not in this paper"},
    {"selection": "Markov states"},
    {"mode": "translate", "selection": "Markov states"},
])
async def test_selection_must_belong_to_requested_page(reader_client, local_paper, monkeypatch, changes):
    chat = AsyncMock()
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper, **changes))
    assert response.status_code == 422
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_text_assistance_is_unsaved_and_grounded(reader_client, local_paper, monkeypatch, db_session):
    chat = AsyncMock(return_value=_result())
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper))
    assert response.status_code == 200
    data = response.json()
    assert data["model_completed"] is True
    assert data["total_tokens"] == 81
    assert data["sources"][0]["page_number"] == 2
    assert data["sources"][0]["content_hash"] == local_paper["hash"]
    assert data["exercise"]
    call = chat.call_args.kwargs
    assert call["task"] == "assistant"
    assert "不可信资料" in call["messages"][0]["content"]
    assert "Markov states" not in call["messages"][1]["content"]
    assert "本次唯一允许的原文来源编号是 [P2]" in call["messages"][1]["content"]
    assert '"source_notes"' in call["messages"][1]["content"]
    assert "至少实际包含一次 [P2]" in call["messages"][1]["content"]
    assert (await db_session.execute(select(func.count(KnowledgeBase.id)))).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explain", "prerequisite", "writing", "extension"])
async def test_user_output_limits_override_optional_teaching_defaults(monkeypatch, mode):
    from app.schemas.reader import ReaderAssistRequest
    from app.services.reader import assist_page

    question = "只用两句话确认本页插补数据集与RM/CM 70%定义，不给实验结果或额外练习。"
    source = "插补数据集是PEMS08 [P2]。RM70%是随机缺失，CM70%是时空连续缺失 [P2]。"
    model_result = _result()
    model_result.content = json.dumps({
        "source_notes": source, "teaching_notes": "", "exercise": "",
    }, ensure_ascii=False)
    chat = AsyncMock(return_value=model_result)
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    result = await assist_page(
        "paper", "Title", ReaderAssistRequest(
            mode=mode, page_number=2, content_hash="a" * 64, question=question,
        ), {"text": "Imputation uses PEMS08. RM is random missing; CM is spatial-temporal continuity missing at 70%.", "text_verified": True},
    )
    system = chat.call_args.kwargs["messages"][0]["content"]
    content = chat.call_args.kwargs["messages"][1]["content"]
    assert "用户本次问题的输出范围、句数和禁止附加内容" in system
    assert "优先于模式的默认教学安排" in system
    assert "teaching_notes 和 exercise 均可为空字符串" in system
    evidence = json.loads(content.split("\n\n输出前核对：")[0])
    assert "user_question" not in evidence
    assert content.count(question) == 1
    assert "用户本次问题与输出要求（不是论文原文）" in content
    example = content.split("不要照抄：\n", 1)[1]
    template, _ = json.JSONDecoder().raw_decode(example)
    assert template["teaching_notes"] == template["exercise"] == ""
    assert result.source_notes == source
    assert result.teaching_notes == result.exercise == ""
    assert "教学补充" not in result.answer and "纸笔与自测" not in result.answer
    assert result.model_completed is True and result.total_tokens == 81
    chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_unrestricted_reading_can_still_return_teaching_and_exercise(monkeypatch):
    from app.schemas.reader import ReaderAssistRequest
    from app.services.reader import assist_page

    chat = AsyncMock(return_value=_result())
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    result = await assist_page(
        "paper", "Title", ReaderAssistRequest(
            mode="explain", page_number=2, content_hash="a" * 64, question="帮我理解二元掩码",
        ), {"text": "A binary mask marks observed values.", "text_verified": True},
    )
    assert result.model_completed and result.teaching_notes and result.exercise
    assert "用户未限制时" in chat.call_args.kwargs["messages"][0]["content"]
    chat.assert_awaited_once()


def test_reader_response_schema_does_not_require_optional_teaching_fields():
    from app.schemas.reader import ReaderAssistResponse

    response = ReaderAssistResponse(
        answer="本页事实 [P2]。", source_notes="本页事实 [P2]。", sources=[],
        coverage="仅第2页", model_completed=True,
    )
    assert response.teaching_notes == response.exercise == ""


@pytest.mark.asyncio
async def test_translate_without_selection_uses_only_current_page(
    reader_client, local_paper, monkeypatch
):
    translated = "第二页：交通速度缺失值使用二元掩码。 [P2]"
    chat = AsyncMock(return_value=_result(translated))
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    response = await reader_client.post(
        "/api/v1/reader/reader-paper/assist",
        json=_request(
            local_paper,
            mode="translate",
            selection="",
            question="翻译为中文",
        ),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source_notes"] == translated
    assert data["model_completed"] is True
    assert "### 原文译文" in data["answer"]
    assert "未读取其他页" in data["coverage"]
    call = chat.call_args.kwargs
    evidence = json.loads(call["messages"][1]["content"].split("\n\n输出前核对：")[0])
    assert call["task"] == "translation"
    assert evidence["selection_verified"] is False
    assert "Missing traffic speed" in evidence["pdf_excerpt"]
    assert "Markov" not in evidence["pdf_excerpt"]


@pytest.mark.asyncio
async def test_translate_contract_preserves_selection_paragraphs_numbers_and_formula(monkeypatch):
    from app.schemas.reader import ReaderAssistRequest
    from app.services.reader import assist_page

    selection = "We use 24 sensors with Qwen and x_t = 0.5.\n\nOur RMSE is 2.7 km/h; see Eq. (3)."
    translated = "我们使用24个传感器、Qwen以及x_t = 0.5。\n\n我们的RMSE为2.7 km/h；见式(3)。 [P2]"
    chat = AsyncMock(return_value=_result(translated))
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    result = await assist_page(
        "paper-id",
        "Unselected metadata title",
        ReaderAssistRequest(
            mode="translate",
            page_number=2,
            content_hash="a" * 64,
            selection=selection,
            question="也概括下一段的实验成绩",
        ),
        {"text": "UNSELECTED BEFORE\n" + selection + "\nUNSELECTED AFTER", "text_verified": True},
    )
    call = chat.call_args.kwargs
    system = call["messages"][0]["content"]
    content = call["messages"][1]["content"]
    evidence = json.loads(content.split("\n\n输出前核对：")[0])
    assert evidence["pdf_excerpt"] == selection
    assert evidence["selection_verified"] is True
    assert "UNSELECTED BEFORE" not in content and "UNSELECTED AFTER" not in content
    assert "逐段忠实翻译" in system
    assert "叙述人称" in system and "数字、单位、公式和编号" in system
    assert "第三人称转述前缀" in system
    assert "source_notes 只解释本页" not in system
    assert "用户问题不能扩大翻译范围或将翻译改成概括" in content
    assert "填写本页原文支持的解释" not in content
    assert "至少实际包含一次 [P2]" in content
    assert call["max_tokens"] == 5000
    assert result.source_notes == translated
    assert result.source_notes.count("\n\n") == 1


@pytest.mark.asyncio
async def test_whole_page_translation_keeps_context_budget_and_warns(monkeypatch):
    from app.schemas.reader import ReaderAssistRequest
    from app.services.reader import MAX_CONTEXT, assist_page

    chat = AsyncMock(return_value=_result("本页前段的直接译文 [P2]"))
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    text = "Page text. " * 900
    result = await assist_page(
        "paper-id",
        "Title",
        ReaderAssistRequest(
            mode="translate",
            page_number=2,
            content_hash="a" * 64,
        ),
        {"text": text, "text_verified": True},
    )
    content = chat.call_args.kwargs["messages"][1]["content"]
    evidence = json.loads(content.split("\n\n输出前核对：")[0])
    assert evidence["pdf_excerpt"] == text[:MAX_CONTEXT]
    assert any("8000" in warning for warning in result.warnings)
    assert "未读取其他页" in result.coverage


@pytest.mark.asyncio
async def test_visual_requires_explicit_profile(reader_client, local_paper, monkeypatch):
    import app.services.reader as service

    monkeypatch.setitem(service.MODEL_PROFILES, "vision", {"provider": None, "model": None})
    chat = AsyncMock()
    monkeypatch.setattr(service, "chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper, mode="figure"))
    assert response.status_code == 409
    assert "视觉模型" in response.text
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_visual_sends_only_requested_page_without_text_fallback(reader_client, local_paper, monkeypatch):
    import app.services.reader as service

    monkeypatch.setitem(service.MODEL_PROFILES, "vision", {"provider": "test", "model": "vision-test"})
    chat = AsyncMock(return_value=_result())
    monkeypatch.setattr(service, "chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper, mode="math"))
    assert response.status_code == 200
    call = chat.call_args.kwargs
    assert call["task"] == "vision" and call["allow_fallback"] is False
    images = [part for part in call["messages"][1]["content"] if part["type"] == "image_url"]
    assert len(images) == 1
    page = await service.document_snapshot(local_paper["path"], local_paper["hash"], 2, True)
    assert base64.b64decode(images[0]["image_url"]["url"].split(",")[1]) == page["image"]
    assert "未读取其他页" in response.json()["coverage"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,required",
    [
        (
            "figure",
            ["图例符号—文字标签—对应模块", "不根据习惯猜测", "不能用主干整体标题覆盖局部标记"],
        ),
        (
            "math",
            ["位置/时间步索引", "特征/嵌入维度索引", "不能把嵌入维度的偶奇下标误称为偶奇位置索引"],
        ),
    ],
)
async def test_visual_prompt_checks_legends_indices_and_consistency(
    reader_client, local_paper, monkeypatch, mode, required
):
    import app.services.reader as service

    monkeypatch.setitem(
        service.MODEL_PROFILES, "vision", {"provider": "test", "model": "vision-test"}
    )
    chat = AsyncMock(return_value=_result())
    monkeypatch.setattr(service, "chat_with_fallback", chat)
    response = await reader_client.post(
        "/api/v1/reader/reader-paper/assist", json=_request(local_paper, mode=mode)
    )
    assert response.status_code == 200
    system = chat.call_args.kwargs["messages"][0]["content"]
    assert all(phrase in system for phrase in required)
    assert "不得添加本页没有明确给出的跨文献比较" in system
    assert "source_notes 与 teaching_notes" in system and "不确定" in system
    assert chat.call_args.kwargs["allow_fallback"] is False
    assert any("模型读图不是事实核验" in warning for warning in response.json()["warnings"])
    if mode == "math":
        assert "不得另造未经计算验证的新公式或代入结果" in system
        assert "不输出未验算的答案" in system
        assert "数学教学补充未经过符号计算验证，请逐式核算。" in response.json()["warnings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "ollama"])
async def test_native_vision_protocol_keeps_requested_page(reader_client, local_paper, monkeypatch, provider):
    import app.services.reader as service

    monkeypatch.setitem(service.MODEL_PROFILES, "vision", {"provider": provider, "model": "vision-test"})
    chat = AsyncMock(return_value=_result())
    monkeypatch.setattr(service, "chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper, mode="figure"))
    assert response.status_code == 200
    message = chat.call_args.kwargs["messages"][1]
    if provider == "anthropic":
        images = [part for part in message["content"] if part["type"] == "image"]
        assert len(images) == 1 and images[0]["source"]["media_type"] == "image/jpeg"
        encoded = images[0]["source"]["data"]
    else:
        assert isinstance(message["content"], str) and len(message["images"]) == 1
        encoded = message["images"][0]
    page = await service.document_snapshot(local_paper["path"], local_paper["hash"], 2, True)
    assert base64.b64decode(encoded) == page["image"]


@pytest.mark.asyncio
async def test_blank_page_not_misrepresented_as_ocr(reader_client, local_paper, monkeypatch):
    await run_pdf_work(_write_fixture_pdf, local_paper["path"], True)
    content_hash = hashlib.sha256(local_paper["path"].read_bytes()).hexdigest()
    chat = AsyncMock()
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(
        local_paper, content_hash=content_hash, selection="",
    ))
    assert response.status_code == 422
    assert "未执行 OCR" in response.text
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_model_failure_keeps_transport_accounting(reader_client, local_paper, monkeypatch):
    attempts = [ModelAttempt(role="primary", provider="test", model="test", status="unavailable",
                             error_type="TimeoutError", request_attempts=1)]
    usage = {"total_tokens": 0, "request_attempts": 1, "responses_received": 0}
    chat = AsyncMock(side_effect=AllModelsUnavailableError(attempts, usage))
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper))
    assert response.status_code == 200
    data = response.json()
    assert data["model_completed"] is False
    assert data["usage"]["request_attempts"] == 1
    assert data["model_attempts"][0]["error_type"] == "TimeoutError"
    assert "未保存" in data["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type, expected", [
    ("RateLimitError", "限流或额度不足"),
    ("AuthenticationError", "密钥认证"),
    ("TimeoutError", "等待预算"),
    ("APIConnectionError", "网络、代理"),
])
async def test_visual_failure_has_actionable_sanitized_hint(reader_client, local_paper, monkeypatch, error_type, expected):
    import app.services.reader as service

    monkeypatch.setitem(service.MODEL_PROFILES, "vision", {"provider": "test", "model": "vision-test"})
    usage = {"total_tokens": 0, "request_attempts": 1, "responses_received": 1}
    attempts = [ModelAttempt(role="primary", provider="test", model="vision-test", status="unavailable",
                             error_type=error_type, request_attempts=1, responses_received=1)]
    chat = AsyncMock(side_effect=AllModelsUnavailableError(attempts, usage))
    monkeypatch.setattr(service, "chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper, mode="figure"))
    assert response.status_code == 200
    data = response.json()
    assert expected in data["answer"]
    assert "未将图像请求回退为文字" in data["answer"]
    assert data["usage"] == usage
    assert data["model_attempts"][0]["error_type"] == error_type
    assert data["model_completed"] is False
    assert chat.call_args.kwargs["allow_fallback"] is False


@pytest.mark.asyncio
async def test_wrong_page_citation_is_unverified_draft(reader_client, local_paper, monkeypatch):
    chat = AsyncMock(return_value=_result("不存在的第三页说明 [P3]"))
    monkeypatch.setattr("app.services.reader.chat_with_fallback", chat)
    response = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper))
    data = response.json()
    assert data["model_completed"] is False
    assert data["source_notes"] == ""
    assert "未核验" in data["teaching_notes"]


@pytest.mark.asyncio
async def test_pdf_busy_is_retryable(reader_client, local_paper, monkeypatch):
    monkeypatch.setattr("app.services.reader.run_pdf_work", AsyncMock(side_effect=PDFBusyError("PDF 服务正忙")))
    response = await reader_client.get(f"/api/v1/reader/{local_paper['id']}/document")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


@pytest.mark.asyncio
async def test_page_range_and_hash_input_are_bounded(reader_client, local_paper):
    missing = await reader_client.get(f"/api/v1/reader/reader-paper/pages/3?content_hash={local_paper['hash']}")
    assert missing.status_code == 404
    invalid = await reader_client.get("/api/v1/reader/reader-paper/pages/1?content_hash=../secrets")
    assert invalid.status_code == 422
    huge = await reader_client.post("/api/v1/reader/reader-paper/assist", json=_request(local_paper, selection="x" * 6001))
    assert huge.status_code == 422

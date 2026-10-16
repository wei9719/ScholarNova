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
    {"mode": "translate", "selection": ""},
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

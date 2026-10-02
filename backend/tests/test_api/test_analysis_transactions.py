"""PDF preparation must not hold a SQLite writer while a model is waiting."""

import asyncio
import gc
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import UploadFile

from app.api.v1 import analysis
from app.database import Base
from app.models.paper import PaperChunk, PaperEntity
from app.schemas.query import AnalysisRequest
from app.services.features import paper as features
from app.services.pdf.parser import PDFBusyError, PDFParser, ParsedDocument


@pytest_asyncio.fixture
async def file_sessions(tmp_path):
    # A file is essential: separate in-memory sessions may share one connection
    # and cannot reliably reproduce SQLite's cross-connection writer lock.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'analysis.sqlite').as_posix()}",
        connect_args={"timeout": 0.2},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[PaperEntity.__table__, PaperChunk.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(PaperEntity(id="paper", title="Test paper", abstract="Test evidence", source="test"))
        await db.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def prepared_document(monkeypatch):
    document = ParsedDocument(
        title="Test paper", abstract="", full_text="Verified paper evidence. " * 40
    )
    monkeypatch.setattr(PDFParser, "parse", AsyncMock(return_value=document))
    monkeypatch.setattr(analysis, "_render_visual_pages", lambda *_: [])
    analysis._uploaded_pdf_path("paper").write_bytes(b"%PDF-test")
    return document


@pytest.mark.asyncio
async def test_model_wait_does_not_hold_sqlite_writer(file_sessions, prepared_document, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(analysis, "check_rate_limit", lambda *args, **kwargs: None)

    async with file_sessions() as db:
        async def waiting_model(**kwargs):
            assert not db.in_transaction()
            assert not analysis._paper_preparation_lock("paper").locked()
            started.set()
            await release.wait()
            return SimpleNamespace(
                content="材料覆盖：全文。", usage={
                    "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                },
            )

        monkeypatch.setattr(analysis, "chat_with_fallback", waiting_model)
        task = asyncio.create_task(analysis.analyze_paper(
            "paper", AnalysisRequest(query="Summarize this paper"), None, db
        ))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            async with file_sessions() as other:
                other.add(PaperEntity(id="concurrent", title="Concurrent write", source="test"))
                await asyncio.wait_for(other.commit(), timeout=1)
                assert (await other.scalar(select(func.count(PaperChunk.id)))) > 0
            assert not task.done()
        finally:
            release.set()
            result = await task
        assert result.model_completed is True
        assert result.document_coverage == "fulltext"


@pytest.mark.asyncio
async def test_feature_flush_failure_rolls_back_and_keeps_fulltext(
    file_sessions, prepared_document, monkeypatch,
):
    async def invalid_features(db, paper, parsed):
        # Cause a real failed SQLAlchemy transaction, not only a raised mock.
        db.add(PaperEntity(id="invalid", title=None, source="test"))
        await db.flush()

    monkeypatch.setattr(features, "ensure_paper_features", invalid_features)
    async with file_sessions() as db:
        text, images, coverage, error = await analysis._load_document_context("paper", {}, db)
        assert text == prepared_document.full_text
        assert images == [] and coverage == "fulltext:uploaded"
        assert "IntegrityError" in error
        assert "INSERT" not in error and "invalid" not in error
        assert db.is_active
        await db.commit()
        assert await db.get(PaperEntity, "paper") is not None
        assert await db.get(PaperEntity, "invalid") is None


@pytest.mark.asyncio
async def test_preparation_commit_failure_never_calls_model(monkeypatch):
    monkeypatch.setattr(analysis, "check_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(analysis, "_find_paper_info", AsyncMock(return_value={"title": "Test"}))
    monkeypatch.setattr(
        analysis, "_load_document_context", AsyncMock(return_value=("text", [], "fulltext", None))
    )
    model = AsyncMock()
    monkeypatch.setattr(analysis, "chat_with_fallback", model)
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("commit failed")), rollback=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="commit failed"):
        await analysis.analyze_paper("paper", AnalysisRequest(query="Summary"), None, db)
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["busy", "flush", "index_commit", None])
async def test_replaced_upload_does_not_leave_old_features(file_sessions, monkeypatch, failure):
    async with file_sessions() as db:
        paper = await db.get(PaperEntity, "paper")
        await features.rebuild_paper_features(
            db, paper, ParsedDocument(title="Old", abstract="", full_text="Obsolete evidence")
        )
        await db.commit()

    monkeypatch.setattr(analysis, "_pdf_page_count", lambda _: 1)
    if failure == "busy":
        monkeypatch.setattr(PDFParser, "parse", AsyncMock(side_effect=PDFBusyError("busy")))
    else:
        monkeypatch.setattr(PDFParser, "parse", AsyncMock(return_value=ParsedDocument(
            title="New", abstract="", full_text="Replacement evidence"
        )))

        if failure == "flush":
            async def invalid_rebuild(db, paper, parsed):
                db.add(PaperEntity(id="invalid", title=None, source="test"))
                await db.flush()

            monkeypatch.setattr(features, "rebuild_paper_features", invalid_rebuild)

    async with file_sessions() as db:
        if failure == "index_commit":
            commit = db.commit
            calls = 0

            async def fail_index_commit():
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("sensitive database detail")
                await commit()

            monkeypatch.setattr(db, "commit", fail_index_commit)
        result = await analysis.upload_fulltext(
            "paper", UploadFile(filename="replacement.pdf", file=BytesIO(b"%PDF-new")), db,
        )
        assert result["available"] is True
        assert result["feature_count"] == (0 if failure else 1)
        if failure:
            assert "PDF 已保存" in result["feature_error"]
            assert "INSERT" not in result["feature_error"]
            assert "sensitive" not in result["feature_error"]
        else:
            assert result["feature_error"] is None
        assert db.is_active
        await db.commit()

    # A fresh connection must see the old index deletion, even after rollback.
    async with file_sessions() as db:
        status = await analysis.get_fulltext_status("paper", db)
        assert status["available"] is True
        assert status["feature_count"] == (0 if failure else 1)
        assert await db.get(PaperEntity, "invalid") is None
        contents = (await db.scalars(select(PaperChunk.content))).all()
        assert "Obsolete evidence" not in contents
        if not failure:
            assert contents == ["Replacement evidence"]
    assert analysis._uploaded_pdf_path("paper").read_bytes() == b"%PDF-new"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["commit", "cancel", "replace"])
async def test_upload_publication_failure_preserves_old_pdf(
    file_sessions, monkeypatch, failure,
):
    async with file_sessions() as db:
        paper = await db.get(PaperEntity, "paper")
        await features.rebuild_paper_features(
            db, paper, ParsedDocument(title="Old", abstract="", full_text="Old evidence")
        )
        await db.commit()

    pdf_path = analysis._uploaded_pdf_path("paper")
    pdf_path.write_bytes(b"%PDF-original")
    # Cleanup must never touch a staged file owned by another upload.
    other_staged = pdf_path.with_name(f".{pdf_path.stem}.other.tmp")
    other_staged.write_bytes(b"other request")
    monkeypatch.setattr(analysis, "_pdf_page_count", lambda _: 1)
    parser = AsyncMock()
    monkeypatch.setattr(PDFParser, "parse", parser)

    async with file_sessions() as db:
        if failure == "replace":
            original_replace = Path.replace

            def failed_replace(path, target):
                if target == pdf_path:
                    raise PermissionError("source or destination open")
                return original_replace(path, target)

            monkeypatch.setattr(Path, "replace", failed_replace)
            expected_error = HTTPException
        else:
            expected_error = RuntimeError if failure == "commit" else asyncio.CancelledError

            async def failed_commit():
                assert pdf_path.read_bytes() == b"%PDF-original"
                staged = list(pdf_path.parent.glob(f".{pdf_path.stem}.*.tmp"))
                assert len(staged) == 2
                raise expected_error("commit interrupted")

            monkeypatch.setattr(db, "commit", failed_commit)

        with pytest.raises(expected_error) as raised:
            await analysis.upload_fulltext(
                "paper", UploadFile(filename="new.pdf", file=BytesIO(b"%PDF-new")), db,
            )
        if failure == "replace":
            assert raised.value.status_code == 500
            assert "原文件未更新" in raised.value.detail
            assert "检索索引已清除" in raised.value.detail
        await db.rollback()

    parser.assert_not_awaited()
    assert pdf_path.read_bytes() == b"%PDF-original"
    assert list(pdf_path.parent.glob(f".{pdf_path.stem}.*.tmp")) == [other_staged]
    async with file_sessions() as db:
        contents = (await db.scalars(select(PaperChunk.content))).all()
        assert contents == ([] if failure == "replace" else ["Old evidence"])


@pytest.mark.asyncio
async def test_upload_cannot_be_overwritten_by_older_analysis_features(file_sessions, monkeypatch):
    old_read, release_old = asyncio.Event(), asyncio.Event()
    path = analysis._uploaded_pdf_path("paper")
    path.write_bytes(b"%PDF-old")

    async def parse(_parser, pdf_path):
        source = pdf_path.read_bytes()
        if source == b"%PDF-old":
            old_read.set()
            await release_old.wait()
        return ParsedDocument(
            title="Test", abstract="",
            full_text=("Old evidence. " if source == b"%PDF-old" else "New evidence. ") * 60,
        )

    monkeypatch.setattr(PDFParser, "parse", parse)
    monkeypatch.setattr(analysis, "_pdf_page_count", lambda _: 1)
    monkeypatch.setattr(analysis, "_render_visual_pages", lambda *_: [])
    monkeypatch.setattr(analysis, "check_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(analysis, "chat_with_fallback", AsyncMock(return_value=SimpleNamespace(
        content="Analysis", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )))

    async with file_sessions() as old_db, file_sessions() as new_db:
        old_task = asyncio.create_task(analysis.analyze_paper(
            "paper", AnalysisRequest(query="Summary"), None, old_db,
        ))
        try:
            await asyncio.wait_for(old_read.wait(), timeout=3)
            new_task = asyncio.create_task(analysis.upload_fulltext(
                "paper", UploadFile(filename="new.pdf", file=BytesIO(b"%PDF-new")), new_db,
            ))
            # Upload must wait for the old preparation to commit. This used to
            # publish B first, then let the paused A analysis restore A's index.
            await asyncio.sleep(0.05)
            assert not new_task.done()
            assert path.read_bytes() == b"%PDF-old"
        finally:
            release_old.set()
            await old_task
        result = await asyncio.wait_for(new_task, timeout=3)
        assert result["feature_error"] is None
        assert not new_db.in_transaction()

    async with file_sessions() as db:
        contents = (await db.scalars(select(PaperChunk.content))).all()
        assert contents and all("New evidence" in content for content in contents)
        assert not any("Old evidence" in content for content in contents)
    assert path.read_bytes() == b"%PDF-new"


@pytest.mark.asyncio
async def test_cancelled_preparation_releases_writer_and_paper_lock(
    file_sessions, prepared_document, monkeypatch,
):
    written = asyncio.Event()
    ensure = features.ensure_paper_features

    async def blocked_features(db, paper, parsed):
        await ensure(db, paper, parsed)
        written.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(features, "ensure_paper_features", blocked_features)
    monkeypatch.setattr(analysis, "check_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(analysis, "_pdf_page_count", lambda _: 1)
    model = AsyncMock()
    monkeypatch.setattr(analysis, "chat_with_fallback", model)
    async with file_sessions() as db:
        task = asyncio.create_task(analysis.analyze_paper(
            "paper", AnalysisRequest(query="Summary"), None, db,
        ))
        try:
            await asyncio.wait_for(written.wait(), timeout=3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not db.in_transaction()
    model.assert_not_awaited()

    async with file_sessions() as db:
        result = await asyncio.wait_for(analysis.upload_fulltext(
            "paper", UploadFile(filename="new.pdf", file=BytesIO(b"%PDF-new")), db,
        ), timeout=3)
        assert result["feature_count"] > 0 and result["feature_error"] is None
        assert not db.in_transaction()


@pytest.mark.asyncio
async def test_paper_locks_are_independent_and_idle_ids_are_released():
    async def use_locks():
        first = analysis._paper_preparation_lock("lock-test-first")
        waiting = asyncio.Event()

        async def wait_same_paper():
            waiting.set()
            async with analysis._paper_preparation_lock("lock-test-first"):
                raise AssertionError("Cancelled waiter must not enter")

        async with first:
            assert analysis._paper_preparation_lock("lock-test-first") is first
            task = asyncio.create_task(wait_same_paper())
            await waiting.wait()
            async with analysis._paper_preparation_lock("lock-test-second"):
                assert first.locked()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert first.locked()
        async with first:
            pass

    await use_locks()
    # Let cancellation callbacks and their tracebacks release request frames.
    await asyncio.sleep(0)
    gc.collect()
    assert "lock-test-first" not in analysis._paper_preparation_locks
    assert "lock-test-second" not in analysis._paper_preparation_locks

"""PDF work is bounded and serialized independently from asyncio cancellation."""

import asyncio
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.v1 import analysis
from app.services.pdf import parser as pdf


@pytest.fixture(autouse=True)
def isolated_worker(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-pdf")
    monkeypatch.setattr(pdf, "_PDF_EXECUTOR", executor)
    monkeypatch.setattr(pdf, "_PDF_CAPACITY", threading.BoundedSemaphore(1))
    yield executor
    executor.shutdown(wait=True, cancel_futures=True)


async def wait_started(event):
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.001)


async def wait_recovered():
    async with asyncio.timeout(3):
        while True:
            try:
                return await pdf.run_pdf_work(lambda: "recovered")
            except pdf.PDFBusyError:
                await asyncio.sleep(0.001)


def blocking_work(started, release):
    started.set()
    assert release.wait(3), "test did not release the worker"
    return "done"


@pytest.mark.asyncio
async def test_busy_worker_does_not_block_event_loop_or_accept_unbounded_work(monkeypatch):
    started, release = threading.Event(), threading.Event()
    parser = pdf.PDFParser()
    calls = []

    def parse_sync(path):
        calls.append((path, threading.get_ident()))
        return blocking_work(started, release)

    monkeypatch.setattr(parser, "_parse_sync", parse_sync)
    task = asyncio.create_task(parser.parse("论文.pdf"))
    try:
        await wait_started(started)
        # This timer must run while the synchronous worker is blocked.
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        attempts = await asyncio.gather(
            *(pdf.PDFParser().parse(f"{index}.pdf") for index in range(20)),
            return_exceptions=True,
        )
        assert all(isinstance(result, pdf.PDFBusyError) for result in attempts)
        assert len(calls) == 1
        assert calls[0][1] != threading.get_ident()
        assert not task.done()
    finally:
        release.set()
        await task
    assert await wait_recovered() == "recovered"


@pytest.mark.asyncio
@pytest.mark.parametrize("timed_out", [False, True])
async def test_cancel_or_timeout_keeps_real_capacity_until_worker_finishes(timed_out):
    started, release = threading.Event(), threading.Event()
    task = asyncio.create_task(pdf.run_pdf_work(blocking_work, started, release))
    try:
        await wait_started(started)
        if timed_out:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(task, timeout=0.01)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(pdf.PDFBusyError):
            await pdf.run_pdf_work(lambda: None)
        with pytest.raises(pdf.PDFBusyError):
            pdf.run_pdf_work_sync(lambda: None)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert await wait_recovered() == "recovered"


@pytest.mark.asyncio
async def test_submit_failure_does_not_leak_capacity(isolated_worker, monkeypatch):
    original = isolated_worker.submit

    def fail(*args, **kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(isolated_worker, "submit", fail)
    with pytest.raises(RuntimeError, match="executor unavailable"):
        await pdf.run_pdf_work(lambda: None)
    monkeypatch.setattr(isolated_worker, "submit", original)
    assert await wait_recovered() == "recovered"


@pytest.mark.asyncio
async def test_parse_failure_closes_document_and_recovers(tmp_path, monkeypatch):
    path = tmp_path / "坏文档.pdf"
    path.touch()
    closed = threading.Event()
    document = SimpleNamespace(close=closed.set)
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda _: document))
    parser = pdf.PDFParser()

    def fail(_document):
        raise ValueError("cannot extract")

    monkeypatch.setattr(parser, "_extract_document", fail)
    assert await parser.parse(path) is None
    assert closed.is_set()
    assert await wait_recovered() == "recovered"


def mock_download(monkeypatch, tmp_path):
    real_temporary_file = tempfile.NamedTemporaryFile
    monkeypatch.setattr(
        tempfile,
        "NamedTemporaryFile",
        lambda **kwargs: real_temporary_file(dir=tmp_path, **kwargs),
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return SimpleNamespace(content=b"%PDF-test", raise_for_status=lambda: None)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_url_cancellation_keeps_file_until_worker_finishes(tmp_path, monkeypatch):
    mock_download(monkeypatch, tmp_path)
    started, release = threading.Event(), threading.Event()
    parser = pdf.PDFParser()
    paths = []

    def parse_sync(path):
        paths.append(path)
        return blocking_work(started, release)

    monkeypatch.setattr(parser, "_parse_sync", parse_sync)
    task = asyncio.create_task(parser.parse_from_url("https://example.invalid/paper.pdf"))
    try:
        await wait_started(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert paths[0].exists()
        with pytest.raises(pdf.PDFBusyError):
            await parser.parse("another.pdf")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    await wait_recovered()
    assert not paths[0].exists()


@pytest.mark.asyncio
async def test_url_busy_rejection_cleans_unsubmitted_file(tmp_path, monkeypatch):
    mock_download(monkeypatch, tmp_path)
    started, release = threading.Event(), threading.Event()
    task = asyncio.create_task(pdf.run_pdf_work(blocking_work, started, release))
    try:
        await wait_started(started)
        with pytest.raises(pdf.PDFBusyError):
            await pdf.PDFParser().parse_from_url("https://example.invalid/paper.pdf")
        assert not list(tmp_path.glob("*.pdf"))
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_extract_text_compatibility_and_missing_file(tmp_path, monkeypatch):
    assert await pdf.PDFParser().parse(tmp_path / "missing.pdf") is None
    parser = pdf.PDFParser()
    monkeypatch.setattr(
        parser, "_parse_sync", lambda _: pdf.ParsedDocument("title", "", full_text="正文")
    )
    assert await parser.extract_text(tmp_path / "paper.pdf") == "正文"


@pytest.mark.asyncio
async def test_all_real_pdf_operations_use_same_non_main_thread(tmp_path, monkeypatch):
    import pymupdf

    thread_ids = []
    original_open = pymupdf.open

    def tracked_open(*args, **kwargs):
        thread_ids.append(threading.get_ident())
        return original_open(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "open", tracked_open)
    path = tmp_path / "科研 文献_测试.pdf"

    def make_document():
        document = pymupdf.open()
        try:
            page = document.new_page()
            page.insert_text((72, 72), "ABSTRACT\nA grounded research study.")
            page = document.new_page()
            page.insert_text((72, 72), "1 Methods\nFigure 1: Evidence pipeline.")
            page.draw_rect(pymupdf.Rect(72, 100, 150, 140))
            document.save(path)
        finally:
            document.close()

    await pdf.run_pdf_work(make_document)
    document = await pdf.PDFParser().parse(path)
    assert document is not None
    assert "Evidence pipeline" in document.full_text
    assert document.figures[0]["page"] == 2
    assert await pdf.run_pdf_work(analysis._pdf_page_count, path.read_bytes()) == 2
    images = await pdf.run_pdf_work(analysis._render_visual_pages, path)
    assert len(images) == 1 and images[0].startswith("data:image/jpeg;base64,")
    # The legacy synchronous rendering API uses this same worker, not the caller.
    assert len(analysis._visual_pages(path)) == 1
    assert len(set(thread_ids)) == 1
    assert thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_fulltext_endpoints_report_busy_without_abstract_fallback(tmp_path, monkeypatch, db_session):
    async def paper_exists(*args):
        return {"title": "Test"}

    monkeypatch.setattr(analysis, "_find_paper_info", paper_exists)
    path = analysis._uploaded_pdf_path("busy-paper")
    path.write_bytes(b"%PDF-test")
    started, release = threading.Event(), threading.Event()
    task = asyncio.create_task(pdf.run_pdf_work(blocking_work, started, release))
    try:
        await wait_started(started)
        with pytest.raises(HTTPException) as uploaded:
            await analysis.upload_fulltext(
                "new-paper", UploadFile(filename="test.pdf", file=BytesIO(b"%PDF-test")), db_session
            )
        assert uploaded.value.status_code == 503
        assert uploaded.value.headers == {"Retry-After": "1"}
        assert not analysis._uploaded_pdf_path("new-paper").exists()
        with pytest.raises(HTTPException) as loaded:
            await analysis._load_document_context("busy-paper", {"title": "Test"})
        assert loaded.value.status_code == 503
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_render_and_validation_close_documents_on_failure(monkeypatch):
    closed = []

    class Document:
        @property
        def page_count(self):
            raise ValueError("invalid page tree")

        def __iter__(self):
            raise ValueError("invalid page tree")

        def close(self):
            closed.append(True)

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda *a, **kw: Document()))
    with pytest.raises(ValueError, match="invalid page tree"):
        await pdf.run_pdf_work(analysis._pdf_page_count, b"%PDF-test")
    assert await pdf.run_pdf_work(analysis._render_visual_pages, "test.pdf") == []
    assert len(closed) == 2
    assert await wait_recovered() == "recovered"


@pytest.mark.asyncio
async def test_busy_feature_extraction_reports_that_upload_was_saved(monkeypatch, db_session):
    async def paper_exists(*args):
        return {"title": "Test"}

    async def busy_parse(*args):
        raise pdf.PDFBusyError("busy")

    monkeypatch.setattr(analysis, "_find_paper_info", paper_exists)
    monkeypatch.setattr(analysis, "_pdf_page_count", lambda _: 1)
    monkeypatch.setattr(pdf.PDFParser, "parse", busy_parse)
    result = await analysis.upload_fulltext(
        "saved-paper",
        UploadFile(filename="test.pdf", file=BytesIO(b"%PDF-test")),
        db_session,
    )
    assert result["available"] is True
    assert result["feature_count"] == 0
    assert "PDF 已保存" in result["feature_error"]
    assert analysis._uploaded_pdf_path("saved-paper").read_bytes() == b"%PDF-test"

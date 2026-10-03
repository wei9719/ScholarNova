"""Native table recovery is optional and cannot discard the online text path."""

import threading
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.datastructures import UploadFile

from app.api.v1 import analysis
from app.services.pdf.parser import ParsedDocument, PDFParser, run_pdf_work


def make_table_pdf(path):
    import pymupdf

    doc = pymupdf.open()
    try:
        doc.set_metadata({"title": "Offline table fixture"})
        page = doc.new_page()
        page.insert_text((72, 80), "Table 1. Measured results")
        for x in (72, 172, 272):
            page.draw_line((x, 100), (x, 160))
        for y in (100, 130, 160):
            page.draw_line((72, y), (272, y))
        for x, y, text in ((78, 120, "Model"), (178, 120, "Score"),
                           (78, 150, "Ours"), (178, 150, "80.0")):
            page.insert_text((x, y), text)
        page.insert_text((72, 200), "Figure 1. Evidence flow")
        page.insert_text((72, 240), "Methods evidence remains available.\n" * 12)
        doc.save(path)
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_default_parser_preserves_text_and_figures_without_native_table_scan(tmp_path, monkeypatch):
    import pymupdf

    path = tmp_path / "table.pdf"
    await run_pdf_work(make_table_pdf, path)
    scan = MagicMock(side_effect=AssertionError("online parsing must not scan tables"))
    monkeypatch.setattr(pymupdf.Page, "find_tables", scan)

    parsed = await PDFParser().parse(path)

    scan.assert_not_called()
    assert parsed is not None
    assert "Ours" in parsed.full_text and "80.0" in parsed.full_text
    assert "[Page 1]" in parsed.full_text and parsed.tables == []
    assert parsed.figures[0]["page"] == 1
    text, note = analysis._document_context(parsed)
    assert "未执行结构化表格识别" in note and "可提取的表格文字" in note
    assert text.startswith(f"[材料覆盖说明：{note}]")


@pytest.mark.asyncio
async def test_opt_in_uses_real_pymupdf_extract_on_same_worker(tmp_path, monkeypatch):
    import pymupdf

    path = tmp_path / "structured-table.pdf"
    await run_pdf_work(make_table_pdf, path)
    worker_threads = []
    find_tables = pymupdf.Page.find_tables

    def tracked(page, *args, **kwargs):
        worker_threads.append(threading.get_ident())
        return find_tables(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "find_tables", tracked)
    parsed = await PDFParser(extract_tables=True).parse(path)

    assert parsed is not None and len(parsed.tables) == 1
    assert parsed.tables[0]["page"] == 1
    assert parsed.tables[0]["rows"] == [["Model", "Score"], ["Ours", "80.0"]]
    assert not parsed.metadata.get("parse_warnings")
    assert worker_threads == [await run_pdf_work(threading.get_ident)]
    assert worker_threads[0] != threading.get_ident()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["find", "extract"])
async def test_optional_table_failure_keeps_body_and_discloses_failed_page(tmp_path, monkeypatch, failure):
    import pymupdf

    path = tmp_path / "bad-table.pdf"
    await run_pdf_work(make_table_pdf, path)

    def fail(*_args, **_kwargs):
        raise ValueError("private parser details must not be shown")

    monkeypatch.setattr(pymupdf.Page, "find_tables", fail if failure == "find" else
                        lambda _page: SimpleNamespace(tables=[SimpleNamespace(extract=fail)]))
    parsed = await PDFParser(extract_tables=True).parse(path)

    assert parsed is not None and "80.0" in parsed.full_text and parsed.figures
    text, note = analysis._document_context(parsed)
    assert "1 页" in note and "结构化表格提取失败" in note
    assert "private parser details" not in text


def test_table_warning_survives_bounded_context_without_claiming_no_table_text():
    warning = "已保留页文本中可提取的表格文字，未执行结构化表格识别"
    parsed = ParsedDocument(title="Title", abstract="", full_text="x" * 48001,
                            metadata={"parse_warnings": [warning]})
    text, note = analysis._document_context(parsed)
    assert warning in note and "48000" in note
    assert len(text.split("\n\n", 1)[1]) == 48000


@pytest.mark.asyncio
async def test_empty_timeout_has_actionable_fulltext_error_and_keeps_download(tmp_path, monkeypatch):
    path = tmp_path / "already-downloaded.pdf"
    path.write_bytes(b"%PDF-test")
    monkeypatch.setattr(analysis, "_uploaded_pdf_path", lambda _paper_id: path)
    monkeypatch.setattr(PDFParser, "parse", AsyncMock(side_effect=TimeoutError()))

    text, visuals, coverage, error = await analysis._load_document_context("paper", {"title": "Test"})

    assert text == "" and visuals == [] and coverage == "abstract"
    assert "等待时限" in error and "稍后重试" in error and "仅使用摘要" in error
    assert path.read_bytes() == b"%PDF-test"


@pytest.mark.asyncio
async def test_upload_timeout_reports_saved_file_without_ready_index(tmp_path, monkeypatch, db_session):
    path = tmp_path / "uploaded.pdf"
    monkeypatch.setattr(analysis, "_uploaded_pdf_path", lambda _paper_id: path)
    monkeypatch.setattr(analysis, "_find_paper_info", AsyncMock(return_value={"title": "Test"}))
    monkeypatch.setattr(analysis, "_pdf_page_count", lambda _content: 1)
    monkeypatch.setattr(PDFParser, "parse", AsyncMock(side_effect=TimeoutError()))

    result = await analysis.upload_fulltext(
        "paper", UploadFile(filename="test.pdf", file=BytesIO(b"%PDF-test")), db_session,
    )

    assert result["available"] is True and result["feature_count"] == 0
    assert "PDF 已保存" in result["feature_error"] and "等待时限" in result["feature_error"]
    assert path.read_bytes() == b"%PDF-test"

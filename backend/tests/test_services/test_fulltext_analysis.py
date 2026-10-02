from io import BytesIO
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

from app.api.v1 import analysis as analysis_api
from app.api.v1.analysis import _document_text, _visual_pages
from app.config import settings
from app.services.pdf.parser import PDFParser


def test_document_context_includes_sections_figures_and_tables():
    document = SimpleNamespace(
        sections=[
            SimpleNamespace(heading="Methods", text="We train the proposed model."),
            SimpleNamespace(heading="Results", text="The model improves F1."),
        ],
        full_text="fallback",
        figures=[{"caption": "Figure 1: Overall architecture."}],
        tables=[{
            "page": 4,
            "caption": "Table 1: Main results.",
            "rows": [["Model", "F1"], ["Ours", "0.42"]],
        }],
    )

    context = _document_text(document)

    assert "Methods" in context
    assert "Figure 1: Overall architecture" in context
    assert "Ours | 0.42" in context


def test_visual_pages_include_vector_figure_caption_pages(tmp_path):
    import pymupdf

    pdf_path = tmp_path / "vector-figure.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Figure 1. Vector-only research architecture")
    page.draw_rect(pymupdf.Rect(72, 100, 250, 180))
    document.save(pdf_path)
    document.close()

    images = _visual_pages(pdf_path)

    assert len(images) == 1
    assert images[0].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_pdf_parser_preserves_section_and_figure_pages(tmp_path):
    import pymupdf

    pdf_path = tmp_path / "located-evidence.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "ABSTRACT\nA traceable retrieval study.\n"
        "INTRODUCTION\nBackground evidence.",
    )
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "1 Methods\nThe model uses grounded retrieval and verification.",
    )
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "2 Results\nThe method improves recall.\nFigure 1: Evidence pipeline.",
    )
    document.save(pdf_path)
    document.close()

    parsed = await PDFParser().parse(pdf_path)

    assert parsed is not None
    methods = next(section for section in parsed.sections if section.heading == "Methods")
    assert methods.page_start == 2
    assert methods.page_end == 2
    assert parsed.figures[0]["page"] == 3


@pytest.mark.asyncio
async def test_uploaded_pdf_is_persisted_and_used_as_fulltext(tmp_path, monkeypatch, db_session):
    import pymupdf

    monkeypatch.setattr(settings, "RUNTIME_DIR", str(tmp_path))

    async def paper_exists(_paper_id, _db):
        return {"title": "Imported paper"}

    monkeypatch.setattr(analysis_api, "_find_paper_info", paper_exists)

    document = pymupdf.open()
    for page_index in range(3):
        page = document.new_page()
        lines = [
            f"Methods and Results page {page_index + 1} line {line}. "
            "The proposed agent is evaluated with reproducible evidence."
            for line in range(18)
        ]
        page.insert_textbox(page.rect + (36, 36, -36, -36), "\n".join(lines), fontsize=9)
    pdf_bytes = document.tobytes()
    document.close()

    result = await analysis_api.upload_fulltext(
        "paper-1",
        UploadFile(filename="paper.pdf", file=BytesIO(pdf_bytes)),
        db=db_session,
    )
    assert result["available"] is True
    assert result["page_count"] == 3

    text, visuals, coverage, error = await analysis_api._load_document_context(
        "paper-1",
        {
            "title": "Imported paper",
            "doi": None,
            "url": None,
            "pdf_url": None,
            "venue": None,
        },
    )
    assert len(text) > 500
    assert visuals == []
    assert coverage == "fulltext:uploaded"
    assert error is None


@pytest.mark.asyncio
async def test_invalid_upload_is_rejected(tmp_path, monkeypatch, db_session):
    monkeypatch.setattr(settings, "RUNTIME_DIR", str(tmp_path))

    async def paper_exists(_paper_id, _db):
        return {"title": "Imported paper"}

    monkeypatch.setattr(analysis_api, "_find_paper_info", paper_exists)
    with pytest.raises(Exception) as exc_info:
        await analysis_api.upload_fulltext(
            "paper-1",
            UploadFile(filename="fake.pdf", file=BytesIO(b"not-a-pdf")),
            db=db_session,
        )
    assert getattr(exc_info.value, "status_code", None) == 400

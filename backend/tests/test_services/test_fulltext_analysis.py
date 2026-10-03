from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import UploadFile

from app.api.v1 import analysis as analysis_api
from app.api.v1.analysis import _document_text, _visual_pages
from app.config import settings
from app.schemas.query import AnalysisRequest
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
    assert "章节的摘录" in error  # Source acquired; the selected context is not the whole paper.


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


@pytest.mark.parametrize("mode", ["body", "sections", "captions", "tables", "large_table"])
def test_document_context_discloses_selection_and_bounds_all_text(mode):
    document = SimpleNamespace(sections=[], full_text="Provided evidence", figures=[], tables=[])
    if mode == "body":
        document.full_text = "x" * 48000 + "HIDDEN_BODY_TAIL"
    elif mode == "sections":
        document.sections = [SimpleNamespace(heading="Methods", text="Selected methods")]
        document.full_text = "Unrecognized preface not included in the selected section"
    elif mode == "captions":
        document.figures = [{"caption": f"Figure {i}"} for i in range(20)] + [{"caption": "HIDDEN_CAPTION"}]
    elif mode == "tables":
        document.tables = [{"rows": [["Visible"]] * 12 + [["HIDDEN_ROW"]]}] * 9
    else:
        document.tables = [{"rows": [["x" * 49000 + "HIDDEN_CELL_TAIL"]]}]

    text, note = analysis_api._document_context(document)

    assert note and text.startswith(f"[材料覆盖说明：{note}]")
    assert "HIDDEN" not in text
    assert len(text.split("\n\n", 1)[1]) <= 48000
    if mode == "sections":
        assert "不等于完整论文" in note and "Selected methods" in text
    elif mode == "captions":
        assert "20/21" in note
    elif mode == "tables":
        assert "8/9" in note and "12 行" in note
    else:
        assert "48000" in note


def test_short_plain_document_is_not_falsely_marked_truncated():
    text, note = analysis_api._document_context(SimpleNamespace(
        sections=[], full_text="Complete extracted short text", figures=[], tables=[],
    ))
    assert text == "Complete extracted short text"
    assert note == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", ["none", "accepted", "rejected"])
async def test_visible_coverage_is_not_left_to_model_claims(monkeypatch, vision):
    info = {"title": "Test", "authors": "Test", "year": 2026, "venue": "Test", "abstract": "Abstract"}
    note = "正文超过 48000 字符，本次仅提供前 48000 字符，不是完整论文"
    monkeypatch.setattr(analysis_api, "check_rate_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analysis_api, "_find_paper_info", AsyncMock(return_value=info))
    visuals = [] if vision == "none" else ["data:image/jpeg;base64,AA=="]
    monkeypatch.setattr(analysis_api, "_load_document_context", AsyncMock(return_value=(
        "Bounded original text", visuals, "fulltext:uploaded", note,
    )))
    captured = []

    async def text_model(**kwargs):
        captured.append(kwargs["messages"][-1]["content"])
        return SimpleNamespace(content="Model answer without its required disclaimer.",
                               usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    class VisionGateway:
        def __init__(self, **_kwargs):
            self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            self.usage = {}

        def configure(self, **kwargs):
            pass

        async def chat(self, **kwargs):
            captured.append(kwargs["messages"][-1]["content"][0]["text"])
            if vision == "rejected":
                raise RuntimeError("Synthetic vision rejection")
            return "Model answer without its required disclaimer."

    monkeypatch.setattr(analysis_api, "chat_with_fallback", text_model)
    monkeypatch.setattr("app.services.llm.gateway.LLMGateway", VisionGateway)
    monkeypatch.setattr("app.config.get_model_for_task", lambda _: {
        "provider": "fake", "model": "fake", "api_key": "", "base_url": "",
    })
    result = await analysis_api.analyze_paper("test", AnalysisRequest(query="Summarize"), None,
                                            SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()))
    assert all(note in prompt and "必须写“全文节选”" in prompt for prompt in captured)
    assert result.document_coverage == "fulltext"  # Compatibility: source availability, not full reading.
    if vision == "rejected":
        assert result.document_error.startswith(note + "；")
        assert "视觉模型未完成读取" in result.document_error
    else:
        assert result.document_error == note
    assert result.summary.startswith("> 材料覆盖：PDF 提取文字；" + note)
    count = 1 if vision == "accepted" else 0
    assert result.visual_pages_read == count
    assert f"完成本次分析的模型收到 {count} 个图表页面，不代表覆盖所有图表" in result.summary
    if vision == "rejected":
        assert "本次仅依据正文" in captured[-1]
        assert "另附 1 个" not in captured[-1]

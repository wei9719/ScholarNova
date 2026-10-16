import pymupdf

from app.api.v1.analysis import _inspect_pdf_identity


def pdf(text):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), text)
        return doc.tobytes()


def test_identity_preview_is_heuristic_and_preserves_excerpt():
    result = _inspect_pdf_identity(pdf("Graph recurrent imputation research\n10.1234/example"), "Graph recurrent imputation research", "10.1234/example")
    assert result["likely_match"]
    assert "人工" in result["summary"]
    assert "Graph" in result["excerpt"]


def test_unrelated_pdf_does_not_claim_identity_match():
    result = _inspect_pdf_identity(pdf("A completely different chemistry experiment"), "Graph recurrent imputation research", "")
    assert not result["likely_match"]
    assert "取消" in result["summary"]

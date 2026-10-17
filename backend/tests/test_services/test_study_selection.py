"""Study columns retrieve their real single-paper evidence, not repeated introductions."""

from __future__ import annotations

import hashlib

import pytest

from app.models.paper import PaperChunk, PaperEntity
from app.schemas.study import StudyDraftRequest
from app.services import study

GENERIC_QUESTION = "仅根据本论文已解析的材料回答；分开作者原文、建议与未知项。不要猜GitHub地址或作者邮箱。"
ABSTRACT = "We propose a pretrained model for traffic series. Introduction and related work discuss the model."


def indexed(pid, position, text, page, *, kind="fulltext", heading="Full text"):
    return PaperChunk(
        id=f"{pid}-{position}", paper_id=pid, position=position, kind=kind,
        heading=heading, page=page, content=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        feature_version="pdf-parser-chunker-v3", char_count=len(text),
    )


async def seed(db):
    db.add_all([
        PaperEntity(id="paper", title="Example model", abstract=ABSTRACT, source="test"),
        PaperEntity(id="private", title="Other paper", source="test"),
    ])
    await db.flush()
    # More than the old first-80 cap, with actual features later in the paper.
    db.add_all(indexed("paper", position, ABSTRACT, 1) for position in range(100))
    db.add_all([
        indexed("paper", 100, "Method formulation. Emb = TE + PE (1). PE uses sin and cos. Graph attention GAT and DropEdge equations (2) (3). MATH_PAGE4", 4),
        indexed("paper", 101, "4 Experiments. 4.1 Datasets: AQI36, PEMS-BAY and METR-LA. Training validation test split. Metrics MAE RMSE. DATASETS_PAGE5", 5),
        indexed("paper", 102, "4.4 Missing experiments: block missing data, masking continuous intervals of 1-4 hours. Evaluate imputation RMSE results. MISSING_PAGE6", 6),
        indexed("paper", 103, "Code availability: https://example.org/actual-code . Author contact: real@example.org . RESOURCE_PAGE9", 9),
        indexed("private", 0, "Experiments datasets UNKNOWN_PRIVATE. Equation PRIVATE = HIDDEN. https://private.example.org/code", 99),
    ])
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("section,markers", [
    ("experiments", ["DATASETS_PAGE5", "MISSING_PAGE6"]),
    ("math", ["MATH_PAGE4", "Emb = TE + PE"]),
    ("resources", ["https://example.org/actual-code", "real@example.org"]),
])
async def test_section_body_focus_survives_generic_question_and_first_80_cutoff(db_session, section, markers):
    await seed(db_session)
    material = await study.load_study_material(
        db_session, StudyDraftRequest(paper_id="paper", section=section, question=GENERIC_QUESTION),
    )
    assert material is not None and material.scope == "fulltext_excerpt"
    evidence = "\n".join(source.excerpt for source in material.sources)
    assert all(marker in evidence for marker in markers)
    assert "UNKNOWN_PRIVATE" not in evidence and "PRIVATE = HIDDEN" not in evidence
    assert len(material.sources) <= study.MAX_SOURCES
    assert all(len(source.excerpt) <= study.MAX_EXCERPT_CHARS for source in material.sources)
    assert [source.label for source in material.sources] == [f"S{i}" for i in range(1, len(material.sources) + 1)]
    assert not db_session.in_transaction()
    first = material.sources[0]
    expected_page = {"experiments": 5, "math": 4, "resources": 9}[section]
    assert first.page == expected_page
    assert first.section == "Full text"  # Never invent a parsed chapter title.
    assert first.id == {"experiments": "paper-101", "math": "paper-100", "resources": "paper-103"}[section]


@pytest.mark.asyncio
async def test_repeated_abstracts_do_not_consume_eight_evidence_slots(db_session):
    await seed(db_session)
    material = await study.load_study_material(db_session, StudyDraftRequest(paper_id="paper", section="overview"))
    normalized = [" ".join(source.excerpt.casefold().split()) for source in material.sources]
    assert len(normalized) == len(set(normalized))
    assert normalized.count(ABSTRACT.casefold()) <= 1


@pytest.mark.parametrize("section,signal", [
    ("math", "Emb = TE + PE (1)"),
    ("experiments", "Dataset and validation split: 60/20/20. MAE RMSE."),
])
def test_excerpt_anchors_column_signal_instead_of_generic_contact_instruction(section, signal):
    text = "GitHub contact context. " + "Ordinary prose. " * 160 + signal + " End."
    excerpt = study._excerpt(text, section, GENERIC_QUESTION)
    assert signal in excerpt
    assert len(excerpt) <= study.MAX_EXCERPT_CHARS


def test_dedup_preserves_different_mathematical_operators():
    from app.schemas.study import StudySource

    items = [(10, StudySource(label="", id=f"math-{i}", content_hash=str(i), section="Full text", page=4, excerpt=text), True)
             for i, text in enumerate(["Emb = TE + PE", "Emb = TE - PE"])]
    assert len(study._deduplicate_sources(items)) == 2


def test_math_excerpt_prioritizes_actual_equation_over_early_method_keywords():
    text = "Attention embedding is discussed. " + "Ordinary prose. " * 160 + "Emb = TE + PE (1)."
    assert "Emb = TE + PE (1)." in study._excerpt(text, "math", GENERIC_QUESTION)

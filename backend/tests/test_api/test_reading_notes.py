"""Offline persistence, provenance and conflict checks for personal reading notes."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.sql.dml import Update

from app.api.v1 import knowledge as knowledge_api
from app.models.knowledge import KnowledgeBase, KnowledgeChunk
from app.models.paper import PaperEntity
from app.services.features.knowledge import ensure_knowledge_features
from app.services.features.reading_notes import NOTE_LABELS, SECTION_TITLES


@pytest.fixture(autouse=True)
def forbid_models(monkeypatch):
    calls = AsyncMock(side_effect=AssertionError("Reading-note CRUD must not invoke a model"))
    monkeypatch.setattr(knowledge_api, "chat_with_fallback", calls)
    monkeypatch.setattr("app.services.llm.gateway.LLMGateway.chat", calls)
    yield calls
    calls.assert_not_called()


def reading_payload(**overrides):
    payload = {
        "title": "个人阅读笔记", "category": "测试主题", "content": "客户端正文不可替代结构",
        "card_type": "reading_note",
        "card_data": {
            "schema_version": 1, "status": "reading", "source_query": "如何复现？",
            "sections": {"overview": {"paper_notes": "摘录待核验", "user_notes": "我的问题"}},
        },
    }
    payload.update(overrides)
    return payload


async def create_note(client, **overrides):
    response = await client.post("/api/v1/knowledge", json=reading_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def chunks_for(db, kid):
    return list((await db.execute(select(KnowledgeChunk).where(
        KnowledgeChunk.knowledge_id == kid,
    ).order_by(KnowledgeChunk.position))).scalars().all())


@pytest.mark.asyncio
async def test_all_sections_roundtrip_and_never_auto_polish(client, db_session):
    payload = reading_payload(auto_polish=True, notes="仅属于我的备注")
    payload["card_data"]["sections"] = {
        name: {field: f"{name}/{field}\n原样公式：$L = x_1^2$" for field in NOTE_LABELS}
        for name in SECTION_TITLES
    }
    result = await create_note(client, **payload)
    assert result["card_data"] == payload["card_data"]
    assert result["card_type"] == "reading_note"
    assert result["polish_status"] == "skipped"
    assert result["model_completed"] is None
    assert result["total_tokens"] == 0
    assert "客户端正文" not in result["content"]
    for name in SECTION_TITLES:
        for field, label in NOTE_LABELS.items():
            assert label in result["content"]
            assert payload["card_data"]["sections"][name][field] in result["content"]
    fetched = (await client.get(f'/api/v1/knowledge/{result["id"]}')).json()
    listed = (await client.get("/api/v1/knowledge", params={"card_type": "reading_note"})).json()
    assert fetched["card_data"] == result["card_data"]
    assert listed["items"][0]["card_data"] == result["card_data"]
    chunks = await chunks_for(db_session, result["id"])
    assert len(chunks) == 28
    assert all(chunk.content.startswith("【") for chunk in chunks)
    assert any("个人备注，非论文事实" in chunk.content for chunk in chunks)


@pytest.mark.asyncio
async def test_partial_sections_are_canonical_and_long_formulas_survive(client, db_session):
    long_text = " 公式推导与实验步骤\n" * 1200 + "末尾公式 $a_{n+1}=a_n+1$\n"
    payload = reading_payload()
    payload["card_data"]["sections"] = {"math": {"ai_suggestions": long_text}}
    result = await create_note(client, **payload)
    assert result["card_data"]["sections"] == {
        "math": {"paper_notes": "", "ai_suggestions": long_text, "user_notes": ""},
    }
    assert result["content"].endswith(long_text)
    chunks = await chunks_for(db_session, result["id"])
    assert len(chunks) > 3
    assert all("AI建议，非论文事实" in chunk.content for chunk in chunks)
    assert any("末尾公式" in chunk.content for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"schema_version": 2}, {"schema_version": True}, {"schema_version": "1"},
    {"status": "completed"}, {"surprise": "not allowed"},
    {"source_query": "q" * 2001}, {"source_query": None},
    {"sections": {"unknown": {}}}, {"sections": {"math": {"extra": "no"}}},
    {"sections": {"math": {"paper_notes": 12}}},
    {"sections": {"math": {"paper_notes": None}}},
    {"sections": {"math": {"paper_notes": "x" * 20_001}}},
])
async def test_invalid_reading_schema_rejected_on_create_and_update(client, patch):
    original = await create_note(client)
    payload = reading_payload()
    payload["card_data"].update(deepcopy(patch))
    created = await client.post("/api/v1/knowledge", json=payload)
    assert created.status_code == 422
    changed = await client.put(f'/api/v1/knowledge/{original["id"]}', json={
        "card_data": payload["card_data"],
    })
    assert changed.status_code == 422
    unchanged = (await client.get(f'/api/v1/knowledge/{original["id"]}')).json()
    assert unchanged["card_data"] == original["card_data"]
    assert unchanged["updated_at"] == original["updated_at"]


@pytest.mark.asyncio
async def test_reading_update_preserves_structure_and_explicit_clear_is_rejected(client):
    original = await create_note(client, notes="旧备注")
    changed = await client.put(f'/api/v1/knowledge/{original["id"]}', json={
        "title": "只改标题", "notes": None, "content": "不能伪造与结构不同的正文",
        "expected_updated_at": original["updated_at"],
    })
    assert changed.status_code == 200, changed.text
    result = changed.json()
    assert result["title"] == "只改标题" and result["notes"] is None
    assert result["content"] == original["content"]
    assert result["card_data"] == original["card_data"]
    cleared = await client.put(f'/api/v1/knowledge/{original["id"]}', json={"card_data": None})
    assert cleared.status_code == 422


@pytest.mark.asyncio
async def test_reading_card_data_replace_and_type_conversion_are_explicit(client):
    original = await create_note(client)
    replacement = {"schema_version": 1, "status": "reviewed", "sections": {"writing": {"user_notes": "写作想法"}}}
    updated = (await client.put(f'/api/v1/knowledge/{original["id"]}', json={"card_data": replacement})).json()
    assert set(updated["card_data"]["sections"]) == {"writing"}
    assert "摘录待核验" not in updated["content"]
    converted = (await client.put(f'/api/v1/knowledge/{original["id"]}', json={"card_type": "foundation"})).json()
    assert converted["card_type"] == "foundation"
    assert converted["card_data"] is None
    assert converted["content"] == updated["content"]
    invalid = await client.put(f'/api/v1/knowledge/{original["id"]}', json={"card_type": "reading_note"})
    assert invalid.status_code == 422
    restored = await client.put(f'/api/v1/knowledge/{original["id"]}', json={
        "card_type": "reading_note", "card_data": replacement,
    })
    assert restored.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("card_type", [None, "research_point", "direction", "architecture", "paper", "analysis", "foundation", "skill", "old_custom_type"])
async def test_legacy_and_new_plain_types_remain_compatible(client, card_type):
    payload = {
        "title": "普通知识", "category": "主题", "content": "完整原文", "card_type": card_type,
        "card_data": {"legacy_field": [1, "old"]}, "source_paper_id": "old-import-id",
    }
    created = await client.post("/api/v1/knowledge", json=payload)
    assert created.status_code == 201
    item = created.json()
    assert item["card_data"] == payload["card_data"]
    assert item["content"] == "完整原文"
    updated = await client.put(f'/api/v1/knowledge/{item["id"]}', json={
        "card_data": None, "notes": "新备注", "source_paper_id": None,
    })
    assert updated.status_code == 200
    assert updated.json()["card_data"] is None
    assert updated.json()["source_paper_id"] is None


@pytest.mark.asyncio
async def test_type_filter_categories_notes_keyword_and_pagination(client):
    await create_note(client, category="共同分类", notes="独有备注关键词")
    await create_note(client, category="阅读专用")
    for kind, category in [("foundation", "共同分类"), ("skill", "技能专用"), (None, "传统专用")]:
        response = await client.post("/api/v1/knowledge", json={
            "title": "其他类型", "category": category, "content": "正文", "card_type": kind,
        })
        assert response.status_code == 201
    response = (await client.get("/api/v1/knowledge", params={
        "card_type": "reading_note", "keyword": "独有备注关键词", "page_size": 1,
    })).json()
    assert response["total"] == 1
    assert response["items"][0]["notes"] == "独有备注关键词"
    assert {item["name"]: item["count"] for item in response["categories"]} == {"共同分类": 1, "阅读专用": 1}
    categories = (await client.get("/api/v1/knowledge/categories", params={"card_type": "reading_note"})).json()
    assert categories == response["categories"]
    scoped = (await client.get("/api/v1/knowledge", params={"card_type": "reading_note", "category": "共同分类"})).json()
    assert scoped["total"] == 1
    assert (await client.get("/api/v1/knowledge")).json()["total"] == 5


@pytest.mark.asyncio
async def test_source_validation_only_on_reading_create_or_source_change(client, db_session):
    invalid = await client.post("/api/v1/knowledge", json=reading_payload(source_paper_id="missing"))
    assert invalid.status_code == 422
    paper = PaperEntity(title="真实存储的来源", id="stored-paper", source="crossref")
    db_session.add(paper)
    await db_session.commit()
    note = await create_note(client, source_paper_id=paper.id)
    await db_session.delete(paper)
    await db_session.commit()
    # A disappeared old source must not prevent the reader saving personal work.
    updated = await client.put(f'/api/v1/knowledge/{note["id"]}', json={"title": "来源已删除仍可编辑"})
    assert updated.status_code == 200
    invalid = await client.put(f'/api/v1/knowledge/{note["id"]}', json={"source_paper_id": "another-missing"})
    assert invalid.status_code == 422
    cleared = await client.put(f'/api/v1/knowledge/{note["id"]}', json={"source_paper_id": None})
    assert cleared.status_code == 200 and cleared.json()["source_paper_id"] is None


@pytest.mark.asyncio
async def test_paper_filter_reuses_reading_note_without_hiding_type_categories(client, db_session):
    paper = PaperEntity(id="exact-paper", title="已保存的论文", source="crossref")
    db_session.add(paper)
    await db_session.commit()
    first = await create_note(client, source_paper_id=paper.id, category="分类 A")
    await create_note(client, source_paper_id=paper.id, category="分类 B")
    await create_note(client, category="其他阅读分类")
    await client.post("/api/v1/knowledge", json={
        "title": "同论文旧知识", "category": "旧类型分类", "content": "旧资料", "source_paper_id": paper.id,
    })
    result = (await client.get("/api/v1/knowledge", params={
        "card_type": "reading_note", "source_paper_id": paper.id, "category": "分类 A",
    })).json()
    assert result["total"] == 1 and result["items"][0]["id"] == first["id"]
    assert {item["name"] for item in result["categories"]} == {"分类 A", "分类 B", "其他阅读分类"}
    missing = (await client.get("/api/v1/knowledge", params={
        "card_type": "reading_note", "source_paper_id": "exact-paper-prefix-not-a-match",
    })).json()
    assert missing["total"] == 0 and missing["categories"] == result["categories"]


@pytest.mark.asyncio
async def test_microsecond_optimistic_lock_and_timezone_equivalence(client, monkeypatch):
    item = await create_note(client)
    instant = datetime.fromisoformat(item["updated_at"])

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.replace(tzinfo=tz)

    monkeypatch.setattr(knowledge_api, "datetime", FrozenDatetime)
    equivalent = instant.replace(tzinfo=UTC).astimezone(timezone(timedelta(hours=8))).isoformat()
    first = await client.put(f'/api/v1/knowledge/{item["id"]}', json={"title": "第一编辑", "expected_updated_at": equivalent})
    assert first.status_code == 200, first.text
    assert datetime.fromisoformat(first.json()["updated_at"]) == instant + timedelta(microseconds=1)
    conflict = await client.put(f'/api/v1/knowledge/{item["id"]}', json={"title": "过时编辑", "expected_updated_at": item["updated_at"]})
    assert conflict.status_code == 409
    second = await client.put(f'/api/v1/knowledge/{item["id"]}', json={"notes": "第二编辑", "expected_updated_at": first.json()["updated_at"]})
    assert second.status_code == 200
    assert datetime.fromisoformat(second.json()["updated_at"]) == instant + timedelta(microseconds=2)
    assert second.json()["title"] == "第一编辑"


@pytest.mark.asyncio
async def test_optimistic_lock_works_for_old_sqlite_second_only_timestamp(client, db_session):
    legacy = KnowledgeBase(title="旧数据库条目", category="旧", content="原文")
    db_session.add(legacy)
    await db_session.commit()
    await db_session.refresh(legacy)
    assert legacy.updated_at.microsecond == 0
    updated = await client.put(f"/api/v1/knowledge/{legacy.id}", json={
        "title": "旧条目更新", "expected_updated_at": legacy.updated_at.isoformat(),
    })
    assert updated.status_code == 200, updated.text


@pytest.mark.asyncio
async def test_atomic_compare_and_swap_rejects_change_after_initial_read(client, db_session, monkeypatch):
    item = await create_note(client)
    execute = db_session.execute
    injected = False

    async def racing_execute(statement, *args, **kwargs):
        nonlocal injected
        if isinstance(statement, Update) and not injected:
            injected = True
            await execute(update(KnowledgeBase).where(KnowledgeBase.id == item["id"]).values(
                title="并发编辑已提交", updated_at=datetime.fromisoformat(item["updated_at"]) + timedelta(seconds=1),
            ).execution_options(synchronize_session=False))
            await db_session.commit()
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", racing_execute)
    response = await client.put(f'/api/v1/knowledge/{item["id"]}', json={
        "title": "不应覆盖", "expected_updated_at": item["updated_at"],
    })
    assert response.status_code == 409
    assert (await client.get(f'/api/v1/knowledge/{item["id"]}')).json()["title"] == "并发编辑已提交"


@pytest.mark.asyncio
async def test_personal_notes_reindex_clear_and_delete_with_no_stale_features(client, db_session):
    response = await client.post("/api/v1/knowledge", json={
        "title": "普通技能", "category": "基础", "content": "基础材料", "notes": "我的独有操作步骤", "card_type": "skill",
    })
    item = response.json()
    first = await chunks_for(db_session, item["id"])
    assert any(chunk.content == "【个人备注，非论文事实】\n我的独有操作步骤" for chunk in first)
    assert any("未独立核验，非论文原文" in chunk.content for chunk in first)
    updated = await client.put(f'/api/v1/knowledge/{item["id"]}', json={"notes": "更新后的步骤"})
    assert updated.status_code == 200
    second = await chunks_for(db_session, item["id"])
    assert {chunk.id for chunk in first}.isdisjoint({chunk.id for chunk in second})
    assert all("我的独有" not in chunk.content for chunk in second)
    knowledge = await db_session.get(KnowledgeBase, item["id"])
    ensured = await ensure_knowledge_features(db_session, [knowledge])
    assert [chunk.id for chunk in ensured] == [chunk.id for chunk in second]
    await client.put(f'/api/v1/knowledge/{item["id"]}', json={"notes": None})
    assert all("个人备注" not in chunk.content for chunk in await chunks_for(db_session, item["id"]))
    assert (await client.delete(f'/api/v1/knowledge/{item["id"]}')).status_code == 200
    assert not await chunks_for(db_session, item["id"])

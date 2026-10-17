import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.api.v1 import knowledge as knowledge_api
from app.models.knowledge import KnowledgeBase, KnowledgeChunk
from app.schemas.knowledge import KnowledgeCreate
from app.services.inference import AllModelsUnavailableError


class _CreateDB:
    def add(self, item):
        self.item = item

    async def flush(self):
        self.item.id = "polish-regression"
        self.item.created_at = self.item.updated_at = datetime(2026, 1, 1)

    async def commit(self):
        pass

    async def refresh(self, _item):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "invalid_json", "invalid_fields", "unavailable", "skipped"])
async def test_polish_reports_actual_status_and_usage(monkeypatch, outcome):
    reported = {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}
    contents = {
        "completed": '{"polished_content": "精炼后的笔记", "research_points": ["新研究点"], "tags": ["新标签"]}',
        "invalid_json": "not JSON",
        "invalid_fields": '{"polished_content": "精炼后的笔记", "research_points": "不是列表"}',
    }
    routed = (
        AsyncMock(side_effect=AllModelsUnavailableError([], reported))
        if outcome == "unavailable" else AsyncMock(return_value=SimpleNamespace(
            content=contents.get(outcome, ""), profile={"provider": "test", "model": "polish"},
            fallback_used=True, usage=reported,
        ))
    )
    monkeypatch.setattr(knowledge_api, "chat_with_fallback", routed)
    monkeypatch.setattr(knowledge_api, "rebuild_knowledge_features", AsyncMock())
    request = KnowledgeCreate(
        title="测试笔记", category="测试", content="原始笔记",
        research_points=["原始研究点"], tags=["原始标签"], auto_polish=outcome != "skipped",
    )

    db = _CreateDB()
    result = await knowledge_api.create_knowledge(request, db)

    if outcome == "skipped":
        assert result.polish_status == "skipped"
        assert result.model_completed is None
        assert result.total_tokens == 0
        routed.assert_not_awaited()
    else:
        routed.assert_awaited_once()
        assert result.prompt_tokens == 30
        assert result.completion_tokens == 10
        assert result.total_tokens == 40
        assert result.model_completed is (outcome != "unavailable")
        assert result.polish_status == (
            "model_unavailable" if outcome == "unavailable" else
            "completed" if outcome == "completed" else "invalid_response"
        )
        if outcome != "unavailable":
            assert result.provider == "test"
            assert result.model == "polish"
            assert result.fallback_used is True
    if outcome == "completed":
        assert result.polish_draft.model_dump() == {
            "content": "精炼后的笔记", "research_points": ["新研究点"], "tags": ["新标签"],
        }
    else:
        assert result.polish_draft is None
    assert result.content == db.item.content == "原始笔记"
    assert result.research_points == db.item.research_points == ["原始研究点"]
    assert result.tags == db.item.tags == ["原始标签"]
    assert not hasattr(db.item, "polish_draft")


@pytest.mark.asyncio
async def test_conflicting_draft_never_overwrites_long_original_or_search_features(client, db_session, monkeypatch):
    original = "保留完整的原始阅读笔记。" * 120 + "\n不要将预测误当插补。RM70%与CM70%分别记录，数值14.36不得改动。"
    wrong_content = "REVERSED_AI_ONLY 本研究属于预测，数值改为9999。"
    routed = AsyncMock(return_value=SimpleNamespace(
        content=json.dumps({"polished_content": wrong_content,
                            "research_points": ["AI_ONLY_REVERSED_POINT"], "tags": ["AI_ONLY_REVERSED_TAG"]}),
        profile={"provider": "test", "model": "polish"}, fallback_used=False,
        usage={"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100},
    ))
    monkeypatch.setattr(knowledge_api, "chat_with_fallback", routed)
    payload = {"title": "不能反转否定", "category": "隔离测试", "content": original,
               "research_points": ["原始研究点"], "tags": ["原始标签"], "auto_polish": True}
    response = await client.post("/api/v1/knowledge", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["content"] == original and body["research_points"] == payload["research_points"]
    assert body["tags"] == payload["tags"] and body["polish_draft"]["content"] == wrong_content
    assert body["model_completed"] is True and body["polish_status"] == "completed"
    assert body["total_tokens"] == 100  # Model output is not factual validation.
    stored = (await db_session.execute(select(KnowledgeBase).where(KnowledgeBase.id == body["id"]))).scalar_one()
    assert stored.content == original and stored.research_points == payload["research_points"] and stored.tags == payload["tags"]
    chunks = (await db_session.execute(select(KnowledgeChunk).where(KnowledgeChunk.knowledge_id == body["id"]))).scalars().all()
    indexed = "\n".join(chunk.content for chunk in chunks)
    assert "不要将预测误当插补" in indexed and "14.36" in indexed
    assert "REVERSED_AI_ONLY" not in indexed and "9999" not in indexed
    # Ignoring/cancelling the suggestion does not need an update; a fresh read
    # has the original and no persisted draft or model-derived tags.
    fresh = (await client.get(f'/api/v1/knowledge/{body["id"]}')).json()
    assert fresh["content"] == original and fresh["polish_draft"] is None
    assert fresh["research_points"] == payload["research_points"] and fresh["tags"] == payload["tags"]
    listed = (await client.get("/api/v1/knowledge", params={"category": "隔离测试"})).json()["items"]
    assert len(listed) == 1 and listed[0]["polish_draft"] is None
    messages = routed.await_args.kwargs["messages"]
    assert "不可信" in messages[0]["content"] and "不得执行" in messages[0]["content"]
    assert "否定关系和数字" in messages[0]["content"]
    assert "不要将预测误当插补" in messages[1]["content"] and "14.36" in messages[1]["content"]
    assert "200字以内" not in messages[1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_edit", [False, True])
async def test_adopting_a_draft_requires_explicit_existing_update_and_respects_conflicts(client, db_session, monkeypatch, concurrent_edit):
    _ = db_session
    draft = {"polished_content": "未有插补证据时，不可依据预测结果推断插补表现。",
             "research_points": ["保留任务边界"], "tags": ["任务边界"]}
    routed = AsyncMock(return_value=SimpleNamespace(
        content=json.dumps(draft, ensure_ascii=False), profile={"provider": "test", "model": "polish"},
        fallback_used=False, usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    ))
    monkeypatch.setattr(knowledge_api, "chat_with_fallback", routed)
    original = "不能把预测结果当成插补证据。"
    created = (await client.post("/api/v1/knowledge", json={
        "title": "确认草稿", "category": "隔离测试", "content": original, "auto_polish": True,
    })).json()
    assert created["content"] == original
    if concurrent_edit:
        other = await client.put(f'/api/v1/knowledge/{created["id"]}', json={
            "notes": "另一个编辑已保存", "expected_updated_at": created["updated_at"],
        })
        assert other.status_code == 200
    adopted = await client.put(f'/api/v1/knowledge/{created["id"]}', json={
        **created["polish_draft"], "expected_updated_at": created["updated_at"],
    })
    assert adopted.status_code == (409 if concurrent_edit else 200)
    fresh = (await client.get(f'/api/v1/knowledge/{created["id"]}')).json()
    assert fresh["content"] == (original if concurrent_edit else draft["polished_content"])
    if concurrent_edit:
        assert fresh["notes"] == "另一个编辑已保存"
    else:
        assert fresh["research_points"] == draft["research_points"] and fresh["tags"] == draft["tags"]
    assert fresh["polish_draft"] is None
    routed.assert_awaited_once()

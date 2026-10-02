"""A researcher's save → compare → route → revisit journey.

Real API, SQLite commits and feature rebuilds; only remote model inference is
replaced. Synthetic notes are not scientific evidence or a model-quality test.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeChunk


@pytest.mark.asyncio
async def test_notes_become_a_revisitable_route_without_losing_requirements(
    client, db_session, monkeypatch,
):
    first = (await client.post('/api/v1/knowledge', json={
        'title': '草莓保鲜方法 A', 'category': '食品保鲜',
        'content': '合成验收材料：方法 A 用低温处理，尚未验证真实保质期。',
        'source_paper_title': 'Synthetic cold-storage study A',
        'auto_polish': False,
    })).json()
    second = (await client.post('/api/v1/knowledge', json={
        'title': '草莓保鲜方法 B', 'category': '食品保鲜',
        'content': '合成验收材料：方法 B 用气调包装，尚未验证真实保质期。',
        'source_paper_title': 'Synthetic cold-storage study B',
        'auto_polish': False,
    })).json()
    other = await client.post('/api/v1/knowledge', json={
        'title': '交通流预测', 'category': '交通研究',
        'content': '独立研究方向的模拟笔记，不应进入本轮保鲜路线。',
    })
    assert other.status_code == 201
    selected_ids = [first['id'], second['id']]
    library = (await client.get('/api/v1/knowledge', params={'category': '食品保鲜'})).json()
    assert library['total'] == 2
    assert {item['id'] for item in library['items']} == set(selected_ids)

    requirement = '比较两种草莓保鲜方法，只规划实验，不涉及交通流预测。'
    full_analysis = '建议比较低温与气调包装，控制初始成熟度，并记录失重率。' * 12 + '末尾保留验证边界。'
    calls = []

    async def model(**kwargs):
        calls.append(kwargs)
        prompt = kwargs['messages'][-1]['content']
        assert requirement in prompt
        assert 'Synthetic' not in prompt  # Notes, not an invented full-paper read.
        assert '方法 A' in prompt and '方法 B' in prompt
        assert '独立研究方向的模拟笔记' not in prompt
        return SimpleNamespace(
            content=full_analysis, profile={'provider': 'synthetic', 'model': 'fixture'},
            fallback_used=False,
            usage={'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20},
        )

    judge = AsyncMock(return_value=None)
    monkeypatch.setattr('app.api.v1.knowledge.chat_with_fallback', model)
    monkeypatch.setattr('app.api.v1.knowledge.judge_architecture', judge)
    response = await client.post('/api/v1/knowledge/ai-analyze', json={
        'knowledge_ids': selected_ids, 'query': requirement,
    })
    assert response.status_code == 200
    analyzed = response.json()
    assert analyzed['analysis'] == full_analysis
    assert analyzed['model_completed'] is True
    assert len(calls) == 1
    assert judge.call_args.kwargs['user_query'] == requirement
    # Analysis is a reviewable result, not a silent route or knowledge write.
    assert (await client.get('/api/v1/knowledge/routes')).json()['total'] == 0
    assert (await client.get('/api/v1/knowledge')).json()['total'] == 3

    description = f'研究要求：{requirement}\n\n{analyzed["analysis"]}'
    saved = await client.post('/api/v1/knowledge/routes', json={
        'title': '草莓保鲜实验设计', 'description': description,
        'knowledge_ids': selected_ids,
    })
    assert saved.status_code == 201
    route_id = saved.json()['id']
    revisited = (await client.get(f'/api/v1/knowledge/routes/{route_id}')).json()
    assert revisited['description'] == description
    assert revisited['description'].endswith('末尾保留验证边界。')
    assert revisited['knowledge_ids'] == selected_ids
    for kid in revisited['knowledge_ids']:
        linked = await client.get(f'/api/v1/knowledge/{kid}')
        assert linked.status_code == 200
        assert linked.json()['source_paper_title'].startswith('Synthetic cold-storage')

    updated = await client.put(f'/api/v1/knowledge/{first["id"]}', json={
        'content': '更新验收笔记：需加入真菌计数指标。',
    })
    assert updated.status_code == 200
    chunks = (await db_session.execute(select(KnowledgeChunk).where(
        KnowledgeChunk.knowledge_id == first['id'],
    ))).scalars().all()
    assert chunks and all('低温处理' not in chunk.content for chunk in chunks)
    assert any('真菌计数' in chunk.content for chunk in chunks)
    assert (await client.delete(f'/api/v1/knowledge/{second["id"]}')).status_code == 200
    assert (await client.get(f'/api/v1/knowledge/{second["id"]}')).status_code == 404
    # Losing an upstream note must not delete a user's saved plan.
    assert (await client.get(f'/api/v1/knowledge/routes/{route_id}')).json()['description'] == description

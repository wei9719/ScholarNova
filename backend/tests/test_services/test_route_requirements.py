"""Saved research requirements survive the route's next analysis step."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import inference
from app.services.diagram import route_pipeline


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['model', 'fallback', 'bounded'])
async def test_route_analysis_uses_saved_goal_without_treating_draft_as_evidence(monkeypatch, mode):
    description = '研究要求：比较草莓保鲜方法，不使用交通流数据。\n\n已审阅的实验设计草稿。'
    if mode == 'bounded':
        description += '补充材料。' * 2500 + '不应伪称已读的尾部。'
    route = SimpleNamespace(id='synthetic-route', title='草莓保鲜', description=description)
    monkeypatch.setattr(route_pipeline, '_resolve_route_context', AsyncMock(return_value={
        'route': route, 'knowledge_list': [], 'knowledge_text': '笔记 A：低温处理的模拟摘录。',
    }))
    model = AsyncMock(return_value=SimpleNamespace(
        content='基于目标制定的待验证研究路线',
        profile={'provider': 'synthetic', 'model': 'fixture'}, fallback_used=False,
    ))
    if mode == 'fallback':
        model.side_effect = inference.AllModelsUnavailableError([], {})
    monkeypatch.setattr(inference, 'chat_with_fallback', model)
    stream = route_pipeline.stream_route_analysis(route.id, None)
    try:
        assert (await anext(stream))['progress'] == 10
        result = await anext(stream)
        assert result['progress'] == 30
    finally:
        # Stop before the image steps: this tests prompt flow, not paid rendering.
        await stream.aclose()
    prompt = model.call_args.kwargs['messages'][-1]['content']
    assert '研究要求：比较草莓保鲜方法，不使用交通流数据。' in prompt
    assert '不是论文原文证据' in prompt
    assert '不是论文全文' in prompt
    assert '笔记 A' in prompt
    if mode == 'fallback':
        assert description in result['data']['text_analysis']
        assert '未由模型分析' in result['data']['text_analysis']
        assert '规则兜底' in result['data']['text_label']
    elif mode == 'bounded':
        assert '本次仅提供前 12000 字' in prompt
        assert '不应伪称已读的尾部' not in prompt
    else:
        assert '已审阅的实验设计草稿' in prompt
        assert result['data']['text_analysis'] == '基于目标制定的待验证研究路线'

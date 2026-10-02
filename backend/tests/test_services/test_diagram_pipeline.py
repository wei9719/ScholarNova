import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.diagram import architecture_judge, route_pipeline
from app.services.inference import AllModelsUnavailableError


@pytest.mark.asyncio
async def test_architecture_judge_receives_the_actual_analysis(monkeypatch):
    analysis = "原文独有模块：频域交通编码器 → 时空预测头。"
    routed = AsyncMock(return_value=SimpleNamespace(
        content='{"layers": [{"name": "编码层", "modules": [{"name": "频域编码"}]}]}', usage={},
    ))
    monkeypatch.setattr(architecture_judge, "chat_with_fallback", routed)

    result = await architecture_judge.judge_architecture("交通预测背景", analysis)

    prompt = routed.call_args.kwargs["messages"][1]["content"]
    assert f"----- 原文开始 -----\n{json.dumps(analysis, ensure_ascii=False)}\n----- 原文结束 -----" in prompt
    assert "{analysis}" not in prompt
    assert result["layers"][0]["modules"][0]["name"] == "频域编码"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "invalid_json", "unavailable"])
async def test_architecture_judge_preserves_reported_usage(monkeypatch, outcome):
    reported = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20, "requests": 1}
    content = '{"layers": [{"name": "编码层", "modules": [{"name": "频域编码"}]}]}'
    if outcome == "unavailable":
        routed = AsyncMock(side_effect=AllModelsUnavailableError([], reported))
    else:
        routed = AsyncMock(return_value=SimpleNamespace(
            content=content if outcome == "completed" else "not JSON", usage=reported,
        ))
    monkeypatch.setattr(architecture_judge, "chat_with_fallback", routed)
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "requests": 1}

    result = await architecture_judge.judge_architecture("背景", "频域编码层", usage=usage)

    assert (result is not None) is (outcome == "completed")
    assert usage == {"prompt_tokens": 22, "completion_tokens": 13, "total_tokens": 35, "requests": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("architecture_ok,roadmap_ok,planning_ok", [
    (True, True, True), (False, True, True), (True, False, True), (True, True, False),
])
async def test_route_persists_successful_roadmap_image(monkeypatch, architecture_ok, roadmap_ok, planning_ok):
    from app import config
    from app.services import inference
    from app.services.diagram import planner, route_planner
    from app.services.llm import gateway

    route = SimpleNamespace(
        id="route-regression",
        title="交通预测",
        description="测试路线",
        knowledge_ids=[],
        ai_analysis=None,
        status="active",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(route_pipeline, "_resolve_route_context", AsyncMock(return_value={
        "route": route, "knowledge_list": [], "knowledge_text": "频域交通预测",
    }))
    monkeypatch.setattr(config, "get_model_for_task", lambda _task: {
        "provider": "test", "model": "test-model", "api_key": "", "base_url": "",
    })
    monkeypatch.setattr(inference, "chat_with_fallback", AsyncMock(return_value=SimpleNamespace(
        content="研究目标：验证频域交通预测。", profile={"provider": "test", "model": "text"},
        fallback_used=False,
    )))
    monkeypatch.setattr(planner, "plan_modules_with_llm", AsyncMock(return_value={
        "layout": "pipeline", "modules": [{"name": "编码层", "desc": "频域编码"}],
    } if planning_ok else None))
    monkeypatch.setattr(route_planner, "build_roadmap_for_route", AsyncMock(return_value={
        "prompt": "Draw roadmap", "plan": {"plan_source": "model" if planning_ok else "rule_fallback", "stages": [{
            "id": "S1", "zh": "基线验证", "tasks": ["复现"],
            "deliverable": "报告", "gate": "误差达标",
        }]},
    }))
    monkeypatch.setattr(architecture_judge, "judge_architecture", AsyncMock(return_value=None))

    class FakePlannerGateway:
        def __init__(self, task):
            assert task == "analysis"
            self.usage = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20, "requests": 2}

    monkeypatch.setattr(inference, "RoutedLLMGateway", FakePlannerGateway)

    class FakeGateway:
        def __init__(self, **_kwargs):
            assert _kwargs == {"provider": "test"}

        def configure(self, **_kwargs):
            pass

        async def generate_image(self, *, prompt, save_path):
            is_roadmap = save_path.endswith("_roadmap.png")
            succeeded = roadmap_ok if is_roadmap else architecture_ok
            if succeeded:
                name = "roadmap" if is_roadmap else "architecture"
                return {"status": "ok", "url": f"https://example.test/{name}.png"}
            return {"status": "error", "error": "test image unavailable"}

    monkeypatch.setattr(gateway, "LLMGateway", FakeGateway)
    events = [event async for event in route_pipeline.stream_route_analysis(route.id, db)]

    assert events[-1]["event"] == "done"
    persisted = events[-1]["data"]["ai_analysis"]
    assert persisted == route.ai_analysis
    assert "基线验证" in persisted
    roadmap_link = "![科研阶段路线图](https://example.test/roadmap.png)"
    assert (roadmap_link in persisted) is roadmap_ok
    assert "![科研阶段路线图]()" not in persisted
    assert ("科研阶段路线图图片未生成" in persisted) is not roadmap_ok
    roadmap_event = next(event for event in events if event.get("progress") == 85)
    assert (roadmap_event["message"] == "科研阶段路线图完成") is roadmap_ok
    expected_source = "model" if planning_ok else "rule_fallback"
    assert roadmap_event["data"]["plan_source"] == expected_source
    assert events[-1]["data"]["planning"]["architecture"] == expected_source
    assert events[-1]["data"]["planning"]["usage"]["total_tokens"] == 20
    assert ("并非模型定制架构" in persisted) is not planning_ok
    assert ("并非模型定制路线" in persisted) is not planning_ok
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_roadmap_marks_model_plan_or_explicit_rule_fallback(valid):
    from app.services.diagram.route_planner import build_roadmap_for_route

    stages = [{
        "id": i, "name": f"RAG Phase {i}", "zh": f"引用验证阶段{i}",
        "tasks": ["align RAG citations"], "deliverable": "citation report", "gate": "verify support",
    } for i in range(1, 6)]
    if not valid:
        stages[0].pop("tasks")
    gateway = SimpleNamespace(chat=AsyncMock(return_value=json.dumps({"stages": stages})))

    result = await build_roadmap_for_route(gateway, "RAG引用验证", "RAG evidence", "citation analysis")

    assert result["plan"]["plan_source"] == ("model" if valid else "rule_fallback")
    assert len(result["plan"]["stages"]) == 5
    gateway.chat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("knowledge_text,text_analysis,expected", [
    ("原文明确给出：y = Wx + b。", "", "y = Wx + b"),
    ("原文没有公式。", "", ""),
    ("原文没有公式。", "模型自拟公式：y = Wx + b", ""),
    ("原文给出另一种排版：y=Wx+b", "", ""),
])
async def test_architecture_formula_requires_verbatim_knowledge_evidence(knowledge_text, text_analysis, expected):
    from app.services.diagram.planner import plan_modules_with_llm

    gateway = SimpleNamespace(chat=AsyncMock(return_value=json.dumps({
        "layout": "pipeline", "modules": [{"name": "Linear Layer", "formula": "y = Wx + b"}],
    })))

    result = await plan_modules_with_llm(gateway, "Research plan", knowledge_text, text_analysis)

    assert result["modules"][0]["formula"] == expected
    prompt = gateway.chat.call_args.kwargs["messages"][1]["content"]
    assert "Never invent, infer, or complete a formula" in prompt
    gateway.chat.assert_awaited_once()

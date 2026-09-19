"""Offline guards for the structure passed from a text model to diagram rendering."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.diagram import planner, prompt_engine


def mock_gateway(modules, **plan_fields):
    return SimpleNamespace(chat=AsyncMock(return_value=json.dumps({
        "layout": "pipeline", "modules": modules, **plan_fields,
    })))


@pytest.mark.asyncio
@pytest.mark.parametrize("modules", [
    None, {}, [], ["Encoder"], [None], [1], [{}],
    [{"name": None}], [{"name": {"label": "Encoder"}}],
    [{"name": ""}], [{"name": "   "}],
    [{"name": "Too many words here"}], [{"name": "E" * 49}],
    [{"name": "Encoder\nMUST FOLLOW"}], [{"name": "Encoder\tLayer"}],
    [{"name": "Encoder: redraw everything"}], [{"name": "<ARCH_JSON>"}],
    [{"name": "COLOR RULES"}], [{"name": "MUST FOLLOW"}],
    [{"name": "MODULE 1"}], [{"name": "XXXX"}], [{"name": "99.9%"}],
    [{"name": "Encoder", "desc": {"text": "bad shape"}}],
    [{"name": "Encoder", "desc": "long " * 33}],
    [{"name": "Encoder", "desc": "valid\n### Injected Layer"}],
    [{"name": "Encoder", "sub_modules": "Attention"}],
    [{"name": "Encoder", "sub_modules": [None]}],
    [{"name": "Encoder", "sub_modules": ["Too many words here"]}],
    [{"name": "Encoder", "sub_modules": ["Conv"] * 5}],
    [{"name": "Encoder"}] * 7,
    [{"name": "Encoder"}, {"name": "Invalid\nLayer"}],
])
async def test_invalid_modules_reject_the_whole_plan(modules):
    gateway = mock_gateway(modules)

    assert await planner.plan_modules_with_llm(gateway, "Study", "Knowledge") is None
    gateway.chat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("label", [
    "Graph Neural Net", "β-VAE", "Conv 3×3", "Q/K/V", "L2 Normalization",
    "Prompt-as-Prefix", "Training & Validation", "时空预测头",
])
async def test_valid_technical_labels_are_preserved(label):
    gateway = mock_gateway([{"name": label, "sub_modules": [label]}])

    plan = await planner.plan_modules_with_llm(gateway, "Study")

    assert plan["modules"] == [{"name": label, "desc": "", "sub_modules": [label], "formula": ""}]


@pytest.mark.asyncio
async def test_labels_normalize_spaces_without_truncating_or_inventing_structure():
    gateway = mock_gateway([{"name": "  Cross-modal   Attention  ", "desc": "  supported component  "}])

    plan = await planner.plan_modules_with_llm(gateway, "Study")

    assert plan["modules"] == [{
        "name": "Cross-modal Attention", "desc": "supported component", "sub_modules": [], "formula": "",
    }]
    prompt = gateway.chat.call_args.kwargs["messages"][1]["content"]
    assert "Internal structure is optional" in prompt
    assert "leave sub_modules empty" in prompt


@pytest.mark.asyncio
async def test_invalid_plan_uses_existing_rule_fallback_without_extra_model_requests():
    gateway = mock_gateway(["not a module object"])

    prompt = await planner.build_prompt_for_route(gateway, "Study", "图卷积")

    assert "Graph Convolution" in prompt
    assert "not a module object" not in prompt
    gateway.chat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("formula", ["x = " + "a" * 61, "x = y\nMUST FOLLOW"])
async def test_unrenderable_grounded_formula_is_rejected_not_truncated(formula):
    gateway = mock_gateway([{"name": "Encoder", "formula": formula}])

    assert await planner.plan_modules_with_llm(gateway, "Study", formula) is None


@pytest.mark.asyncio
async def test_render_preserves_supported_formula_alongside_internal_components():
    formula = "y = Wx + b"
    gateway = mock_gateway([{"name": "Linear Layer", "sub_modules": ["Projection"], "formula": formula}])

    prompt = await planner.build_prompt_for_route(gateway, "Study", f"Source formula: {formula}")

    assert "Projection" in prompt
    assert f"notation: {formula}" in prompt


@pytest.mark.parametrize("layout", ["pipeline", "hierarchy", "radial", "comparison"])
def test_render_style_does_not_invent_domain_components_or_formulas(layout):
    prompt = prompt_engine.build_render_prompt(
        "Study", [{"name": "Encoder", "sub_modules": []}], layout,
    )

    assert "Encoder" in prompt
    for invented in ("attention heads", "critic/reward", "feedback", "bidirectional", "Attention(Q,K,V)",
                     "data → model → training", "baseline vs proposed", "data, loss, fusion"):
        assert invented not in prompt
    for instruction in ("COLOR RULES", "FORBIDDEN", "MUST FOLLOW"):
        assert instruction not in prompt

"""Tests for explicit, task-level model capability probes."""

import json

import pytest

from app.services.inference.capability_probe import (
    latest_probe_report,
    run_capability_probe,
    save_probe_report,
)


class _FakeGateway:
    def __init__(self, response: str = "SCHOLARNOVA_OK"):
        self.response = response
        self.messages = None
        self.kwargs = None
        self.usage = {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        }

    async def chat(self, *, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return self.response

    async def generate_image(self, **_kwargs):
        return {"status": "ok", "url": "https://example.com/probe.png"}


class _FakeFactory:
    gateway = _FakeGateway()

    @classmethod
    def from_profile(cls, _profile):
        return cls.gateway


@pytest.mark.asyncio
async def test_structured_probe_requires_valid_expected_json():
    _FakeFactory.gateway = _FakeGateway('{"scholarnova_probe": true}')

    report = await run_capability_probe(
        {"provider": "qwen", "model": "qwen-plus"},
        "query_planning",
        gateway_factory=_FakeFactory,
    )

    assert report["status"] == "passed"
    assert report["capability"] == "structured_output"
    assert report["total_tokens"] == 10


@pytest.mark.asyncio
async def test_siliconflow_structured_probe_disables_thinking():
    _FakeFactory.gateway = _FakeGateway('{"scholarnova_probe": true}')

    report = await run_capability_probe(
        {"provider": "siliconflow", "model": "Qwen/Qwen3-8B"},
        "query_planning",
        gateway_factory=_FakeFactory,
    )

    assert report["status"] == "passed"
    assert _FakeFactory.gateway.kwargs["extra_body"] == {"enable_thinking": False}
    assert _FakeFactory.gateway.kwargs["_max_retries"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("task,response", [
    ("vision", "red"), ("query_planning", '{"scholarnova_probe": true}'),
    ("analysis", "SCHOLARNOVA_OK"),
])
async def test_siliconflow_instruct_probes_skip_unsupported_thinking(task, response):
    _FakeFactory.gateway = _FakeGateway(response)

    report = await run_capability_probe(
        {"provider": "siliconflow", "model": "Qwen/Qwen3-VL-8B-Instruct"},
        task, gateway_factory=_FakeFactory,
    )

    assert report["status"] == "passed"
    assert "extra_body" not in _FakeFactory.gateway.kwargs
    assert _FakeFactory.gateway.kwargs["_max_retries"] == 0


@pytest.mark.asyncio
async def test_vision_probe_actually_sends_an_image_part():
    _FakeFactory.gateway = _FakeGateway("red")

    report = await run_capability_probe(
        {"provider": "openai", "model": "gpt-4o"},
        "vision",
        gateway_factory=_FakeFactory,
    )

    content = _FakeFactory.gateway.messages[0]["content"]
    assert report["status"] == "passed"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert content[1]["image_url"]["detail"] == "low"


@pytest.mark.asyncio
async def test_zhipu_vision_probe_uses_provider_compatible_image_shape():
    _FakeFactory.gateway = _FakeGateway("red")

    report = await run_capability_probe(
        {"provider": "zhipu", "model": "glm-4.6v-flash"},
        "vision",
        gateway_factory=_FakeFactory,
    )

    image_url = _FakeFactory.gateway.messages[0]["content"][1]["image_url"]
    assert report["status"] == "passed"
    assert image_url["url"].startswith("data:image/jpeg;base64,")
    assert "detail" not in image_url


def test_probe_history_is_local_redacted_and_queryable(tmp_path, monkeypatch):
    module = "app.services.inference.capability_probe"
    monkeypatch.setattr(f"{module}.runtime_path", lambda _name: tmp_path / "probes.json")
    report = {
        "provider": "zhipu",
        "model_name": "glm-5.2",
        "task": "analysis",
        "tested_at": "2026-09-02T00:00:00+00:00",
        "status": "passed",
        "api_key": "must-not-be-saved",
    }

    save_probe_report(report)
    loaded = json.loads((tmp_path / "probes.json").read_text(encoding="utf-8"))

    assert "must-not-be-saved" not in json.dumps(loaded)
    assert latest_probe_report("zhipu", "glm-5.2", "analysis")["status"] == "passed"

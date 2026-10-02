"""Offline safety checks: replace all configuration and provider imports with fakes."""

import asyncio
import importlib.util
import json
import logging
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def benchmark(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_live_api.py"
    spec = importlib.util.spec_from_file_location("offline_live_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Do not globally disable logging or retain runtime/sys.path mutations in tests.
    monkeypatch.setattr(module, "logging", SimpleNamespace(disable=lambda _: None, CRITICAL=logging.CRITICAL))
    monkeypatch.setattr(module.sys, "path", list(sys.path))
    monkeypatch.setenv("RUNTIME_DIR", "unused-test-runtime")
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(
        wait_for=asyncio.wait_for,
        Semaphore=asyncio.Semaphore,
        gather=asyncio.gather,
        sleep=AsyncMock(),
        run=asyncio.run,
    ))
    return module


@pytest.fixture
def fake_provider(benchmark, monkeypatch, tmp_path):
    profile = {
        "provider": "siliconflow",
        "model": "mock-chat-model",
        "api_key": "FAKE_CREDENTIAL_NEVER_LOG",
        "base_url": "https://api.siliconflow.cn/v1",
    }
    state = SimpleNamespace(
        calls=[], profiles=[], instances=[], active=0, peaks={}, fail_at=None,
        failure_status=429, missing_usage=False, response_override=None,
    )
    responses = {
        "query_json": '{"queries":["food manufacturing LLM","food production AI"]}',
        "evidence_json": '{"method":"混合检索","limitation":"尚未验证跨领域泛化"}',
        "grounded_answer": "召回率为80% [S1]。",
        "conflicting_evidence": "不能断言总是更好，两组材料结论不同 [S1][S2]。",
        "long_input": "SN731，120毫秒。",
    }
    names = {prompt: name for name, _, prompt in benchmark.CASES}

    class ProviderFailure(Exception):
        def __init__(self):
            super().__init__("FAKE_CREDENTIAL_NEVER_LOG https://private.invalid/?token=FAKE")
            self.status_code = state.failure_status

    class FakeGateway:
        def __init__(self):
            self.closed = False
            self._usage = dict.fromkeys((
                "request_attempts", "responses_received", "usage_reports", "requests",
                "prompt_tokens", "completion_tokens", "total_tokens",
            ), 0)
            state.instances.append(self)

        @classmethod
        def from_profile(cls, selected):
            state.profiles.append(dict(selected))
            return cls()

        @property
        def usage(self):
            return dict(self._usage)

        async def _invoke_text_request(self, call, **kwargs):
            self._usage["request_attempts"] += 1
            result = await call(**kwargs)
            self._usage["responses_received"] += 1
            return result

        async def chat(self, **kwargs):
            index = len(state.calls)
            state.calls.append(kwargs)
            level = 1 if index < 2 else 2 if index < 6 else 3
            state.active += 1
            state.peaks[level] = max(state.peaks.get(level, 0), state.active)
            try:
                await asyncio.sleep(0.001)

                async def answer():
                    if index == state.fail_at:
                        self._usage["responses_received"] += 1
                        raise ProviderFailure()
                    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop")])

                try:
                    await self._invoke_text_request(answer)
                except ProviderFailure as exc:
                    raise RuntimeError("Gateway wrapper also contains FAKE_CREDENTIAL_NEVER_LOG") from exc
                self._usage["requests"] = 1
                if not state.missing_usage:
                    self._usage.update(
                        usage_reports=1, prompt_tokens=11, completion_tokens=7, total_tokens=18,
                    )
                name = names[kwargs["messages"][0]["content"]]
                return state.response_override if state.response_override is not None else responses.get(name, "合成测试回答")
            finally:
                state.active -= 1

        async def _discard_openai_client(self):
            self.closed = True

    def request_options(provider):
        if provider == "siliconflow":
            return {"extra_body": {"enable_thinking": False}}
        return {"extra_body": {"thinking": {"type": "disabled"}}}

    config = SimpleNamespace(get_model_for_task=lambda _task: dict(profile))
    monkeypatch.setitem(sys.modules, "app.config", config)
    monkeypatch.setitem(sys.modules, "app.services.inference.model_router", SimpleNamespace(_request_options=request_options))
    monkeypatch.setitem(sys.modules, "app.services.llm.gateway", SimpleNamespace(LLMGateway=FakeGateway))
    args = Namespace(runtime=tmp_path / "runtime", output=tmp_path / "report.json", live=True)
    return SimpleNamespace(args=args, profile=profile, state=state)


def test_cli_requires_live_opt_in_before_runtime_or_gateway_access(benchmark, monkeypatch, tmp_path, capsys):
    run = AsyncMock()
    monkeypatch.setattr(benchmark, "run", run)
    monkeypatch.setattr(sys, "argv", ["probe", "--runtime", str(tmp_path), "--output", str(tmp_path / "report.json")])
    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2
    assert "--live is required" in capsys.readouterr().err
    run.assert_not_called()


def test_cli_requires_explicit_configured_runtime(benchmark, monkeypatch, tmp_path, capsys):
    run = AsyncMock()
    monkeypatch.setattr(benchmark, "run", run)
    monkeypatch.setattr(sys, "argv", ["probe", "--live", "--runtime", str(tmp_path), "--output", str(tmp_path / "report.json")])
    with pytest.raises(SystemExit):
        benchmark.main()
    assert "Configured runtime not found" in capsys.readouterr().err
    run.assert_not_called()


async def test_twelve_requests_are_bounded_and_each_has_no_retry_or_fallback(benchmark, fake_provider, capsys):
    await benchmark.run(fake_provider.args)
    state = fake_provider.state
    report = json.loads(fake_provider.args.output.read_text(encoding="utf-8"))
    assert len(state.calls) == len(state.instances) == 12
    assert state.peaks == {1: 1, 2: 2, 3: 3}
    assert state.active == 0 and all(item.closed for item in state.instances)
    assert [stage["requests"] for stage in report["stages"]] == [2, 4, 6]
    assert report["totals"] == {
        "request_attempts": 12, "responses_received": 12, "usage_reports": 12,
        "prompt_tokens": 132, "completion_tokens": 84, "total_tokens": 216,
    }
    assert all(row["finish_reason"] == "stop" for row in report["requests"])
    assert all(row["content_check"] for row in report["requests"])
    for call in state.calls:
        assert call["max_tokens"] == 128 and call["_max_retries"] == 0
        assert call["extra_body"] == {"enable_thinking": False}
        assert set(call) == {"messages", "temperature", "max_tokens", "_max_retries", "extra_body"}
    public_output = fake_provider.args.output.read_text(encoding="utf-8") + capsys.readouterr().out
    assert "FAKE_CREDENTIAL_NEVER_LOG" not in public_output
    assert "api_key" not in public_output


async def test_extra_cases_do_not_raise_the_fixed_twelve_request_cap(benchmark, fake_provider, monkeypatch):
    monkeypatch.setattr(benchmark, "CASES", benchmark.CASES * 10)
    await benchmark.run(fake_provider.args)
    assert len(fake_provider.state.calls) == 12


@pytest.mark.parametrize("failure_status", [429, 401, 402, 403, 500])
async def test_transport_failure_stops_escalation_without_retry(benchmark, fake_provider, failure_status, capsys):
    fake_provider.state.fail_at = 0
    fake_provider.state.failure_status = failure_status
    await benchmark.run(fake_provider.args)
    report = json.loads(fake_provider.args.output.read_text(encoding="utf-8"))
    # The currently scheduled stage completes, including its queued request.
    assert len(fake_provider.state.calls) == 2
    assert len(report["stages"]) == 1 and report["stopped_reason"]
    assert report["totals"]["request_attempts"] == 2
    failed = next(row for row in report["requests"] if not row["transport_success"])
    assert failed["http_status"] == failure_status and failed["error_type"] == "ProviderFailure"
    assert failed["usage"]["usage_reports"] == 0
    assert "FAKE_CREDENTIAL_NEVER_LOG" not in fake_provider.args.output.read_text(encoding="utf-8")
    assert "FAKE_CREDENTIAL_NEVER_LOG" not in capsys.readouterr().out


@pytest.mark.parametrize("changes", [
    {"api_key": ""},
    {"provider": "local", "base_url": "http://127.0.0.1:8766/v1"},
    {"provider": "custom"},
    {"base_url": "http://api.siliconflow.cn/v1"},
    {"base_url": "https://api.siliconflow.cn.attacker.invalid/v1"},
    {"base_url": "https://api.siliconflow.cn@attacker.invalid/v1"},
    {"base_url": "https://user:pass@api.siliconflow.cn/v1"},
    {"base_url": "https://api.siliconflow.cn:8443/v1"},
    {"provider": "zhipu", "base_url": "https://api.siliconflow.cn/v1"},
])
async def test_invalid_routes_are_rejected_before_any_gateway_is_created(benchmark, fake_provider, changes):
    fake_provider.profile.update(changes)
    with pytest.raises(ValueError, match="approved configured cloud endpoint"):
        await benchmark.run(fake_provider.args)
    assert not fake_provider.state.instances and not fake_provider.state.calls
    assert not fake_provider.args.output.exists()


async def test_missing_provider_usage_is_not_invented(benchmark, fake_provider):
    fake_provider.state.missing_usage = True
    await benchmark.run(fake_provider.args)
    report = json.loads(fake_provider.args.output.read_text(encoding="utf-8"))
    assert report["totals"]["request_attempts"] == 12
    assert report["totals"]["responses_received"] == 12
    assert report["totals"]["usage_reports"] == 0
    assert report["totals"]["total_tokens"] == 0
    assert "Missing usage does not mean zero charge" in report["billing_note"]


async def test_transport_success_does_not_imply_content_success(benchmark, fake_provider):
    fake_provider.state.response_override = "not JSON and missing required source identifiers"
    await benchmark.run(fake_provider.args)
    report = json.loads(fake_provider.args.output.read_text(encoding="utf-8"))
    assert all(row["transport_success"] for row in report["requests"])
    assert not next(row for row in report["requests"] if row["case"] == "query_json")["content_check"]
    assert not next(row for row in report["requests"] if row["case"] == "grounded_answer")["content_check"]


@pytest.mark.parametrize(("name", "response", "expected"), [
    ("query_json", '{"queries":["one","two"]}', True),
    ("query_json", '```json\n{"queries":["one","two"]}\n```', True),
    ("query_json", '{"queries":["one"]}', False),
    ("query_json", '{"queries":["one",2]}', False),
    ("evidence_json", '{"method":"A","limitation":"B"}', True),
    ("evidence_json", '{"method":"A","limitation":""}', False),
    ("evidence_json", "not JSON", False),
    ("grounded_answer", "80% [S1]", True),
    ("grounded_answer", "80%", False),
    ("conflicting_evidence", "不同结论 [S1][S2]", True),
    ("conflicting_evidence", "只有来源 [S1]", False),
    ("long_input", "SN731 120毫秒", True),
    ("long_input", "120毫秒", False),
    ("product_help", "   ", False),
])
def test_structural_content_checks(benchmark, name, response, expected):
    assert benchmark.validate_case(name, response) is expected


def test_safe_error_omits_messages_and_non_integer_status(benchmark):
    exc = RuntimeError("FAKE_CREDENTIAL_NEVER_LOG")
    exc.status_code = "FAKE_CREDENTIAL_NEVER_LOG"
    assert benchmark.safe_error(exc) == {"error_type": "RuntimeError", "http_status": None}


def test_report_path_must_not_replace_an_existing_file(benchmark, tmp_path):
    output = tmp_path / "report.json"
    output.write_text("previous report", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        benchmark.validate_output_path(tmp_path / "runtime", output)
    assert output.read_text(encoding="utf-8") == "previous report"


@pytest.mark.parametrize("filename", ["model_config.json", "logs/report.json", "nested/../new.json"])
def test_report_path_must_stay_outside_runtime(benchmark, tmp_path, filename):
    runtime = tmp_path / "runtime"
    with pytest.raises(ValueError, match="outside the selected user runtime"):
        benchmark.validate_output_path(runtime, runtime / filename)


def test_report_path_rejects_even_a_dangling_symlink(benchmark, tmp_path):
    # No OS symlink privilege is required to test the guard's dangling-link case.
    output = SimpleNamespace(exists=lambda: False, is_symlink=lambda: True)
    with pytest.raises(ValueError, match="already exists"):
        benchmark.validate_output_path(tmp_path / "runtime", output)


def test_new_report_outside_runtime_is_accepted(benchmark, tmp_path):
    benchmark.validate_output_path(tmp_path / "runtime", tmp_path / "reports" / "new.json")


def test_cli_rejects_runtime_output_before_provider_access(benchmark, monkeypatch, tmp_path):
    config = tmp_path / "model_config.json"
    config.write_text('{"api_key":"synthetic-fixture-only"}', encoding="utf-8")
    run = AsyncMock()
    monkeypatch.setattr(benchmark, "run", run)
    monkeypatch.setattr(sys, "argv", ["probe", "--live", "--runtime", str(tmp_path), "--output", str(config)])
    with pytest.raises(SystemExit):
        benchmark.main()
    run.assert_not_called()
    assert json.loads(config.read_text(encoding="utf-8"))["api_key"] == "synthetic-fixture-only"


def test_cli_hash_check_protects_against_unnoticed_config_change(benchmark, monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config = runtime / "model_config.json"
    config.write_text('{"fixture":1}', encoding="utf-8")

    async def fake_run(_args):
        config.write_text('{"fixture":2}', encoding="utf-8")

    monkeypatch.setattr(benchmark, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["probe", "--live", "--runtime", str(runtime), "--output", str(tmp_path / "report.json")])
    with pytest.raises(AssertionError, match="Model configuration was changed"):
        benchmark.main()

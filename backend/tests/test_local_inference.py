"""Local inference contracts without importing Torch, loading weights or opening ports."""

import asyncio
import os
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from local_inference import Settings, create_app
from local_inference.engine import ContextLimitError, GenerationCancelled, TransformersEngine


TOKEN = "offline-test-token-not-a-secret-123456789"
HEADERS = {"Authorization": "Bearer " + TOKEN}
BODY = {"model": "Qwen2.5-1.5B-Instruct", "messages": [{"role": "user", "content": "Hello"}]}


class FakeEngine:
    def __init__(self, settings):
        self.settings = settings
        self.device = "test"
        self.loaded = 0
        self.closed = 0
        self.calls = []

    def load(self):
        self.loaded += 1

    def generate(self, messages, max_tokens, temperature, cancelled):
        self.calls.append((messages, max_tokens, temperature))
        return {"content": "answer", "finish_reason": "stop",
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}}

    def close(self):
        self.closed += 1


@pytest.fixture
def settings(tmp_path):
    return Settings(model_dir=tmp_path, token=TOKEN)


@pytest.fixture
async def service(settings):
    engine = FakeEngine(settings)
    app = create_app(settings, engine_factory=lambda _: engine)
    assert engine.loaded == 0
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://127.0.0.1") as client:
            yield app, client, engine
    assert engine.loaded == engine.closed == 1


async def test_authentication_and_browser_requests_cannot_invoke_model(service):
    app, client, engine = service
    for headers, status in [({}, 401), ({"Authorization": "Bearer wrong"}, 401),
                            ({**HEADERS, "Origin": "https://untrusted.example"}, 403)]:
        response = await client.post("/v1/chat/completions", json=BODY, headers=headers)
        assert response.status_code == status
        assert "message" in response.json()["error"]
    assert (await client.get("/health")).status_code == 401
    assert engine.calls == []
    assert app.state.active is None


async def test_health_and_openai_response_preserve_actual_usage(service):
    _, client, engine = service
    health = await client.get("/health", headers=HEADERS)
    assert health.json() == {
        "status": "ok", "model": BODY["model"], "context_length": 2048,
        "max_output_tokens": 320, "device": "test", "busy": False,
    }
    response = await client.post("/v1/chat/completions", headers=HEADERS, json={
        **BODY, "model": BODY["model"].lower(), "max_tokens": 12, "temperature": 0.2,
    })
    result = response.json()
    assert response.status_code == 200
    assert result["model"] == BODY["model"]
    assert result["choices"][0]["message"] == {"role": "assistant", "content": "answer"}
    assert result["usage"] == {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
    assert engine.calls == [(BODY["messages"], 12, 0.2)]
    assert TOKEN not in health.text + response.text
    assert str(engine.settings.model_dir) not in health.text + response.text


@pytest.mark.parametrize("changes,status", [
    ({"model": "different-model"}, 404), ({"max_tokens": 321}, 400),
    ({"max_tokens": 0}, 422), ({"max_tokens": True}, 422),
    ({"max_tokens": 513}, 422), ({"stream": True}, 422),
    ({"messages": []}, 422), ({"messages": [{"role": "tool", "content": "text"}]}, 422),
    ({"messages": [{"role": "user", "content": [{"type": "image_url"}]}]}, 422),
    ({"messages": [{"role": "user", "content": "a" * 16001}]}, 422),
])
async def test_invalid_requests_do_not_reach_engine(service, changes, status):
    _, client, engine = service
    response = await client.post("/v1/chat/completions", json={**BODY, **changes}, headers=HEADERS)
    assert response.status_code == status
    assert "message" in response.json()["error"]
    assert engine.calls == []


@pytest.mark.parametrize("failure,status,message", [
    (ContextLimitError("Input plus requested output exceeds the local context limit"), 400, "context limit"),
    (RuntimeError("private engine detail"), 503, "Local generation failed"),
])
async def test_engine_errors_are_safe_and_release_the_slot(service, failure, status, message):
    app, client, engine = service
    def fail(*args):
        raise failure
    engine.generate = fail
    response = await client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
    assert response.status_code == status
    assert message in response.json()["error"]["message"]
    assert "private engine detail" not in response.text
    assert app.state.active is None


class BlockingEngine(FakeEngine):
    def __init__(self, settings):
        super().__init__(settings)
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancel_seen = threading.Event()

    def generate(self, messages, max_tokens, temperature, cancelled):
        self.started.set()
        while not self.release.wait(0.005):
            if cancelled.is_set():
                self.cancel_seen.set()
        if cancelled.is_set():
            raise GenerationCancelled()
        return super().generate(messages, max_tokens, temperature, cancelled)


async def wait_for_event(event):
    assert await asyncio.to_thread(event.wait, 2), "test worker did not reach the expected state"


async def test_timeout_cancels_decoding_without_queueing_the_next_request(tmp_path):
    config = Settings(model_dir=tmp_path, token=TOKEN, timeout=0.08)
    engine = BlockingEngine(config)
    app = create_app(config, engine_factory=lambda _: engine)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://127.0.0.1") as client:
            first = asyncio.create_task(client.post("/v1/chat/completions", json=BODY, headers=HEADERS))
            try:
                await wait_for_event(engine.started)
                assert (await client.post("/v1/chat/completions", json=BODY, headers=HEADERS)).status_code == 429
                response = await first
                assert response.status_code == 504
                await wait_for_event(engine.cancel_seen)
                assert app.state.active is not None
                assert (await client.post("/v1/chat/completions", json=BODY, headers=HEADERS)).status_code == 429
            finally:
                engine.release.set()
                if app.state.active is not None:
                    await asyncio.wait({app.state.active}, timeout=2)
            assert app.state.active is None
            assert (await client.post("/v1/chat/completions", json=BODY, headers=HEADERS)).status_code == 200


async def test_disconnect_signals_cancellation(monkeypatch, settings):
    from starlette.requests import Request
    async def disconnected(self):
        return True
    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    engine = BlockingEngine(settings)
    app = create_app(settings, engine_factory=lambda _: engine)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://127.0.0.1") as client:
            try:
                response = await client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
                assert response.status_code == 499
                await wait_for_event(engine.cancel_seen)
                assert app.state.active is not None
            finally:
                engine.release.set()


def test_settings_are_fail_closed_and_use_dedicated_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOLARNOVA_LOCAL_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOLARNOVA_LOCAL_MODEL_TOKEN", TOKEN)
    monkeypatch.setenv("SCHOLARNOVA_LOCAL_MODEL_PORT", "8767")
    settings = Settings.from_env()
    assert settings.port == 8767
    assert TOKEN not in repr(settings)
    monkeypatch.delenv("SCHOLARNOVA_LOCAL_MODEL_TOKEN")
    with pytest.raises(ValueError, match="dedicated random"):
        Settings.from_env()


class FakeTensor:
    shape = (1, 7)
    def to(self, device):
        return self


def fake_transformer_engine(monkeypatch, settings, tokens):
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        StoppingCriteria=object, StoppingCriteriaList=list,
    ))
    engine = TransformersEngine(settings)
    engine.device = "cpu"
    engine.torch = SimpleNamespace(inference_mode=nullcontext)
    class Tokenizer:
        eos_token_id = 99
        def apply_chat_template(self, messages, **kwargs):
            assert messages == BODY["messages"]
            assert kwargs == {"tokenize": True, "add_generation_prompt": True,
                              "return_tensors": "pt", "return_dict": True}
            return {"input_ids": FakeTensor(), "attention_mask": FakeTensor()}
        def decode(self, ids, skip_special_tokens):
            assert ids == tokens and skip_special_tokens
            return "generated"
    class Outputs:
        def __getitem__(self, key):
            assert key == (0, slice(7, None))
            return SimpleNamespace(tolist=lambda: tokens)
    class Model:
        config = SimpleNamespace(max_position_embeddings=32768)
        generation_config = SimpleNamespace(eos_token_id=[99])
        calls = []
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return Outputs()
    engine.tokenizer = Tokenizer()
    engine.model = Model()
    return engine


@pytest.mark.parametrize("tokens,reason", [([21, 22], "length"), ([21, 99], "stop"), ([99], "stop")])
def test_engine_uses_chat_template_and_counts_generated_tokens(monkeypatch, settings, tokens, reason):
    engine = fake_transformer_engine(monkeypatch, settings, tokens)
    result = engine.generate(BODY["messages"], 2, 0, threading.Event())
    assert result["finish_reason"] == reason
    assert result["usage"] == {"prompt_tokens": 7, "completion_tokens": len(tokens), "total_tokens": 7 + len(tokens)}
    assert engine.model.calls[0]["do_sample"] is False
    assert engine.model.calls[0]["max_new_tokens"] == 2


def test_context_limit_includes_requested_completion_without_truncation(monkeypatch, tmp_path):
    settings = Settings(model_dir=tmp_path, token=TOKEN, context=12, max_tokens=5)
    engine = fake_transformer_engine(monkeypatch, settings, [99])
    with pytest.raises(ContextLimitError):
        engine.generate(BODY["messages"], 6, 0, threading.Event())
    assert engine.model.calls == []
    assert engine.generate(BODY["messages"], 5, 0, threading.Event())["usage"]["prompt_tokens"] == 7


def test_cancelled_request_never_starts_generation(monkeypatch, settings):
    engine = fake_transformer_engine(monkeypatch, settings, [99])
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(GenerationCancelled):
        engine.generate(BODY["messages"], 2, 0, cancelled)
    assert engine.model.calls == []


def test_port_is_reserved_before_application_creation(monkeypatch, settings):
    from local_inference import __main__ as entry
    events = []
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            events.append("close")
        def setsockopt(self, *args):
            pass
        def bind(self, address):
            assert address == ("127.0.0.1", 8766)
            events.append("bind")
            raise OSError("port occupied")
    monkeypatch.setattr(entry.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(entry.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(entry, "create_app", lambda *args: events.append("create_app"))
    with pytest.raises(OSError, match="occupied"):
        entry.main()
    assert events == ["bind", "close"]


@pytest.mark.parametrize("ram,gpu,error", [
    (4 * 1024**3, 4 * 1024**3, None),
    (1, 4 * 1024**3, "system memory"),
    (4 * 1024**3, 1, "GPU memory"),
])
def test_loader_is_offline_read_only_and_checks_memory(monkeypatch, settings, ram, gpu, error):
    weight_file = settings.model_dir / "model.safetensors"
    weight_file.write_bytes(b"offline test placeholder")
    before = {path.name: path.read_bytes() for path in settings.model_dir.iterdir()}
    # Restore the process environment after exercising production environment isolation.
    for name in ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME", "HF_HUB_OFFLINE",
                 "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_IMPLICIT_TOKEN",
                 "TOKENIZERS_PARALLELISM"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=ram)))
    calls = []
    fake_torch = SimpleNamespace(
        set_num_threads=lambda _: None, float16="float16", float32="float32",
        cuda=SimpleNamespace(is_available=lambda: True, mem_get_info=lambda: (gpu, gpu), empty_cache=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    class Loader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            assert path == settings.model_dir
            assert kwargs["local_files_only"] is True
            assert kwargs["trust_remote_code"] is False
            calls.append(kwargs)
            return SimpleNamespace(eval=lambda: None)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoTokenizer=Loader, AutoModelForCausalLM=Loader))
    engine = TransformersEngine(settings)
    try:
        if error:
            with pytest.raises(RuntimeError, match=error):
                engine.load()
            assert calls == []
        else:
            engine.load()
            assert len(calls) == 2
            assert calls[1]["use_safetensors"] is True
            assert calls[1]["torch_dtype"] == "float16"
            assert calls[1]["device_map"] == {"": "cuda"}
        assert os.environ["HF_HUB_OFFLINE"] == os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert os.environ["HF_HOME"] != str(settings.model_dir)
        assert {path.name: path.read_bytes() for path in settings.model_dir.iterdir()} == before
    finally:
        engine.close()


async def test_failed_startup_still_closes_the_engine(settings):
    engine = FakeEngine(settings)
    def fail():
        raise RuntimeError("offline startup failed")
    engine.load = fail
    app = create_app(settings, engine_factory=lambda _: engine)
    with pytest.raises(RuntimeError, match="offline startup failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("Failed model initialization must not expose a ready service")
    assert engine.closed == 1

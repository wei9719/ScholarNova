"""Small private OpenAI-compatible endpoint; one active request, no waiting queue."""

import asyncio
import hmac
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import ContextLimitError, GenerationCancelled, TransformersEngine


@dataclass(frozen=True)
class Settings:
    model_dir: Path
    token: str = field(repr=False)
    model_name: str = "Qwen2.5-1.5B-Instruct"
    port: int = 8766
    context: int = 2048
    max_tokens: int = 320
    timeout: float = 90.0
    device: str = "auto"

    def __post_init__(self):
        if not self.model_dir.is_absolute() or not self.model_dir.is_dir():
            raise ValueError("An existing absolute local model directory is required")
        if len(self.token) < 32 or not self.token.isascii() or any(c.isspace() for c in self.token):
            raise ValueError("A dedicated random local service token of at least 32 characters is required")
        if not 1 <= self.port <= 65535 or not 1 <= self.max_tokens <= 512:
            raise ValueError("Invalid local port or output token limit")
        if not self.max_tokens < self.context <= 2048 or not 0 < self.timeout <= 120:
            raise ValueError("Invalid local context or timeout limit")
        if self.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("Local device must be auto, cpu or cuda")

    @classmethod
    def from_env(cls):
        return cls(
            model_dir=Path(os.environ.get("SCHOLARNOVA_LOCAL_MODEL_DIR", "")),
            token=os.environ.get("SCHOLARNOVA_LOCAL_MODEL_TOKEN", ""),
            port=int(os.environ.get("SCHOLARNOVA_LOCAL_MODEL_PORT", "8766")),
            timeout=float(os.environ.get("SCHOLARNOVA_LOCAL_TIMEOUT", "90")),
            device=os.environ.get("SCHOLARNOVA_LOCAL_DEVICE", "auto"),
        )


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    messages: list[Message] = Field(min_length=1, max_length=32)
    max_tokens: int = Field(default=256, ge=1, le=512, strict=True)
    temperature: float = Field(default=0, ge=0, le=2)
    stream: Literal[False] = False


def create_app(settings=None, *, engine_factory=TransformersEngine):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-model")
        engine = engine_factory(settings)
        app.state.engine = engine
        app.state.executor = executor
        app.state.active = None
        app.state.cancelled = None
        app.state.ready = False
        try:
            await asyncio.get_running_loop().run_in_executor(executor, engine.load)
            app.state.ready = True
            yield
        finally:
            app.state.ready = False
            if app.state.cancelled is not None:
                app.state.cancelled.set()
            # Do not unload live tensors until the active decoding step has stopped.
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
            await asyncio.to_thread(engine.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def error_response(status, message):
        return JSONResponse({"error": {
            "message": message, "type": "server_error" if status >= 500 else "invalid_request_error",
            "param": None, "code": str(status),
        }}, status_code=status)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(422, "Invalid request: use non-streaming text messages and bounded output tokens")

    @app.middleware("http")
    async def authenticate(request, call_next):
        provided = request.headers.get("authorization", "").encode("utf-8")
        expected = ("Bearer " + settings.token).encode("ascii")
        if not hmac.compare_digest(provided, expected):
            return error_response(401, "Local service authentication required")
        if request.headers.get("origin"):
            return error_response(403, "Browser-origin requests are not supported")
        return await call_next(request)

    @app.get("/health")
    async def health():
        if not getattr(app.state, "ready", False):
            raise HTTPException(503, "Local model is not ready")
        return {
            "status": "ok", "model": settings.model_name,
            "context_length": settings.context, "max_output_tokens": settings.max_tokens,
            "device": app.state.engine.device, "busy": app.state.active is not None,
        }

    @app.post("/v1/chat/completions")
    async def chat(payload: ChatRequest, request: Request):
        if payload.model.casefold() != settings.model_name.casefold():
            raise HTTPException(404, "Unknown local model")
        if payload.max_tokens > settings.max_tokens:
            raise HTTPException(400, "Requested output exceeds the local token limit")
        if not getattr(app.state, "ready", False):
            raise HTTPException(503, "Local model is not ready")
        if app.state.active is not None:
            raise HTTPException(429, "Local model is busy; retry after the active request finishes")

        cancelled = threading.Event()
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            app.state.executor, app.state.engine.generate,
            [message.model_dump() for message in payload.messages],
            payload.max_tokens, payload.temperature, cancelled,
        )
        app.state.active = future
        app.state.cancelled = cancelled

        def completed(done):
            # A timed-out/disconnected handler must not free the slot while its thread runs.
            if not done.cancelled():
                done.exception()
            app.state.active = None
            app.state.cancelled = None

        future.add_done_callback(completed)
        deadline = loop.time() + settings.timeout
        try:
            while not future.done():
                if await request.is_disconnected():
                    cancelled.set()
                    raise HTTPException(499, "Client disconnected")
                remaining = deadline - loop.time()
                if remaining <= 0:
                    cancelled.set()
                    raise HTTPException(504, "Local generation timed out")
                await asyncio.wait({future}, timeout=min(0.05, remaining))
            result = await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except ContextLimitError as exc:
            raise HTTPException(400, str(exc)) from None
        except GenerationCancelled:
            raise HTTPException(503, "Local generation was cancelled") from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Local generation failed; check available memory and retry") from None

        return {
            "id": "chatcmpl-local-" + uuid.uuid4().hex,
            "object": "chat.completion", "created": int(time.time()), "model": settings.model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": result["content"]},
                         "finish_reason": result["finish_reason"]}],
            "usage": result["usage"],
        }

    return app

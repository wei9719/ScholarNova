"""Opt-in, provider-backed model capability probes with local-only history."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Callable

from app.config import runtime_path
from app.services.llm.gateway import LLMGateway

TASK_CAPABILITY = {
    "analysis": "text",
    "query_planning": "structured_output",
    "translation": "text",
    "vision": "vision",
    "recommendation": "text",
    "assistant": "text",
    "diagram": "image_generation",
}

_TEST_IMAGE = (
    "data:image/jpeg;base64,"
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQE"
    "BQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/"
    "2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU"
    "FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCABAAEADASIAAhEBAxEB/8QA"
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUF"
    "BAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1"
    "dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEB"
    "AQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC"
    "AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRom"
    "JygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU"
    "1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD50ooor8MP"
    "9UwooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKK"
    "ACiiigAooooAKKKKACiiigD/2Q=="
)


def _usage(gateway: Any) -> dict[str, int]:
    raw = getattr(gateway, "usage", {})
    return {
        key: max(0, int(raw.get(key, 0) or 0))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _request_options(provider: str, model: str = "") -> dict[str, Any]:
    options: dict[str, Any] = {}
    if provider == "zhipu":
        options["extra_body"] = {"thinking": {"type": "disabled"}}
    elif provider == "siliconflow" and "instruct" not in model.casefold():
        options["extra_body"] = {"enable_thinking": False}
    if provider in {
        "openai",
        "mimo",
        "deepseek",
        "zhipu",
        "qwen",
        "siliconflow",
        "moonshot",
        "sensenova",
        "custom",
    }:
        options["_max_retries"] = 0
    return options


def _json_object(value: str) -> dict[str, Any] | None:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def run_capability_probe(
    profile: dict[str, Any],
    task: str,
    *,
    gateway_factory: Callable[..., Any] = LLMGateway,
) -> dict[str, Any]:
    """Run exactly one small real request for the selected task capability."""
    capability = TASK_CAPABILITY[task]
    gateway = gateway_factory.from_profile(profile)
    started = time.perf_counter()
    success = False
    detail_zh = ""
    detail_en = ""
    error: str | None = None
    try:
        if capability == "structured_output":
            response = await asyncio.wait_for(
                gateway.chat(
                    messages=[
                        {
                            "role": "user",
                            "content": '只输出以下 JSON，不要解释：{"scholarnova_probe": true}',
                        }
                    ],
                    temperature=0,
                    max_tokens=64,
                    **_request_options(str(profile.get("provider") or ""), str(profile.get("model") or "")),
                ),
                timeout=30.0,
            )
            success = _json_object(response) == {"scholarnova_probe": True}
            detail_zh = (
                "模型返回了可解析且字段正确的 JSON。"
                if success
                else "模型有响应，但未返回要求的可解析 JSON。"
            )
            detail_en = (
                "The model returned parseable JSON with the required field."
                if success
                else "The model responded but did not return the required parseable JSON."
            )
        elif capability == "vision":
            image_url = {"url": _TEST_IMAGE}
            if str(profile.get("provider") or "") != "zhipu":
                image_url["detail"] = "low"
            response = await asyncio.wait_for(
                gateway.chat(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "What is the dominant color in this image? Reply with one English color word.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": image_url,
                                },
                            ],
                        }
                    ],
                    temperature=0,
                    max_tokens=32,
                    **_request_options(str(profile.get("provider") or ""), str(profile.get("model") or "")),
                ),
                timeout=30.0,
            )
            success = response.strip().lower().strip(".! `\n") == "red"
            detail_zh = (
                "模型正确识别了测试图片的主色。"
                if success
                else "模型有响应，但未正确识别测试图片。"
            )
            detail_en = (
                "The model correctly identified the test image's dominant color."
                if success
                else "The model responded but did not correctly identify the test image."
            )
        elif capability == "image_generation":
            result = await asyncio.wait_for(
                gateway.generate_image(
                    prompt="A single navy circle centered on a plain white background, no text.",
                    aspect_ratio="1:1",
                    image_size="1k",
                ),
                timeout=150.0,
            )
            success = result.get("status") == "ok" and bool(
                result.get("url") or result.get("output")
            )
            error = None if success else str(result.get("error") or "No image returned")
            detail_zh = "模型成功生成了一张测试图片。" if success else "模型未生成可用的测试图片。"
            detail_en = (
                "The model generated a test image."
                if success
                else "The model did not generate a usable test image."
            )
        else:
            response = await asyncio.wait_for(
                gateway.chat(
                    messages=[{"role": "user", "content": "Reply exactly: SCHOLARNOVA_OK"}],
                    temperature=0,
                    max_tokens=32,
                    **_request_options(str(profile.get("provider") or ""), str(profile.get("model") or "")),
                ),
                timeout=30.0,
            )
            success = "SCHOLARNOVA_OK" in response.upper()
            detail_zh = "模型完成了最小文本生成请求。" if success else "模型未返回可用文本。"
            detail_en = (
                "The model completed a minimal text-generation request."
                if success
                else "The model returned no usable text."
            )
    except TimeoutError:
        error = "能力测试超时"
        detail_zh = "模型未在能力测试时限内完成请求。"
        detail_en = "The model did not finish within the probe deadline."
    except Exception as exc:
        error = str(exc)
        detail_zh = "模型拒绝或未完成该能力请求。"
        detail_en = "The model rejected or did not complete this capability request."

    usage = _usage(gateway)
    report = {
        "success": success,
        "status": "passed" if success else "failed",
        "provider": str(profile.get("provider") or "unknown"),
        "model_name": str(profile.get("model") or "unknown"),
        "task": task,
        "capability": capability,
        "tested_at": datetime.now(UTC).isoformat(),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        **usage,
        "detail_zh": detail_zh,
        "detail_en": detail_en,
        "error": error,
        "endpoint_host": urlparse(str(profile.get("base_url") or "")).netloc,
    }
    return report


def _report_key(report: dict[str, Any]) -> str:
    raw = "|".join(
        str(report.get(key) or "") for key in ("provider", "model_name", "task", "endpoint_host")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def save_probe_report(report: dict[str, Any]) -> None:
    """Persist a redacted report atomically under the per-user runtime dir."""
    path = runtime_path("model_capability_probes.json")
    data: dict[str, Any] = {"version": 1, "probes": {}}
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("probes"), dict):
                data = loaded
    except (OSError, ValueError):
        pass
    safe_report = {key: value for key, value in report.items() if key != "api_key"}
    data["probes"][_report_key(safe_report)] = safe_report
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def latest_probe_report(provider: str, model_name: str, task: str) -> dict[str, Any] | None:
    """Return the newest local report for a task/model without exposing config."""
    path = runtime_path("model_capability_probes.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        matches = [
            report
            for report in (data.get("probes") or {}).values()
            if report.get("provider") == provider
            and report.get("model_name") == model_name
            and report.get("task") == task
        ]
        return max(matches, key=lambda item: item.get("tested_at") or "") if matches else None
    except (OSError, ValueError, AttributeError):
        return None

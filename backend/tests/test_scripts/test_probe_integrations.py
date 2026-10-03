"""Keep optional integration probes bounded and Zotero truly optional."""

import importlib.util
import json
import logging
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,cooldown", [
    (["crossref", "openalex", "semantic_scholar", "arxiv"], False),
    (["semantic_scholar"], False),
    (["semantic_scholar"], True),
])
async def test_selected_sources_are_bounded_and_zotero_is_not_contacted(monkeypatch, tmp_path, selected, cooldown):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("offline_integration_probe", scripts / "probe_integrations.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setattr(probe, "logging", SimpleNamespace(disable=lambda _: None, CRITICAL=logging.CRITICAL))
    monkeypatch.setattr(probe.sys, "path", list(sys.path))
    monkeypatch.setenv("RUNTIME_DIR", "unused-test-runtime")
    calls, closed = [], []

    def fake_source(name):
        class Source:
            api_key = None
            last_error = None

            def __init__(self, **kwargs):
                assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 20
                self.client = SimpleNamespace(event_hooks={})

            async def _get_client(self):
                return self.client

            def quota_snapshot(self):
                return {"cooldown_remaining_seconds": 60 if cooldown else 0}

            async def search(self, query, max_results):
                calls.append((name, query, max_results))
                for hook in self.client.event_hooks["request"]:
                    await hook(None)
                for hook in self.client.event_hooks["response"]:
                    await hook(SimpleNamespace(status_code=200))
                return [SimpleNamespace(title=query)]

            async def close(self):
                closed.append(name)

        Source.name = name
        return Source

    for name, class_name in [
        ("crossref", "CrossRefSource"), ("openalex", "OpenAlexSource"),
        ("semantic_scholar", "SemanticScholarSource"), ("arxiv", "ArxivSource"),
    ]:
        monkeypatch.setattr(f"app.services.sources.{name}.{class_name}", fake_source(name))
    forbidden = Mock(side_effect=AssertionError("Zotero must not be contacted"))
    monkeypatch.setattr("app.services.integrations.zotero.ZoteroLocalClient", forbidden)
    output = tmp_path / "report.json"
    await probe.run(Namespace(runtime=tmp_path, output=output, sources=selected, skip_zotero=True))
    report = json.loads(output.read_text(encoding="utf-8"))
    assert {row["source"] for row in report["sources"]} == set(selected)
    assert sorted(closed) == sorted(selected)
    assert len(calls) == (0 if cooldown else len(selected))
    assert all(query == "Attention Is All You Need" and limit == 5 for _, query, limit in calls)
    assert report["zotero"] == {"status": "skipped_by_request", "writes_attempted": 0}
    assert all(row["outbound_attempts"] == (0 if cooldown else 1) for row in report["sources"])
    forbidden.assert_not_called()

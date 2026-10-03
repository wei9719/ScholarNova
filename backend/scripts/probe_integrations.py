"""Opt-in read-only integration checks; never invoke a generative model.

One small academic search per source, no retry or cooldown bypass. Zotero reads
only. Reports omit keys, private library names, local paths and HTTP bodies.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from benchmark_live_api import safe_error, validate_output_path


async def run(args):
    os.environ["RUNTIME_DIR"] = str(args.runtime.resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.disable(logging.CRITICAL)
    from app.config import settings
    from app.services.integrations.zotero import ZoteroLocalClient
    from app.services.sources.arxiv import ArxivSource
    from app.services.sources.crossref import CrossRefSource
    from app.services.sources.openalex import OpenAlexSource
    from app.services.sources.semantic_scholar import SemanticScholarSource

    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "One real read-only search per academic source and local Zotero read connectivity, no LLM or write calls.",
        "query": "Attention Is All You Need",
        "proxy_env_present": any(os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")),
        "sources": [],
    }

    async def probe(source):
        started = time.perf_counter()
        row = {"source": source.name, "key_configured": bool(source.api_key), "http_statuses": [], "outbound_attempts": 0}
        try:
            if isinstance(source, SemanticScholarSource):
                row["quota_before"] = source.quota_snapshot()
                if row["quota_before"]["cooldown_remaining_seconds"] > 0:
                    row.update(status="skipped_cooldown", result_count=0)
                    return row
            client = await source._get_client()

            async def on_request(_request):
                row["outbound_attempts"] += 1

            async def on_response(response):
                row["http_statuses"].append(response.status_code)

            client.event_hooks = {"request": [on_request], "response": [on_response]}
            papers = await asyncio.wait_for(source.search(report["query"], max_results=5), 30)
            row.update(
                result_count=len(papers),
                status="results" if papers else ("error" if source.last_error else "empty"),
                source_error_present=bool(source.last_error),
                exact_title_match=any(p.title.strip().casefold() == report["query"].casefold() for p in papers),
                cache_hit_possible=bool(papers) and row["outbound_attempts"] == 0,
            )
        except Exception as exc:
            row.update(status="error", **safe_error(exc))
        finally:
            await source.close()
            row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            if isinstance(source, SemanticScholarSource):
                row["quota_after"] = source.quota_snapshot()
        return row

    common = {"timeout": 20, "max_retries": 0}
    sources = [
        CrossRefSource(email=settings.CROSSREF_EMAIL, **common),
        OpenAlexSource(email=settings.OPENALEX_EMAIL, api_key=settings.OPENALEX_API_KEY, **common),
        SemanticScholarSource(api_key=settings.SEMANTIC_SCHOLAR_API_KEY, **common),
        ArxivSource(**common),
    ]
    selected = getattr(args, "sources", None)
    if selected:
        sources = [source for source in sources if source.name in selected]
    report["sources"] = await asyncio.gather(*(probe(source) for source in sources))
    if getattr(args, "skip_zotero", False):
        report["zotero"] = {"status": "skipped_by_request", "writes_attempted": 0}
        report["scope"] = "One read-only search per selected academic source; no Zotero or generative-model calls."
        report["finished_at"] = datetime.now(UTC).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return
    started = time.perf_counter()
    try:
        client = ZoteroLocalClient(timeout=4)
        status = await client.status()
        collections = await client.collections()
        report["zotero"] = {
            "connected": status.connected,
            "version": status.zotero_version,
            "collections_returned": len(collections),
            "collection_limit": 100,
            "writes_attempted": 0,
        }
    except Exception as exc:
        report["zotero"] = {"connected": False, "writes_attempted": 0, **safe_error(exc)}
    report["zotero"]["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    report["finished_at"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sources", nargs="+", choices=["crossref", "openalex", "semantic_scholar", "arxiv"])
    parser.add_argument("--skip-zotero", action="store_true", help="Do not connect to the local reference manager")
    args = parser.parse_args()
    if not args.live:
        parser.error("No requests made. --live is required for network checks.")
    if not args.runtime.is_dir():
        parser.error("Runtime directory not found.")
    try:
        validate_output_path(args.runtime, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

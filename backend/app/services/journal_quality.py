"""Journal-level quality metadata with explicit provenance.

Commercial JCR data and the historical CAS partition table are never guessed.
Users can import data they are licensed to use.  OpenAlex metrics are fetched
separately and labelled as open indicators rather than official quartiles.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import time
from difflib import SequenceMatcher
from typing import Any, Optional

import httpx

from app.config import runtime_path, settings
from app.core.tls import load_ssl_context
from app.schemas.paper import PaperQuality


_DATA_PATH = runtime_path("journal_rankings.json")
_OPENALEX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OPENALEX_CACHE_SECONDS = 7 * 24 * 60 * 60
_OPENALEX_SEMAPHORE = asyncio.Semaphore(3)
_OPENALEX_BUDGET_SECONDS = 8.0


def normalize_journal_name(value: str) -> str:
    """Normalize a journal title for conservative exact matching."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def normalize_issn(value: str) -> str:
    return re.sub(r"[^0-9xX]", "", value or "").upper()


def _parse_quartile(value: Any, *, cas: bool = False) -> Optional[str]:
    text = str(value or "").strip().upper()
    if not text:
        return None
    match = re.search(r"Q\s*([1-4])", text)
    if not match and cas:
        match = re.search(r"([1-4])\s*区", str(value))
    if not match and text in {"1", "2", "3", "4"}:
        match = re.match(r"([1-4])", text)
    if not match:
        return None
    return f"{match.group(1)}区" if cas else f"Q{match.group(1)}"


def _normalized_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(key).casefold()): value
        for key, value in row.items()
    }


def _pick(row: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", alias.casefold())
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _load_dataset() -> dict[str, Any]:
    if not _DATA_PATH.exists():
        return {"version": 1, "entries": {}}
    try:
        with _DATA_PATH.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "entries": {}}


def ranking_status() -> dict[str, Any]:
    data = _load_dataset()
    entries = data.get("entries", {})
    return {
        "entry_count": len(entries),
        "updated_at": data.get("updated_at"),
        "filename": data.get("filename"),
        "sources": sorted({
            str(item.get("partition_source"))
            for item in entries.values()
            if item.get("partition_source")
        }),
    }


def import_ranking_content(content: str, filename: str) -> dict[str, Any]:
    """Import licensed JSON/CSV journal data into the user's runtime folder."""
    if len(content.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("分区文件不能超过 8 MB")

    filename_lower = (filename or "").casefold()
    rows: list[dict[str, Any]]
    if filename_lower.endswith(".json"):
        raw = json.loads(content)
        if isinstance(raw, dict):
            raw = raw.get("entries", raw.get("journals", raw.get("data", [])))
        if not isinstance(raw, list):
            raise ValueError("JSON 必须是期刊对象数组")
        rows = [item for item in raw if isinstance(item, dict)]
    else:
        sample = content[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(io.StringIO(content), dialect=dialect))

    entries: dict[str, dict[str, Any]] = {}
    for raw_row in rows[:100_000]:
        row = _normalized_row(raw_row)
        journal = _pick(
            row,
            "journal",
            "journal name",
            "title",
            "source title",
            "full journal title",
            "期刊",
            "期刊名称",
            "刊名",
        )
        if not journal:
            continue
        journal = str(journal).strip()
        key = normalize_journal_name(journal)
        if not key:
            continue

        jcr = _parse_quartile(_pick(row, "jcr quartile", "jif quartile", "jcr", "jcr分区"))
        cas = _parse_quartile(
            _pick(row, "cas quartile", "cas", "中科院分区", "大类分区", "分区"),
            cas=True,
        )
        sjr = _parse_quartile(
            _pick(row, "sjr best quartile", "sjr quartile", "best quartile", "sjr分区")
        )
        if not any((jcr, cas, sjr)):
            continue

        year_value = _pick(row, "year", "data year", "jcr year", "年份", "年度")
        try:
            year = int(float(str(year_value))) if year_value not in (None, "") else None
        except (TypeError, ValueError):
            year = None
        sjr_value = _pick(row, "sjr", "sjr score")
        try:
            sjr_score = float(str(sjr_value).replace(",", ".")) if sjr_value else None
        except (TypeError, ValueError):
            sjr_score = None

        source = _pick(row, "source", "data source", "来源")
        if not source:
            source = "用户授权导入"
        entry = {
            "journal": journal,
            "issn": normalize_issn(str(_pick(row, "issn", "issn-l", "issnl") or "")),
            "jcr_quartile": jcr,
            "cas_quartile": cas,
            "sjr_quartile": sjr,
            "sjr_score": sjr_score,
            "partition_year": year,
            "partition_source": str(source),
            "partition_status": "verified_import",
        }
        entries[key] = entry

    if not entries:
        raise ValueError(
            "没有识别到分区记录；至少需要期刊名以及 JCR/CAS/SJR 分区列"
        )

    from datetime import datetime, timezone

    data = {
        "version": 1,
        "filename": filename,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DATA_PATH.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return ranking_status()


def apply_local_ranking(quality: PaperQuality, venue: Optional[str]) -> PaperQuality:
    if not venue:
        return quality
    entry = _load_dataset().get("entries", {}).get(normalize_journal_name(venue))
    if not entry:
        return quality
    return quality.model_copy(update={
        "jcr_quartile": entry.get("jcr_quartile"),
        "cas_quartile": entry.get("cas_quartile"),
        "sjr_quartile": entry.get("sjr_quartile"),
        "sjr_score": entry.get("sjr_score"),
        "partition_year": entry.get("partition_year"),
        "partition_status": entry.get("partition_status", "verified_import"),
        "partition_source": entry.get("partition_source"),
        "matched_venue": entry.get("journal"),
        "match_confidence": 1.0,
    })


async def lookup_openalex_metrics(venue: str) -> dict[str, Any]:
    """Fetch open journal metrics without presenting them as JCR/CAS data."""
    key = normalize_journal_name(venue)
    if not key:
        return {}
    cached = _OPENALEX_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _OPENALEX_CACHE_SECONDS:
        return dict(cached[1])

    params = {
        "search": venue,
        "per-page": 3,
        "select": (
            "id,display_name,issn_l,type,works_count,cited_by_count,"
            "is_oa,is_in_doaj,summary_stats"
        ),
    }
    email = getattr(settings, "OPENALEX_EMAIL", None)
    if email:
        params["mailto"] = email

    # This optional enrichment shares one deadline for queueing, trust-store
    # preparation and the request; it must not stall paper details under load.
    async with asyncio.timeout(_OPENALEX_BUDGET_SECONDS), _OPENALEX_SEMAPHORE:
        context = await load_ssl_context()
        async with httpx.AsyncClient(
            timeout=_OPENALEX_BUDGET_SECONDS, follow_redirects=True, verify=context,
        ) as client:
            response = await client.get("https://api.openalex.org/sources", params=params)
            response.raise_for_status()
            candidates = response.json().get("results", [])

    best: Optional[dict[str, Any]] = None
    best_score = 0.0
    for candidate in candidates:
        candidate_name = candidate.get("display_name") or ""
        score = SequenceMatcher(
            None,
            normalize_journal_name(venue),
            normalize_journal_name(candidate_name),
        ).ratio()
        if score > best_score:
            best_score = score
            best = candidate
    if not best or best_score < 0.72:
        result: dict[str, Any] = {
            "partition_status": "unverified",
            "match_confidence": round(best_score, 3),
        }
    else:
        summary = best.get("summary_stats") or {}
        result = {
            "openalex_source_id": best.get("id"),
            "openalex_h_index": summary.get("h_index"),
            "openalex_2yr_mean_citedness": summary.get("2yr_mean_citedness"),
            "openalex_i10_index": summary.get("i10_index"),
            "openalex_works_count": best.get("works_count"),
            "openalex_cited_by_count": best.get("cited_by_count"),
            "openalex_is_in_doaj": best.get("is_in_doaj"),
            "matched_venue": best.get("display_name"),
            "match_confidence": round(best_score, 3),
            "partition_status": "open_metrics",
            "open_metrics_source": "OpenAlex Sources API",
        }
    _OPENALEX_CACHE[key] = (time.monotonic(), result)
    return dict(result)


async def lookup_journal_quality(
    venue: str,
    base_quality: Optional[PaperQuality] = None,
) -> PaperQuality:
    quality = base_quality or PaperQuality()
    quality = apply_local_ranking(quality, venue)
    try:
        open_metrics = await lookup_openalex_metrics(venue)
    except (httpx.HTTPError, ValueError, TimeoutError, RuntimeError, OSError):
        open_metrics = {}
    if open_metrics:
        # Imported licensed data remains authoritative for official partitions.
        status = quality.partition_status
        if status == "unverified":
            status = open_metrics.get("partition_status", status)
        quality = quality.model_copy(update={**open_metrics, "partition_status": status})
    return quality

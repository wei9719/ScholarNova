"""Bounded, provenance-preserving journal discovery without a generated ranking."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.core.tls import load_ssl_context
from app.schemas.journals import (
    JournalDiscoveryRequest, JournalDiscoveryResponse, JournalMetric,
    JournalPapersRequest, JournalPapersResponse, JournalRecommendation,
)
from app.services.journal_quality import local_journal_record, normalize_journal_name
from app.services.search.query_planner import QueryPlanner
from app.services.sources.openalex import OpenAlexSource


_HTTP_GATE = asyncio.Semaphore(2)
_BUDGET = 14.0
_CACHE_TTL = 600
_CACHE: dict[str, tuple[float, JournalDiscoveryResponse]] = {}
_SOURCE_ID = re.compile(r"S[1-9][0-9]{0,15}")


class NotJournalError(ValueError):
    """A resolved source is not a journal, distinct from malformed upstream data."""


def source_id(value: Any) -> str | None:
    text = str(value or "").removeprefix("https://openalex.org/")
    return text if _SOURCE_ID.fullmatch(text) else None


def safe_url(value: Any) -> str | None:
    text = str(value or "")
    try:
        parts = urlsplit(text)
        if parts.scheme in {"https", "http"} and parts.hostname and not parts.username and not parts.password:
            return text
    except ValueError:
        pass
    return None


def _issns(source: dict) -> list[str]:
    extra = source.get("issn") or []
    values = [source.get("issn_l"), *(extra if isinstance(extra, list) else [])]
    return list(dict.fromkeys(str(value).upper() for value in values
                             if re.fullmatch(r"\d{4}-\d{3}[\dXx]", str(value or ""))))


def _is_journal(source: dict) -> bool:
    return (isinstance(source, dict) and source.get("type") == "journal"
            and bool(str(source.get("display_name") or "").strip())
            and bool(source_id(source.get("id"))) and bool(_issns(source)))


def _metrics(source: dict) -> list[JournalMetric]:
    result: list[JournalMetric] = []
    summary = source.get("summary_stats") or {}
    if not isinstance(summary, dict):
        summary = {}
    metadata_url = f"https://openalex.org/{source_id(source.get('id'))}"
    for key, name in [("h_index", "OpenAlex H-index"), ("2yr_mean_citedness", "OpenAlex 近两年篇均被引")]:
        value = summary.get(key)
        if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            result.append(JournalMetric(name=name, value=value, source="OpenAlex Sources API",
                                       source_url=metadata_url, basis="当前 API 快照；未提供指标年度，不是 JIF/JCR 分区"))
    imported = local_journal_record(source.get("display_name") or "", _issns(source))
    jif = imported.get("jif")
    if isinstance(jif, (int, float)) and math.isfinite(jif) and jif >= 0 and imported.get("jif_year"):
        result.insert(0, JournalMetric(name="JIF（用户授权导入）", value=jif, year=imported["jif_year"],
                                      source=imported.get("jif_source") or "用户授权导入", basis="本地授权数据；未通过公开 API 推测"))
    for key, name in [("jcr_quartile", "JCR 分区（用户授权导入）"), ("cas_quartile", "中科院分区（用户授权导入）"), ("sjr_quartile", "SJR 分区（用户授权导入）")]:
        if imported.get(key):
            result.append(JournalMetric(name=name, value=str(imported[key]), year=imported.get("partition_year"),
                                        source=imported.get("partition_source") or "用户授权导入", basis="本地授权数据"))
    return result


async def _get(path: str, params: dict[str, Any]) -> dict:
    # The deadline includes queueing, certificate preparation and all HTTP work.
    async with asyncio.timeout(7), _HTTP_GATE:
        context = await load_ssl_context()
        async with httpx.AsyncClient(timeout=6, verify=context, follow_redirects=False) as client:
            email = getattr(settings, "OPENALEX_EMAIL", None)
            response = await client.get(f"https://api.openalex.org/{path}", params={
                **params, **({"mailto": email} if email else {}),
            })
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid OpenAlex response")
            return data


def _query(query: str) -> str:
    return QueryPlanner._translate_to_english(query)[:300]


async def discover_journals(request: JournalDiscoveryRequest) -> JournalDiscoveryResponse:
    key = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True).encode()).hexdigest()
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL:
        return cached[1].model_copy(deep=True, update={"cached": True})
    result = JournalDiscoveryResponse(query=request.query, candidate_count=len(request.candidates), checked_at=datetime.now(timezone.utc))
    groups: dict[str, list] = {}
    for candidate in request.candidates:
        normalized = normalize_journal_name(candidate.venue or "")
        if normalized:
            groups.setdefault(normalized, []).append(candidate)
    groups = dict(sorted(groups.items(), key=lambda item: -len(item[1]))[:12])
    sources: dict[str, dict] = {}
    external_counts: Counter = Counter()
    network_failed = False
    try:
        async with asyncio.timeout(_BUDGET):
            try:
                data = await _get("works", {"search": _query(request.query), "per_page": 40,
                                            "select": "id,title,primary_location"})
                works = data.get("results") or []
                if not isinstance(works, list):
                    raise ValueError("invalid OpenAlex results")
                result.discovery_candidate_count = len(works)
                for work in works:
                    if not isinstance(work, dict) or not isinstance(work.get("primary_location"), dict):
                        continue
                    source = work["primary_location"].get("source") or {}
                    if _is_journal(source):
                        sid = source_id(source["id"])
                        sources[sid] = source
                        external_counts[sid] += 1
            except (httpx.HTTPError, ValueError, TimeoutError, RuntimeError, OSError):
                network_failed = True

            known_names = {normalize_journal_name(value.get("display_name") or "") for value in sources.values()}
            missing = [rows[0].venue for name, rows in groups.items() if name not in known_names][:4]

            async def match_name(name: str):
                nonlocal network_failed
                try:
                    data = await _get("sources", {"search": name, "filter": "type:journal", "per_page": 3})
                    rows = data.get("results") or []
                    if not isinstance(rows, list):
                        raise ValueError("invalid OpenAlex results")
                    return next((item for item in rows if _is_journal(item)
                                 and normalize_journal_name(item.get("display_name") or "") == normalize_journal_name(name)), None)
                except (httpx.HTTPError, ValueError, TimeoutError, RuntimeError, OSError):
                    network_failed = True
                    return None

            for source in await asyncio.gather(*(match_name(name) for name in missing)):
                if source:
                    sources[source_id(source["id"])] = source

            # Work records lack homepage/summary_stats: enrich at most six sources.
            ordered = sorted(sources.values(), key=lambda item: (
                -len(groups.get(normalize_journal_name(item.get("display_name") or ""), [])),
                -external_counts[source_id(item["id"])], item.get("display_name") or "",
            ))[:6]

            async def enrich(source: dict):
                nonlocal network_failed
                if "summary_stats" in source:
                    return source
                try:
                    details = await _get(f"sources/{source_id(source['id'])}", {})
                    if _is_journal(details) and source_id(details.get("id")) == source_id(source["id"]):
                        return details
                except (httpx.HTTPError, ValueError, TimeoutError, RuntimeError, OSError):
                    network_failed = True
                return source

            for source in await asyncio.gather(*(enrich(source) for source in ordered)):
                sources[source_id(source["id"])] = source
    except TimeoutError:
        network_failed = True

    verified_names = set()
    for source in sources.values():
        name = source.get("display_name") or ""
        normalized = normalize_journal_name(name)
        verified_names.add(normalized)
        rows = groups.get(normalized, [])
        count = external_counts[source_id(source["id"])]
        result.journals.append(JournalRecommendation(
            name=name, source_id=source_id(source["id"]), issns=_issns(source), verification="verified_journal",
            homepage_url=safe_url(source.get("homepage_url")),
            reason=f"当前检索结果中有 {len(rows)} 篇；关键词补充检索样本中有 {count} 篇发表于此刊。期刊类型、Source ID 和 ISSN 已由 OpenAlex 元数据核对。",
            candidate_match_count=len(rows), candidate_paper_ids=[item.id for item in rows], discovery_match_count=count,
            metrics=_metrics(source),
        ))
    result.journals.sort(key=lambda item: (-item.candidate_match_count, -item.discovery_match_count, item.name))
    result.journals = result.journals[:8]
    for name, rows in groups.items():
        if name not in verified_names and len(result.journals) < 12:
            result.journals.append(JournalRecommendation(name=rows[0].venue, candidate_match_count=len(rows),
                candidate_paper_ids=[item.id for item in rows], reason="仅按当前候选的发表源名称聚合，尚未核验是否为期刊；会议、书系和预印本也可能在此，不能用于投稿判断。"))
    if network_failed:
        result.warnings.append("OpenAlex 补充查询未全部完成；已保留当前候选的发表源聚合。待核验项不作为已确认期刊或投稿建议。")
    result.warnings.append("作者指南请从已核验期刊主页进入并核对；未获得权威指南链接时不自动拼接。正式 JIF 仅展示本机授权导入且带年份的数据。")
    if not network_failed:
        if len(_CACHE) >= 128:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.monotonic(), result.model_copy(deep=True))
    return result


async def journal_papers(sid: str, request: JournalPapersRequest) -> JournalPapersResponse:
    result = JournalPapersResponse(source_id=sid, query=request.query)
    try:
        async with asyncio.timeout(_BUDGET):
            source = await _get(f"sources/{sid}", {})
            if not _is_journal(source) or source_id(source.get("id")) != sid:
                raise NotJournalError("该发表源尚未核验为期刊")
            filters = [f"primary_location.source.id:{sid}"]
            if request.date_from:
                filters.append(f"from_publication_date:{request.date_from.isoformat()}")
            if request.date_to:
                filters.append(f"to_publication_date:{request.date_to.isoformat()}")
            data = await _get("works", {"search": _query(request.query), "filter": ",".join(filters), "per_page": request.limit})
            parser = OpenAlexSource(max_retries=0)
            works = data.get("results") or []
            if not isinstance(works, list):
                raise ValueError("invalid OpenAlex results")
            for work in works:
                # Do not trust the API filter alone when selecting a journal.
                if not isinstance(work, dict) or not isinstance(work.get("primary_location"), dict):
                    continue
                location_source = work["primary_location"].get("source") or {}
                if not isinstance(location_source, dict):
                    continue
                if source_id(location_source.get("id")) == sid and (paper := parser._parse_paper(work)) and paper.title.strip():
                    result.papers.append(paper)
    except NotJournalError:
        raise
    except (httpx.HTTPError, TimeoutError, RuntimeError, OSError, ValueError):
        result.warnings.append("期刊论文查询暂未完成，请稍后重试；当前搜索结果没有被清除。")
    return result

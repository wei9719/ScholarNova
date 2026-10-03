"""
搜索相关 API 端点
"""

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import check_rate_limit
from app.core.capacity import CapacityExceeded, busy_response
from app.database import get_db
from app.models.search_run import SearchRun
from app.schemas.search import SearchRequest, SearchResponse, SearchRunDetail, SearchStatus

logger = logging.getLogger(__name__)

router = APIRouter()
_running_search_tasks: set[asyncio.Task[None]] = set()

_SOURCE_DESCRIPTORS: dict[str, dict[str, str]] = {
    "retrieval_limit": {
        "label": "检索容量保护",
        "api_name": "本地策略 / 未请求外部 API",
        "endpoint": "",
    },
    "semantic_scholar": {
        "label": "Semantic Scholar",
        "api_name": "Semantic Scholar Graph API",
        "endpoint": "api.semanticscholar.org/graph/v1/paper/search",
    },
    "openalex": {
        "label": "OpenAlex",
        "api_name": "OpenAlex Works API",
        "endpoint": "api.openalex.org/works",
    },
    "crossref": {
        "label": "Crossref",
        "api_name": "Crossref REST API",
        "endpoint": "api.crossref.org/works",
    },
    "arxiv": {
        "label": "arXiv",
        "api_name": "arXiv Atom API",
        "endpoint": "export.arxiv.org/api/query",
    },
    "zotero": {
        "label": "Zotero 本地文献库",
        "api_name": "ScholarNova Local Library",
        "endpoint": "local database (no network request)",
    },
    "semantic_scholar_batch": {
        "label": "Semantic Scholar",
        "api_name": "Semantic Scholar Batch API",
        "endpoint": "api.semanticscholar.org/graph/v1/paper/batch",
    },
    "semantic_scholar_exact": {
        "label": "Semantic Scholar",
        "api_name": "Semantic Scholar Paper API",
        "endpoint": "api.semanticscholar.org/graph/v1/paper/{id}",
    },
}


def _source_key(source: Any) -> str:
    value = source.value if hasattr(source, "value") else str(source)
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _source_call(source: Any, **values: Any) -> dict[str, Any]:
    key = _source_key(source)
    descriptor = _SOURCE_DESCRIPTORS.get(
        key,
        {"label": str(source), "api_name": str(source), "endpoint": ""},
    )
    return {"source": key, **descriptor, **values}


def _status_call(status: Any) -> dict[str, Any]:
    return _source_call(
        status.source,
        query=status.query,
        status="completed" if status.success else "failed",
        success=status.success,
        paper_count=status.paper_count,
        elapsed_ms=round(status.elapsed_ms, 2),
        error=status.error,
    )


def _api_call_count(statuses: list[Any]) -> int:
    """Capacity notices describe skipped work, not external API requests."""
    return sum(_source_key(status.source) != "retrieval_limit" for status in statuses)


def _merge_source_calls(
    planned: list[dict[str, Any]], statuses: list[Any]
) -> list[dict[str, Any]]:
    remaining = [dict(item) for item in planned]
    completed: list[dict[str, Any]] = []
    for status in statuses:
        completed.append(_status_call(status))
        key = _source_key(status.source)
        query = status.query
        index = next(
            (
                i
                for i, item in enumerate(remaining)
                if item.get("source") == key
                and (query is None or item.get("query") == query)
            ),
            None,
        )
        if index is not None:
            remaining.pop(index)
    return completed + remaining


async def _fail_search(run_id: str, message: str) -> None:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(select(SearchRun).where(SearchRun.id == run_id))
        run = result.scalar_one_or_none()
        if run and run.status in {"pending", "running"}:
            run.mark_as_failed(message)
            run.update_progress({**(run.progress or {}), "current_phase": "failed", "message": message})
            await db.commit()


async def _bounded_search(run_id: str, request: SearchRequest, lease) -> None:
    from app.config import settings

    try:
        async with lease:
            await asyncio.wait_for(_execute_search_task(run_id, request), settings.SEARCH_TIMEOUT)
    except CapacityExceeded:
        await _fail_search(run_id, "搜索排队超时，请稍后重新检索")
    except TimeoutError:
        await _fail_search(run_id, "搜索超过总时限，请缩小范围后重试")
    except asyncio.CancelledError:
        await _fail_search(run_id, "搜索已取消或应用正在关闭")
        raise
    finally:
        lease.release()


def _start_search_task(run_id: str, request: SearchRequest, lease) -> None:
    """Start and retain a search task until it completes."""
    task = asyncio.create_task(
        _bounded_search(run_id, request, lease), name=f"search:{run_id}"
    )
    _running_search_tasks.add(task)
    def finished(completed):
        lease.release()  # Also handles cancellation before the coroutine starts.
        _running_search_tasks.discard(completed)
        if not completed.cancelled() and completed.exception():
            logger.error("Search worker cleanup failed", exc_info=completed.exception())
    task.add_done_callback(finished)


async def shutdown_search_tasks() -> None:
    tasks = list(_running_search_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    # A task cancelled before its first scheduling turn never reaches the
    # coroutine's cancellation handler. Its persisted run must not stay pending.
    for task in tasks:
        run_id = task.get_name().removeprefix("search:")
        try:
            await _fail_search(run_id, "搜索已取消或应用正在关闭")
        except Exception:
            logger.exception("Could not persist search shutdown status", extra={"run_id": run_id})


def _collect_constraints(request: SearchRequest):
    """合并高级筛选字段，保持原 SearchRequest 接口不变。"""
    from app.schemas.query import Constraint

    constraints = list(request.constraints)
    if request.date_from:
        constraints.append(Constraint(
            key="year", operator="gte", value=int(request.date_from[:4]),
            description=f"Published after {request.date_from}",
        ))
    if request.date_to:
        constraints.append(Constraint(
            key="year", operator="lte", value=int(request.date_to[:4]),
            description=f"Published before {request.date_to}",
        ))
    if request.min_citations is not None:
        constraints.append(Constraint(
            key="min_citations", operator="gte", value=request.min_citations,
            description=f"Minimum {request.min_citations} citations",
        ))
    if request.open_access_only:
        constraints.append(Constraint(
            key="open_access", operator="eq", value=True,
            description="Open access only",
        ))
    return constraints


def _should_refine(request: SearchRequest, paper_count: int, successful_sources: int) -> bool:
    """仅在首轮低召回时进入二轮，避免无条件增加 API 成本。"""
    enabled = request.preferences.get("iterative_search", True)
    if not enabled or successful_sources == 0:
        return False
    target = min(request.max_results, 20)
    return paper_count < max(8, target // 2)


def _build_result_summary(papers, query_plan) -> dict[str, Any]:
    """构造无需额外模型调用的结构化结果概览。"""
    high = sum(1 for p in papers if (p.relevance_score or 0) >= 0.70)
    partial = sum(1 for p in papers if 0.45 <= (p.relevance_score or 0) < 0.70)
    years = [p.year for p in papers if p.year is not None]
    venues: dict[str, int] = {}
    for paper in papers:
        if paper.venue:
            venues[paper.venue] = venues.get(paper.venue, 0) + 1
    top_venues = [
        {"name": name, "count": count}
        for name, count in sorted(venues.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    return {
        "total": len(papers),
        "highly_relevant": high,
        "partially_relevant": partial,
        "other": max(0, len(papers) - high - partial),
        "year_range": [min(years), max(years)] if years else [],
        "top_venues": top_venues,
        "intent": query_plan.intent,
        "entity_dimensions": {
            key: values for key, values in query_plan.entities.items() if values
        },
    }


async def _execute_search_task(run_id: str, request: SearchRequest) -> None:
    """
    后台执行搜索任务

    Args:
        run_id: 搜索运行 ID
        request: 搜索请求
    """
    from app.config import settings
    from app.database import async_session_factory
    from app.services.inference import RoutedLLMGateway
    from app.services.search.deduplicator import Deduplicator
    from app.services.search.query_planner import QueryPlanner
    from app.services.search.ranker import Ranker
    from app.services.search.retriever import Retriever, SourceStatus
    from app.services.sources.arxiv import ArxivSource
    from app.services.sources.crossref import CrossRefSource
    from app.services.sources.local_library import LocalLibrarySource
    from app.services.sources.openalex import OpenAlexSource
    from app.services.sources.semantic_scholar import SemanticScholarSource

    start_time = time.time()

    async with async_session_factory() as db:
        search_run: SearchRun | None = None
        sources_map = {}
        try:
            # 获取搜索运行记录
            result = await db.execute(select(SearchRun).where(SearchRun.id == run_id))
            search_run = result.scalar_one_or_none()
            if not search_run:
                return

            # 标记为运行中
            search_run.mark_as_running()
            search_run.update_progress({
                "total_sources": len(request.sources),
                "completed_sources": 0,
                "total_papers": 0,
                "deduplicated_papers": 0,
                "current_phase": "planning",
            })
            await db.commit()

            # 初始化服务
            llm_gateway = RoutedLLMGateway(task="query_planning")
            planner = QueryPlanner(llm_gateway=llm_gateway)
            all_constraints = _collect_constraints(request)

            # 1. 查询规划
            query_plan = await planner.plan(
                query=request.query,
                sources=request.sources,
                user_constraints=all_constraints or None,
                planning_mode=request.planning_mode,
            )
            search_run.query_plan = query_plan.model_dump()
            planned_calls = [
                _source_call(
                    sub_query.source,
                    query=sub_query.query,
                    status="pending",
                    success=None,
                    paper_count=0,
                    elapsed_ms=0,
                    error=None,
                )
                for sub_query in query_plan.sub_queries
            ]
            live_statuses: list[SourceStatus] = []
            search_run.source_status = {
                "calls": planned_calls,
                "search_rounds": 0,
                "api_calls": 0,
            }
            search_run.update_progress({
                "total_sources": len(planned_calls),
                "completed_sources": 0,
                "total_papers": 0,
                "deduplicated_papers": 0,
                "current_phase": "searching",
                "search_rounds": 0,
                "api_calls": 0,
                "latency_ms": (time.time() - start_time) * 1000,
                "source_calls": planned_calls,
            })
            await db.commit()

            # 2. 初始化数据源
            sources_map = {}
            for source_type in request.sources:
                source_value = source_type.value if hasattr(source_type, 'value') else source_type
                if source_value == "semantic_scholar":
                    sources_map[source_type] = SemanticScholarSource(
                        api_key=settings.SEMANTIC_SCHOLAR_API_KEY,
                    )
                elif source_value == "openalex":
                    sources_map[source_type] = OpenAlexSource(
                        email=settings.OPENALEX_EMAIL,
                        api_key=settings.OPENALEX_API_KEY,
                    )
                elif source_value == "crossref":
                    sources_map[source_type] = CrossRefSource(
                        email=settings.CROSSREF_EMAIL,
                    )
                elif source_value == "arxiv":
                    sources_map[source_type] = ArxivSource()
                elif source_value == "zotero":
                    sources_map[source_type] = LocalLibrarySource()

            print(f"[SEARCH] sources_map keys: {list(sources_map.keys())}")
            logger.debug(
                "Planned sub-queries",
                extra={"sub_queries": [sq.model_dump() for sq in query_plan.sub_queries]},
            )

            async def report_source_progress(status: SourceStatus) -> None:
                live_statuses.append(status)
                source_calls = _merge_source_calls(planned_calls, live_statuses)
                search_run.source_status = {
                    "calls": source_calls,
                    "search_rounds": 1,
                    "api_calls": _api_call_count(live_statuses),
                }
                search_run.update_progress({
                    **(search_run.progress or {}),
                    "total_sources": len(planned_calls),
                    "completed_sources": len(live_statuses),
                    "total_papers": sum(item.paper_count for item in live_statuses),
                    "current_phase": "searching",
                    "search_rounds": 1,
                    "api_calls": _api_call_count(live_statuses),
                    "latency_ms": (time.time() - start_time) * 1000,
                    "source_calls": source_calls,
                })
                await db.commit()

            # 3. 检索。每个 API 完成时立即写入进度。
            retriever = Retriever(sources=sources_map, timeout=45)
            retrieve_result = await retriever.retrieve(
                sub_queries=query_plan.sub_queries,
                max_results=request.max_results,
                progress_callback=report_source_progress,
            )
            papers = list(retrieve_result.papers)
            all_statuses = list(retrieve_result.source_statuses)
            search_rounds = 1
            constraint_ranker = Ranker(intent=query_plan.intent or "open_exploration")
            first_pass_eligible = constraint_ranker.filter_hard_constraints(
                papers,
                query_plan.constraints,
            )

            # 仅首轮低召回时做一次有界扩展，正常查询不增加调用。
            if _should_refine(
                request,
                len(first_pass_eligible),
                retrieve_result.successful_sources,
            ):
                refinement_queries = planner.build_refinement_subqueries(
                    query_plan,
                    request.sources,
                )
                if refinement_queries:
                    planned_calls.extend(
                        _source_call(
                            sub_query.source,
                            query=sub_query.query,
                            status="pending",
                            success=None,
                            paper_count=0,
                            elapsed_ms=0,
                            error=None,
                            round=2,
                        )
                        for sub_query in refinement_queries
                    )
                    search_run.update_progress({
                        **(search_run.progress or {}),
                        "total_sources": len(planned_calls),
                        "completed_sources": len(live_statuses),
                        "total_papers": len(papers),
                        "deduplicated_papers": len(papers),
                        "current_phase": "refining",
                        "search_rounds": 1,
                        "api_calls": _api_call_count(all_statuses),
                        "latency_ms": (time.time() - start_time) * 1000,
                        "source_calls": _merge_source_calls(
                            planned_calls, live_statuses
                        ),
                    })
                    await db.commit()
                    refined_result = await retriever.retrieve(
                        sub_queries=refinement_queries,
                        max_results=min(request.max_results, 25),
                        progress_callback=report_source_progress,
                    )
                    papers.extend(refined_result.papers)
                    all_statuses.extend(refined_result.source_statuses)
                    search_rounds = 2

            api_calls = _api_call_count(all_statuses)
            print(f"[SEARCH] retrieved {len(papers)} papers")
            for s in all_statuses:
                logger.debug(
                    "Source result: %s success=%s papers=%s error=%s",
                    s.source,
                    s.success,
                    s.paper_count,
                    s.error,
                )

            search_run.update_progress({
                **(search_run.progress or {}),
                "total_sources": len(planned_calls),
                "completed_sources": len(live_statuses),
                "total_papers": len(papers),
                "deduplicated_papers": len(papers),
                "current_phase": "deduplicating",
                "search_rounds": search_rounds,
                "api_calls": api_calls,
                "latency_ms": (time.time() - start_time) * 1000,
                "source_calls": _merge_source_calls(planned_calls, all_statuses),
            })
            await db.commit()

            # 4. 去重
            deduplicator = Deduplicator()
            unique_papers = deduplicator.deduplicate(papers)

            # 使用 Semantic Scholar 官方 batch 接口一次性回填其他来源论文的
            # CorpusId。该步骤最多增加一次 API 调用，避免逐篇请求，并为
            # Asta/PaSa 的确定性评测提供统一标识。
            semantic_source = next(
                (
                    source
                    for source in sources_map.values()
                    if isinstance(source, SemanticScholarSource)
                ),
                None,
            )
            missing_corpus_ids = sum(
                1
                for paper in unique_papers
                if not paper.corpus_id
                and (
                    paper.doi
                    or "arxiv.org/" in (paper.url or "").lower()
                    or "arxiv.org/" in (paper.pdf_url or "").lower()
                )
            )
            if (
                semantic_source
                and settings.SEMANTIC_SCHOLAR_API_KEY
                and missing_corpus_ids
            ):
                enrichment_started = time.monotonic()
                try:
                    enriched = await asyncio.wait_for(
                        semantic_source.enrich_with_corpus_ids(unique_papers),
                        timeout=50,
                    )
                    resolved_count = sum(
                        1
                        for before, after in zip(unique_papers, enriched)
                        if not before.corpus_id and after.corpus_id
                    )
                    unique_papers = enriched
                    all_statuses.append(
                        SourceStatus(
                            source="semantic_scholar_batch",
                            success=not bool(semantic_source.last_error),
                            paper_count=resolved_count,
                            elapsed_ms=(
                                time.monotonic() - enrichment_started
                            ) * 1000,
                            error=semantic_source.last_error,
                        )
                    )
                except Exception as enrichment_error:
                    all_statuses.append(
                        SourceStatus(
                            source="semantic_scholar_batch",
                            success=False,
                            elapsed_ms=(
                                time.monotonic() - enrichment_started
                            ) * 1000,
                            error=str(enrichment_error),
                        )
                    )

            api_calls = _api_call_count(all_statuses)
            eligible_papers = constraint_ranker.filter_hard_constraints(
                unique_papers,
                query_plan.constraints,
            )

            search_run.update_progress({
                **(search_run.progress or {}),
                "total_sources": len(planned_calls),
                "completed_sources": len(live_statuses),
                "total_papers": len(papers),
                "deduplicated_papers": len(eligible_papers),
                "current_phase": "ranking",
                "search_rounds": search_rounds,
                "api_calls": api_calls,
                "latency_ms": (time.time() - start_time) * 1000,
                "source_calls": _merge_source_calls(planned_calls, all_statuses),
            })
            await db.commit()

            # 5. 排序
            ranker = constraint_ranker
            result_limit = (
                (
                    1
                    if planner.is_confident_single_paper_lookup(request.query)
                    else min(request.max_results, 10)
                )
                if query_plan.intent == "exact_lookup"
                else request.max_results
            )
            ranked_papers = await ranker.rank(
                papers=eligible_papers,
                query=request.query,
                limit=result_limit,
                constraints=query_plan.constraints,
            )
            # 仅应用用户导入的真实分区数据；开放指标稍后按可见卡片异步补齐。
            from app.services.journal_quality import apply_local_ranking

            for paper in ranked_papers:
                if paper.quality:
                    paper.quality = apply_local_ranking(paper.quality, paper.venue)
            if (
                semantic_source
                and settings.SEMANTIC_SCHOLAR_API_KEY
                and query_plan.intent == "exact_lookup"
                and ranked_papers
                and not ranked_papers[0].corpus_id
            ):
                identifier = semantic_source.identifier_for_paper(ranked_papers[0])
                if identifier:
                    resolve_started = time.monotonic()
                    exact_paper = await semantic_source.get_paper(identifier)
                    resolved = bool(exact_paper and exact_paper.corpus_id)
                    if resolved:
                        ranked_papers[0] = ranked_papers[0].model_copy(
                            update={
                                "corpus_id": exact_paper.corpus_id,
                                "citation_count": max(
                                    ranked_papers[0].citation_count,
                                    exact_paper.citation_count,
                                ),
                            }
                        )
                    all_statuses.append(
                        SourceStatus(
                            source="semantic_scholar_exact",
                            success=resolved,
                            paper_count=1 if resolved else 0,
                            elapsed_ms=(
                                time.monotonic() - resolve_started
                            ) * 1000,
                            error=None if resolved else semantic_source.last_error,
                        )
                    )
                    api_calls = _api_call_count(all_statuses)
            print(f"[SEARCH] ranked_papers: {len(ranked_papers)}")
            if ranked_papers:
                print(f"[SEARCH] first paper title: {ranked_papers[0].title[:50]}")

            # 6. 完成：先发布缓存结果，再进行非阻塞的论文持久化。
            latency_ms = (time.time() - start_time) * 1000
            search_run.latency_ms = latency_ms
            search_run.model_name = settings.OPENAI_DEFAULT_MODEL
            search_run.source_status = {
                "calls": [_status_call(status) for status in all_statuses],
                "search_rounds": search_rounds,
                "api_calls": api_calls,
            }
            usage = llm_gateway.usage
            search_run.token_usage = {
                **usage,
                "mode": (
                    "llm_query_planning"
                    if planner.planning_mode == "ai"
                    else "rule_based_query_planning"
                ),
                "planning_mode": planner.planning_mode,
                "planning_reason": planner.planning_reason,
            }
            result_summary = _build_result_summary(ranked_papers, query_plan)
            search_run.mark_as_completed()
            search_run.update_progress({
                **(search_run.progress or {}),
                "total_sources": len(planned_calls),
                "completed_sources": len(planned_calls),
                "total_papers": len(papers),
                "deduplicated_papers": len(eligible_papers),
                "current_phase": "caching",
                "search_rounds": search_rounds,
                "api_calls": api_calls,
                "latency_ms": latency_ms,
                "result_summary": result_summary,
                "source_calls": [_status_call(status) for status in all_statuses],
            })

            # 将结果存储到缓存
            from app.core.cache import CacheManager
            cache = CacheManager()
            papers_json = [p.model_dump(mode="json") for p in ranked_papers]
            print(f"[SEARCH] caching {len(papers_json)} papers")
            await cache.set(
                f"search_results:{run_id}",
                papers_json,
                ttl=3600,
            )

            search_run.update_progress({
                **(search_run.progress or {}),
                "current_phase": "completed",
                "latency_ms": latency_ms,
            })
            await db.commit()

            # Persist paper detail records after the run is visible as completed.
            try:
                from app.models.paper import PaperEntity

                ranked_ids = [str(p.id) for p in ranked_papers]
                existing_ids = set()
                if ranked_ids:
                    existing_ids = set(
                        (
                            await db.execute(
                                select(PaperEntity.id).where(PaperEntity.id.in_(ranked_ids))
                            )
                        ).scalars().all()
                    )
                for p in ranked_papers:
                    if str(p.id) in existing_ids:
                        continue
                    db.add(PaperEntity(
                        id=str(p.id),
                        title=p.title,
                        abstract=p.abstract,
                        authors=[{"name": a} for a in p.authors] if p.authors else [],
                        year=p.year,
                        venue=p.venue,
                        doi=p.doi,
                        url=p.url,
                        pdf_url=p.pdf_url,
                        source=p.source,
                        external_id=p.corpus_id,
                        citation_count=p.citation_count,
                        is_open_access=p.is_open_access,
                    ))
                await db.commit()
            except Exception as persist_error:
                await db.rollback()
                logger.warning(
                    "Long-tail paper persistence failed",
                    extra={"run_id": run_id, "error": str(persist_error)},
                )

        except Exception as e:
            if search_run is not None:
                try:
                    search_run.mark_as_failed(str(e))
                    await db.commit()
                except Exception:
                    await db.rollback()
            logger.exception(
                "Background search task failed",
                extra={"run_id": run_id},
            )
        finally:
            await asyncio.gather(
                *(source.close() for source in sources_map.values()),
                return_exceptions=True,
            )


@router.post("", response_model=SearchResponse, status_code=202)
async def create_search(
    request: SearchRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """
    创建复杂查询搜索

    按所选模式生成规则或 AI 查询计划，然后并行检索多个学术数据源。
    """
    # 速率限制检查
    rate_limit_response = check_rate_limit(http_request, endpoint_type="search")
    if rate_limit_response:
        return rate_limit_response

    try:
        lease = http_request.app.state.search_capacity.reserve()
    except CapacityExceeded:
        return busy_response()

    # 创建 SearchRun 记录
    run_id = str(uuid.uuid4())
    search_run = SearchRun(
        id=run_id,
        raw_query=request.query,
        progress={"current_phase": "queued"},
        sources=[s.value for s in request.sources],
        max_results=request.max_results,
        filters={
            "planning_mode": request.planning_mode,
            "date_from": request.date_from,
            "date_to": request.date_to,
            "min_citations": request.min_citations,
            "open_access_only": request.open_access_only,
            "constraints": [c.model_dump() for c in request.constraints],
            "preferences": request.preferences,
        },
    )
    try:
        db.add(search_run)
        await db.commit()
        # Reservation follows the job, not the short-lived HTTP 202 response.
        _start_search_task(run_id, request, lease)
    except BaseException:
        lease.release()
        raise

    return SearchResponse(
        run_id=search_run.id,
        status=SearchStatus.PENDING,
        query_plan=None,
        papers=[],
        matched_constraints=[],
        evidence=[],
        recommendation_reason=None,
        uncertainty=None,
        coverage=None,
        message="搜索任务已创建，正在处理中...",
    )


@router.get("/{run_id}", response_model=SearchRunDetail)
async def get_search_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> SearchRunDetail:
    """
    获取搜索运行详情

    获取指定搜索运行的状态、进度和结果
    """
    result = await db.execute(select(SearchRun).where(SearchRun.id == run_id))
    search_run = result.scalar_one_or_none()

    if not search_run:
        raise HTTPException(status_code=404, detail="Search run not found")

    # 从缓存获取结果
    papers = []
    if search_run.is_completed:
        from app.core.cache import CacheManager
        cache = CacheManager()
        cached_results = await cache.get(f"search_results:{run_id}")
        if cached_results:
            from app.schemas.paper import Paper
            papers = [Paper(**p) for p in cached_results]

    source_status = search_run.source_status or {}
    source_calls: list[dict[str, Any]] = (
        source_status.get("calls", [])
        if isinstance(source_status, dict)
        else source_status
    )
    progress = search_run.progress or {}
    result_summary = progress.get("result_summary") or {}
    runtime_metrics = {
        "api_calls": source_status.get("api_calls", progress.get("api_calls", 0))
        if isinstance(source_status, dict) else progress.get("api_calls", 0),
        "search_rounds": source_status.get("search_rounds", progress.get("search_rounds", 0))
        if isinstance(source_status, dict) else progress.get("search_rounds", 0),
        "latency_ms": round(search_run.latency_ms or progress.get("latency_ms", 0), 2),
        "token_usage": search_run.token_usage or {},
        "successful_calls": sum(
            1 for item in source_calls
            if item.get("source") != "retrieval_limit" and item.get("success")
        ),
        "failed_calls": sum(
            1 for item in source_calls
            if item.get("source") != "retrieval_limit" and not item.get("success")
        ),
    }

    return SearchRunDetail(
        run_id=search_run.id,
        status=SearchStatus(search_run.status),
        original_query=search_run.raw_query,
        query_plan=search_run.query_plan,
        progress=search_run.progress,
        results=papers,
        source_status=source_calls,
        runtime_metrics=runtime_metrics,
        result_summary=result_summary,
        created_at=search_run.created_at,
        completed_at=search_run.completed_at,
    )

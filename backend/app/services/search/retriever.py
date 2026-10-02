"""
检索器

从多个学术数据源并行检索论文，支持:
- 并行调用多个数据源适配器
- 单个数据源失败降级（不影响其他源）
- 返回每个源的状态（成功/失败/耗时）
- 可配置超时
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from app.schemas.paper import Paper
from app.schemas.query import DataSource, SubQuery
from app.services.sources.base import BaseSource

logger = logging.getLogger(__name__)

_SOURCE_FAILURE_COUNTS: dict[str, int] = {}
_SOURCE_CIRCUIT_UNTIL: dict[str, float] = {}
_SOURCE_CIRCUIT_SECONDS = 60.0
MAX_SOURCE_REQUESTS = 12


@dataclass
class SourceStatus:
    """单个数据源的检索状态"""

    source: str
    success: bool
    paper_count: int = 0
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    query: Optional[str] = None


@dataclass
class RetrieveResult:
    """检索结果（包含论文列表和各源状态）"""

    papers: List[Paper] = field(default_factory=list)
    source_statuses: List[SourceStatus] = field(default_factory=list)

    @property
    def total_papers(self) -> int:
        return len(self.papers)

    @property
    def successful_sources(self) -> int:
        return sum(1 for s in self.source_statuses if s.success)

    @property
    def failed_sources(self) -> int:
        return sum(1 for s in self.source_statuses if not s.success)


class Retriever:
    """
    检索器

    从多个学术数据源并行检索论文，支持超时和错误处理。
    单个数据源失败不影响其他源。
    """

    def __init__(
        self,
        sources: Optional[Dict[DataSource, BaseSource]] = None,
        timeout: int = 120,
    ):
        """
        初始化检索器

        Args:
            sources: 数据源适配器字典 {DataSource: BaseSource}
            timeout: 单个数据源的超时时间（秒）
        """
        self.sources = sources or {}
        self._timeout = timeout

    async def retrieve(
        self,
        sub_queries: List[SubQuery],
        max_results: int = 50,
        progress_callback: Optional[
            Callable[[SourceStatus], Awaitable[None]]
        ] = None,
    ) -> RetrieveResult:
        """
        执行检索

        并行调用多个数据源，汇总结果。

        Args:
            sub_queries: 子查询列表
            max_results: 每个源的最大结果数

        Returns:
            RetrieveResult（包含论文列表和各源状态）
        """
        logger.info(f"开始检索: {len(sub_queries)} 个子查询")

        # 创建检索任务。结果按完成顺序消费，让界面能够立即看到每个 API
        # 的真实状态，而不必等待最慢的数据源。
        tasks: list[asyncio.Task] = []
        seen: set[tuple[DataSource, str]] = set()
        duplicate_count = 0
        truncated_count = 0
        for sq in sub_queries:
            signature = (sq.source, sq.query.strip())
            if signature in seen:
                duplicate_count += 1
                continue
            seen.add(signature)
            source = self.sources.get(sq.source)
            logger.info(f"[retriever] 查找数据源: {sq.source} -> {'找到' if source else '未找到'}")
            if source:
                if len(tasks) >= MAX_SOURCE_REQUESTS:
                    truncated_count += 1
                    continue
                tasks.append(asyncio.create_task(
                    self._retrieve_from_source(source, sq.query, max_results)
                ))
            else:
                logger.warning(f"未找到数据源适配器: {sq.source}, 可用: {list(self.sources.keys())}")

        if not tasks:
            logger.warning("没有可用的检索任务")
            return RetrieveResult()

        all_papers: List[Paper] = []
        statuses: List[SourceStatus] = []
        try:
            if duplicate_count:
                logger.info("检索子查询去重：跳过 %s 个重复项", duplicate_count)
            if truncated_count:
                status = SourceStatus(
                    source="retrieval_limit", success=False,
                    error=(
                        f"本轮最多执行 {MAX_SOURCE_REQUESTS} 个不同子查询，"
                        f"已跳过 {truncated_count} 个超限子查询；结果仅覆盖已执行部分"
                    ),
                )
                statuses.append(status)
                logger.warning(status.error)
                if progress_callback is not None:
                    try:
                        await progress_callback(status)
                    except Exception:
                        logger.exception("写入检索上限提示失败；继续返回已获取的论文")
            for task in asyncio.as_completed(tasks):
                try:
                    papers, status = await task
                except Exception as exc:  # Source adapters normally isolate failures.
                    logger.exception("检索任务出现未捕获异常")
                    status = SourceStatus(source="unknown", success=False, error=str(exc))
                    papers = []
                all_papers.extend(papers)
                statuses.append(status)
                if progress_callback is not None:
                    try:
                        await progress_callback(status)
                    except Exception:
                        logger.exception("写入检索进度失败；继续返回已获取的论文")
        finally:
            # Parent timeout/cancellation must not leave source requests running.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        ok_count = sum(1 for s in statuses if s.success)
        fail_count = sum(1 for s in statuses if not s.success)
        logger.info(
            f"检索完成: {len(all_papers)} 篇论文, "
            f"{ok_count} 个源成功, {fail_count} 个源失败"
        )

        return RetrieveResult(papers=all_papers, source_statuses=statuses)

    async def _retrieve_from_source(
        self,
        source: BaseSource,
        query: str,
        max_results: int,
    ) -> tuple[List[Paper], SourceStatus]:
        """
        从单个数据源检索

        Args:
            source: 数据源适配器
            query: 查询字符串
            max_results: 最大结果数

        Returns:
            (论文列表, SourceStatus) 元组
        """
        source_name = source.name
        start = time.monotonic()
        blocked_until = _SOURCE_CIRCUIT_UNTIL.get(source_name, 0.0)
        if blocked_until > start:
            remaining = blocked_until - start
            return [], SourceStatus(
                source=source_name,
                success=False,
                elapsed_ms=0.0,
                error=f"数据源断路保护中，{remaining:.0f}s 后重试",
                query=query,
            )

        try:
            papers = await asyncio.wait_for(
                source.search(query, max_results),
                timeout=self._timeout,
            )
            elapsed = (time.monotonic() - start) * 1000
            source_error = getattr(source, "last_error", None)
            if source_error:
                if source_error.startswith(("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504")):
                    failures = _SOURCE_FAILURE_COUNTS.get(source_name, 0) + 1
                    _SOURCE_FAILURE_COUNTS[source_name] = failures
                    if failures >= 2:
                        _SOURCE_CIRCUIT_UNTIL[source_name] = (
                            time.monotonic() + _SOURCE_CIRCUIT_SECONDS
                        )
                logger.error(f"[{source_name}] 检索失败并降级为空结果: {source_error}")
                return papers, SourceStatus(
                    source=source_name,
                    success=False,
                    paper_count=len(papers),
                    elapsed_ms=elapsed,
                    error=source_error,
                    query=query,
                )

            logger.info(f"[{source_name}] 检索成功: {len(papers)} 篇, {elapsed:.0f}ms")
            _SOURCE_FAILURE_COUNTS.pop(source_name, None)
            _SOURCE_CIRCUIT_UNTIL.pop(source_name, None)
            return papers, SourceStatus(
                source=source_name,
                success=True,
                paper_count=len(papers),
                elapsed_ms=elapsed,
                query=query,
            )

        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            logger.error(f"[{source_name}] 检索超时 ({elapsed:.0f}ms)")
            return [], SourceStatus(
                source=source_name,
                success=False,
                elapsed_ms=elapsed,
                error=f"超时 ({self._timeout}s)",
                query=query,
            )

        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            logger.error(f"[{source_name}] 检索失败: {e}")
            return [], SourceStatus(
                source=source_name,
                success=False,
                elapsed_ms=elapsed,
                error=str(e),
                query=query,
            )

    async def health_check_all(self) -> Dict[str, dict]:
        """
        对所有已注册数据源执行健康检查

        Returns:
            {source_name: health_status} 字典
        """
        results = {}
        for source_enum, source in self.sources.items():
            try:
                status = await source.health_check()
                results[source_enum.value] = status
            except Exception as e:
                results[source_enum.value] = {
                    "status": "error",
                    "source": source_enum.value,
                    "message": str(e),
                }
        return results

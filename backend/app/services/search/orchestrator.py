"""
检索编排器

完整检索流程编排:

输入: SearchRequest
  → QueryPlanner.parse(query) → QueryParseResult
  → 并行调用数据源 → 原始论文列表
  → Deduplicator.dedup(papers) → 去重后论文
  → ConstraintVerifier.verify(papers, constraints) → 带约束状态的论文
  → Ranker.rank(papers, query, constraints) → 排序后论文
  → 输出: SearchResponse
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.schemas.paper import Paper
from app.schemas.query import Constraint, DataSource
from app.schemas.search import SearchProgress, SearchRequest, SearchResponse, SearchStatus
from app.services.search.constraint_verifier import ConstraintVerifier
from app.services.search.deduplicator import Deduplicator
from app.services.search.query_planner import QueryPlanner
from app.services.search.ranker import Ranker
from app.services.search.retriever import Retriever, RetrieveResult

logger = logging.getLogger(__name__)


class SearchOrchestrator:
    """
    检索编排器

    协调查询规划、多源检索、去重、约束验证和排序的完整流程。
    """

    def __init__(
        self,
        query_planner: Optional[QueryPlanner] = None,
        retriever: Optional[Retriever] = None,
        deduplicator: Optional[Deduplicator] = None,
        constraint_verifier: Optional[ConstraintVerifier] = None,
        ranker: Optional[Ranker] = None,
    ):
        """
        初始化编排器

        Args:
            query_planner: 查询规划器实例
            retriever: 检索器实例
            deduplicator: 去重器实例
            constraint_verifier: 约束验证器实例
            ranker: 排序器实例
        """
        self.query_planner = query_planner or QueryPlanner()
        self.retriever = retriever or Retriever()
        self.deduplicator = deduplicator or Deduplicator()
        self.constraint_verifier = constraint_verifier or ConstraintVerifier()
        self.ranker = ranker or Ranker()

    async def search(self, request: SearchRequest) -> SearchResponse:
        """
        执行完整检索流程

        Args:
            request: 搜索请求

        Returns:
            搜索响应（包含论文列表、查询计划、约束匹配等）
        """
        run_id = uuid.uuid4()
        start_time = time.monotonic()

        logger.info(f"[{run_id}] 开始检索: {request.query[:100]}...")

        try:
            # ──────────────────────────────────────────────
            # Step 1: 查询规划
            # ──────────────────────────────────────────────
            logger.info(f"[{run_id}] Step 1: 查询规划")

            # 合并用户显式约束和请求中的约束参数
            all_constraints = list(request.constraints)
            if request.date_from:
                all_constraints.append(Constraint(
                    key="year",
                    operator="gte",
                    value=int(request.date_from[:4]),
                    description=f"Published after {request.date_from}",
                ))
            if request.date_to:
                all_constraints.append(Constraint(
                    key="year",
                    operator="lte",
                    value=int(request.date_to[:4]),
                    description=f"Published before {request.date_to}",
                ))
            if request.min_citations is not None:
                all_constraints.append(Constraint(
                    key="min_citations",
                    operator="gte",
                    value=request.min_citations,
                    description=f"Minimum {request.min_citations} citations",
                ))
            if request.open_access_only:
                all_constraints.append(Constraint(
                    key="open_access",
                    operator="eq",
                    value=True,
                    description="Open access only",
                ))

            query_plan = await self.query_planner.plan(
                query=request.query,
                sources=request.sources,
                user_constraints=all_constraints,
                planning_mode=request.planning_mode,
            )

            # 更新排序器的意图
            if query_plan.intent:
                self.ranker.intent = query_plan.intent

            # ──────────────────────────────────────────────
            # Step 2: 多源并行检索
            # ──────────────────────────────────────────────
            logger.info(f"[{run_id}] Step 2: 多源检索 ({len(query_plan.sub_queries)} 个子查询)")

            retrieve_result = await self.retriever.retrieve(
                sub_queries=query_plan.sub_queries,
                max_results=request.max_results * 2,  # 多取一些用于去重后仍有足够结果
            )

            raw_paper_count = retrieve_result.total_papers
            logger.info(f"[{run_id}] 原始结果: {raw_paper_count} 篇")

            # ──────────────────────────────────────────────
            # Step 3: 去重
            # ──────────────────────────────────────────────
            logger.info(f"[{run_id}] Step 3: 去重")

            deduplicated_papers = self.deduplicator.deduplicate(retrieve_result.papers)

            logger.info(
                f"[{run_id}] 去重后: {len(deduplicated_papers)} 篇 "
                f"(移除 {raw_paper_count - len(deduplicated_papers)} 篇重复)"
            )

            # ──────────────────────────────────────────────
            # Step 4: 约束验证
            # ──────────────────────────────────────────────
            all_verification_results = []
            matched_constraints = []

            if all_constraints:
                logger.info(f"[{run_id}] Step 4: 约束验证 ({len(all_constraints)} 个约束)")

                for paper in deduplicated_papers:
                    verifications = await self.constraint_verifier.verify(
                        paper=paper,
                        constraints=all_constraints,
                    )
                    all_verification_results.append(verifications)

                    # 统计匹配状态
                    satisfied = sum(1 for v in verifications if v["verdict"] == "satisfied")
                    total = len(verifications)
                    matched_constraints.append({
                        "paper_id": str(paper.id),
                        "satisfied": satisfied,
                        "total": total,
                        "ratio": satisfied / total if total > 0 else 0,
                        "verifications": verifications,
                    })
            else:
                logger.info(f"[{run_id}] Step 4: 无约束，跳过验证")

            # ──────────────────────────────────────────────
            # Step 5: 多阶段排序
            # ──────────────────────────────────────────────
            logger.info(f"[{run_id}] Step 5: 多阶段排序")

            ranked_papers = await self.ranker.rank(
                papers=deduplicated_papers,
                query=request.query,
                limit=request.max_results,
                constraints=all_constraints,
            )

            # ──────────────────────────────────────────────
            # Step 6: 构建响应
            # ──────────────────────────────────────────────
            elapsed_ms = (time.monotonic() - start_time) * 1000

            # 计算覆盖率和不确定性
            coverage = self._calculate_coverage(
                retrieve_result, len(all_constraints), matched_constraints
            )
            uncertainty = self._calculate_uncertainty(
                retrieve_result, matched_constraints
            )

            # 生成推荐理由
            recommendation_reason = self._generate_recommendation_reason(
                query_plan, len(ranked_papers), retrieve_result
            )

            logger.info(
                f"[{run_id}] 检索完成: {len(ranked_papers)} 篇结果, "
                f"耗时 {elapsed_ms:.0f}ms"
            )

            return SearchResponse(
                run_id=run_id,
                status=SearchStatus.COMPLETED,
                query_plan=query_plan,
                papers=ranked_papers,
                matched_constraints=matched_constraints,
                evidence=[],
                recommendation_reason=recommendation_reason,
                uncertainty=uncertainty,
                coverage=coverage,
                message=(
                    f"Search completed: {len(ranked_papers)} papers found "
                    f"from {retrieve_result.successful_sources} sources "
                    f"in {elapsed_ms:.0f}ms"
                ),
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(f"[{run_id}] 检索失败 ({elapsed_ms:.0f}ms): {e}", exc_info=True)

            return SearchResponse(
                run_id=run_id,
                status=SearchStatus.FAILED,
                papers=[],
                message=f"Search failed: {str(e)}",
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_coverage(
        retrieve_result: RetrieveResult,
        num_constraints: int,
        matched_constraints: List[Dict],
    ) -> float:
        """
        计算检索覆盖率

        综合考虑:
        - 数据源成功率
        - 约束满足率

        Returns:
            覆盖率 (0.0 ~ 1.0)
        """
        # 数据源覆盖率
        total_sources = len(retrieve_result.source_statuses)
        successful = retrieve_result.successful_sources
        source_coverage = successful / total_sources if total_sources > 0 else 0.0

        # 约束覆盖率
        if matched_constraints and num_constraints > 0:
            avg_constraint_ratio = sum(
                mc.get("ratio", 0) for mc in matched_constraints
            ) / len(matched_constraints)
        else:
            avg_constraint_ratio = 1.0  # 无约束时覆盖率视为满

        # 综合覆盖率
        return round(0.6 * source_coverage + 0.4 * avg_constraint_ratio, 4)

    @staticmethod
    def _calculate_uncertainty(
        retrieve_result: RetrieveResult,
        matched_constraints: List[Dict],
    ) -> float:
        """
        计算检索不确定性

        不确定性来源:
        - 数据源失败数
        - 约束验证中 unknown 的比例

        Returns:
            不确定性 (0.0 ~ 1.0)
        """
        # 数据源失败导致的不确定性
        total_sources = len(retrieve_result.source_statuses)
        failed = retrieve_result.failed_sources
        source_uncertainty = failed / total_sources if total_sources > 0 else 0.0

        # 约束验证 unknown 导致的不确定性
        if matched_constraints:
            unknown_count = 0
            total_verifications = 0
            for mc in matched_constraints:
                for v in mc.get("verifications", []):
                    total_verifications += 1
                    if v.get("verdict") == "unknown":
                        unknown_count += 1
            constraint_uncertainty = (
                unknown_count / total_verifications if total_verifications > 0 else 0.0
            )
        else:
            constraint_uncertainty = 0.0

        return round(0.5 * source_uncertainty + 0.5 * constraint_uncertainty, 4)

    @staticmethod
    def _generate_recommendation_reason(
        query_plan,
        result_count: int,
        retrieve_result: RetrieveResult,
    ) -> str:
        """生成推荐理由"""
        intent = query_plan.intent or "open_exploration"
        source_count = retrieve_result.successful_sources

        reasons = {
            "exact_lookup": f"Found {result_count} papers matching your specific query from {source_count} sources",
            "open_exploration": f"Exploring {result_count} relevant papers from {source_count} sources based on keyword relevance and citation impact",
            "literature_review": f"Curated {result_count} papers for literature review, prioritizing comprehensive coverage and high-impact works",
            "similar_recommendation": f"Found {result_count} papers similar to your reference, ranked by topical relevance",
            "methodology_survey": f"Identified {result_count} papers with focus on methodology and experimental evaluation",
        }

        return reasons.get(intent, f"Found {result_count} relevant papers from {source_count} sources")

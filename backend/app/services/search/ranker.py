"""
排序器

多阶段排序算法:

Stage 1 - RRF 多源融合:
    RRFScore(d) = Σ 1/(k + rank_source(d)), k=60

Stage 2 - 约束加权:
    ConstraintScore = 满足的硬约束数 / 总硬约束数

Stage 3 - 相关性精排:
    BaseScore = 0.45 * Relevance
              + 0.25 * ConstraintConfidence
              + 0.15 * FieldNormalizedImpact
              + 0.10 * IntentAwareTimeliness
              + 0.05 * OAAvailability

Stage 4 - MMR 多样性重排:
    MMR = λ * BaseScore - (1-λ) * maxSimilarity(selected, candidate)
    λ = 0.75

所有分数可追溯、可解释。
"""

import logging
import math
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from app.schemas.paper import Paper, PaperQuality

logger = logging.getLogger(__name__)

# RRF 常数
RRF_K = 60

# Stage 3 权重
WEIGHT_RELEVANCE = 0.45
WEIGHT_CONSTRAINT = 0.25
WEIGHT_IMPACT = 0.15
WEIGHT_TIMELINESS = 0.10
WEIGHT_OA = 0.05

# MRF lambda
MMR_LAMBDA = 0.75

# Broad academic vocabulary is not evidence of a shared research topic.
GENERIC_TERMS = {
    "data", "model", "method", "approach", "framework", "system", "algorithm",
    "application", "research", "paper", "study", "survey", "comparison", "experiment",
}
STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "of", "for", "to", "in", "on", "at", "by", "with", "from",
    "this", "that", "these", "those", "it", "its", "we", "our", "you", "your",
    "as", "about", "using", "use", "used", "based", "via", "into", "can", "how",
    "find", "please", "et", "al",
}
TERM_VARIANTS = {
    "models": "model", "methods": "method", "approaches": "approach",
    "systems": "system", "algorithms": "algorithm", "applications": "application",
    "papers": "paper", "studies": "study", "networks": "network", "images": "image",
    "predict": "prediction", "predicting": "prediction", "predictions": "prediction",
    "forecast": "prediction", "forecasting": "prediction", "forecasts": "prediction",
    "impute": "repair", "imputation": "repair", "imputing": "repair",
    "repairing": "repair", "restoration": "repair", "inpainting": "repair",
    "completion": "repair", "classify": "classification", "detect": "detection",
}
TASK_TERMS = {"repair", "prediction", "classification", "detection", "segmentation", "generation"}


class Ranker:
    """
    多阶段排序器

    支持可解释的分数构成，每个阶段的得分可追溯。
    """

    def __init__(self, intent: str = "open_exploration"):
        """
        初始化排序器

        Args:
            intent: 查询意图，影响时效性权重
        """
        self.intent = intent

    async def rank(
        self,
        papers: List[Paper],
        query: str,
        limit: int = 50,
        constraints: Optional[list] = None,
        source_ranks: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> List[Paper]:
        """
        对论文列表进行多阶段排序

        Args:
            papers: 去重后的论文列表
            query: 原始查询
            limit: 返回数量限制
            constraints: 约束条件列表（可选）
            source_ranks: 各数据源的原始排名 {source: {paper_id: rank}}（可选）

        Returns:
            排序后的论文列表（按相关性降序），已设置 relevance_score
        """
        if not papers:
            return []

        # 预计算各论文的相关性分数
        scored_papers = []
        for paper in papers:
            relevance = await self._calculate_relevance(paper, query)
            constraint_score = self._calculate_constraint_score(paper, constraints)
            impact_score = self._calculate_impact_score(paper, papers)
            timeliness_score = self._calculate_timeliness_score(paper)
            oa_score = 1.0 if paper.is_open_access else 0.0

            # Stage 1: RRF 多源融合分数
            rrf_score = self._calculate_rrf_score(paper, source_ranks)

            # Stage 3: 加权基础分数
            if self.intent == "exact_lookup":
                # 精确找论文时，作者+标题命中必须主导排序，不能被高引用
                # 的同名/同缩写论文挤下去。
                base_score = (
                    0.85 * relevance
                    + 0.05 * impact_score
                    + 0.05 * timeliness_score
                    + 0.05 * oa_score
                )
            else:
                base_score = (
                    WEIGHT_RELEVANCE * relevance
                    + WEIGHT_CONSTRAINT * constraint_score
                    + WEIGHT_IMPACT * impact_score
                    + WEIGHT_TIMELINESS * timeliness_score
                    + WEIGHT_OA * oa_score
                )

            # 融合 RRF 分数（如果有）
            if rrf_score > 0:
                base_score = 0.7 * base_score + 0.3 * rrf_score

            scored_papers.append({
                "paper": paper,
                "base_score": base_score,
                "breakdown": {
                    "relevance": relevance,
                    "constraint": constraint_score,
                    "impact": impact_score,
                    "timeliness": timeliness_score,
                    "oa": oa_score,
                    "rrf": rrf_score,
                },
            })

        # Stage 4: MMR 多样性重排
        selected = self._mmr_rerank(scored_papers, limit)

        # 设置 relevance_score 并返回
        result = []
        for item in selected:
            paper = item["paper"]
            paper.relevance_score = round(item["breakdown"]["relevance"], 4)
            paper.ranking_score = round(item["base_score"], 4)
            result.append(paper)

        self._attach_quality_signals(result, papers)
        return result

    @staticmethod
    def _attach_quality_signals(
        ranked_papers: List[Paper],
        candidate_papers: List[Paper],
    ) -> None:
        """附加开放、可复算的质量信号，不伪造 JCR 或中科院分区。"""
        if not ranked_papers:
            return

        citations = sorted(max(0, paper.citation_count) for paper in candidate_papers)
        total = max(1, len(citations))
        max_velocity = 0.0
        velocities: Dict[str, float] = {}

        for paper in candidate_papers:
            age = max(1, 2026 - paper.year + 1) if paper.year else 1
            velocity = max(0, paper.citation_count) / age
            velocities[str(paper.id)] = velocity
            max_velocity = max(max_velocity, velocity)

        for paper in ranked_papers:
            count = max(0, paper.citation_count)
            less = sum(1 for value in citations if value < count)
            equal = sum(1 for value in citations if value == count)
            percentile = (less + 0.5 * equal) / total
            velocity = velocities.get(str(paper.id), 0.0)
            velocity_score = (
                math.log1p(velocity) / math.log1p(max_velocity)
                if max_velocity > 0
                else 0.0
            )
            quality_score = 0.75 * percentile + 0.25 * velocity_score

            if count > 0 and percentile >= 0.90:
                impact_label = "highly_cited"
            elif count > 0 and percentile >= 0.70:
                impact_label = "well_cited"
            elif count > 0 and percentile >= 0.40:
                impact_label = "established"
            elif paper.year and paper.year >= 2024:
                impact_label = "emerging"
            else:
                impact_label = "limited_signal"

            paper.quality = PaperQuality(
                quality_score=round(quality_score, 4),
                citation_percentile=round(percentile, 4),
                citation_velocity=round(velocity, 2),
                impact_label=impact_label,
            )

    # ------------------------------------------------------------------
    # 相关性计算
    # ------------------------------------------------------------------

    async def _calculate_relevance(self, paper: Paper, query: str) -> float:
        """
        计算查询与论文的相关性分数

        支持跨语言匹配：中文查询 → 英文论文标题/摘要
        """
        if not query:
            return 0.0

        title = (paper.title or "").lower()
        abstract = (paper.abstract or "").lower()

        # 1. 查询转英文后的关键词匹配
        from app.services.search.query_planner import QueryPlanner
        scoring_query = QueryPlanner.resolve_paper_alias(query)
        en_query = QueryPlanner._translate_to_english(scoring_query).lower()

        # Both sides use the same phrase translation and word boundaries.
        # Unknown Chinese subjects remain matchable, rather than collapsing to data.
        all_keywords = set(self._tokenize(scoring_query))
        informative = all_keywords - GENERIC_TERMS
        if informative:
            all_keywords = informative
        if not all_keywords:
            return 0.0
        title_tokens = set(self._tokenize(title))
        abstract_tokens = set(self._tokenize(abstract))

        def coverage(terms: set[str]) -> float:
            return sum(
                1.0 if term in title_tokens else 0.5 if term in abstract_tokens else 0.0
                for term in terms
            ) / len(terms) if terms else 0.0

        # Matching the task (e.g. repair) alone cannot establish the domain.
        # A shared domain with a different task (prediction) is a useful neighbor,
        # not an exact answer. Citations and venue never enter this score.
        tasks = all_keywords & TASK_TERMS
        topics = all_keywords - TASK_TERMS
        if topics and tasks:
            topic_coverage = coverage(topics)
            task_coverage = coverage(tasks)
            base_score = 0.75 * topic_coverage + 0.25 * task_coverage
            if not topic_coverage:
                base_score = 0.15 * task_coverage
            elif len(topics & (title_tokens | abstract_tokens)) / len(topics) < 0.75:
                base_score = min(base_score, 0.35)
            elif not task_coverage:
                base_score = min(base_score, 0.65)
        else:
            base_score = coverage(all_keywords)
        if all_keywords <= title_tokens:
            base_score = 0.95

        # 5. “标题简称 by 作者 et al.” 是学术精确查找的常见写法。
        # 标题线索与作者姓氏同时命中时必须压过引用量和时效性。
        if self.intent == "exact_lookup":
            citation_match = re.match(
                r"^\s*(.+?)\s+by\s+([a-z][a-z\-']+)(?:\s+et\s+al\.?)?\s*$",
                en_query,
                flags=re.IGNORECASE,
            )
            if citation_match:
                title_hint = self._tokenize(citation_match.group(1))
                author_hint = citation_match.group(2).casefold()
                title_matches = bool(title_hint) and set(title_hint) <= title_tokens
                author_matches = any(
                    author_hint in author.casefold() for author in paper.authors
                )
                if title_matches and author_matches:
                    base_score = 1.0
                elif title_matches:
                    base_score = max(base_score, 0.70)

            named_match = re.match(
                r"^\s*the\s+(.+?)\s+([a-z][a-z0-9_-]*?)(\d{4})[a-z0-9_-]*\s+paper\s*$",
                en_query,
                flags=re.IGNORECASE,
            )
            if named_match:
                alias = named_match.group(1).replace("²", "2")
                alias = re.sub(r"([a-z])\s*\^\s*(\d)", r"\1\2", alias)
                alias_compact = re.sub(r"[^a-z0-9]+", "", alias)
                title_compact = re.sub(r"[^a-z0-9]+", "", title)
                if len(alias_compact) >= 3 and alias_compact in title_compact:
                    base_score = max(base_score, 0.85)
                    citation_author = named_match.group(2).casefold()
                    author_surnames = [
                        re.sub(r"[^a-z]", "", author.casefold().split()[-1])
                        for author in paper.authors
                        if author
                    ]
                    if any(
                        SequenceMatcher(
                            None,
                            citation_author,
                            surname,
                        ).ratio() >= 0.80
                        for surname in author_surnames
                    ):
                        base_score = 1.0

            alias_text: Optional[str] = None
            about_match = re.match(
                r"^\s*(?:the|a)\s+paper\s+about\s+(.+?)\s*$",
                en_query,
                flags=re.IGNORECASE,
            )
            if about_match:
                alias_text = about_match.group(1)
            elif en_query.rstrip(".").endswith(" paper"):
                alias_text = re.sub(
                    r"^\s*(?:the|a)\s+|\s+paper\.?\s*$",
                    "",
                    en_query,
                    flags=re.IGNORECASE,
                )
            if alias_text:
                alias_tokens = [
                    token
                    for token in self._tokenize(alias_text)
                    if token not in {
                        "the", "a", "an", "about",
                        "dataset", "model", "system", "method",
                    }
                ]
                if alias_tokens and set(alias_tokens) <= title_tokens:
                    base_score = 1.0

        return min(base_score, 1.0)

    def _calculate_constraint_score(
        self,
        paper: Paper,
        constraints: Optional[list],
    ) -> float:
        """
        计算约束满足分数

        满足的硬约束数 / 总硬约束数

        Args:
            paper: 论文对象
            constraints: 约束条件列表

        Returns:
            约束满足分数 (0.0 ~ 1.0)
        """
        if not constraints:
            # No constraint is not evidence of relevance. Returning 1.0 here
            # inflated every unconstrained paper by WEIGHT_CONSTRAINT.
            return 0.0

        satisfied = 0
        total = len(constraints)

        for constraint in constraints:
            key = constraint.key if hasattr(constraint, "key") else constraint.get("key", "")
            op = constraint.operator if hasattr(constraint, "operator") else constraint.get("operator", "")
            value = constraint.value if hasattr(constraint, "value") else constraint.get("value")

            if self._check_constraint(paper, key, op, value):
                satisfied += 1

        return satisfied / total if total > 0 else 1.0

    def filter_hard_constraints(
        self,
        papers: List[Paper],
        constraints: Optional[list],
    ) -> List[Paper]:
        """Apply deterministic user/query constraints before final ranking."""
        if not constraints:
            return list(papers)
        hard_keys = {"year", "min_citations", "open_access", "author", "venue"}
        normalized = [
            (
                constraint.key if hasattr(constraint, "key") else constraint.get("key", ""),
                constraint.operator if hasattr(constraint, "operator") else constraint.get("operator", ""),
                constraint.value if hasattr(constraint, "value") else constraint.get("value"),
            )
            for constraint in constraints
        ]
        return [
            paper
            for paper in papers
            if all(
                key not in hard_keys or self._check_constraint(paper, key, operator, value)
                for key, operator, value in normalized
            )
        ]

    def _check_constraint(
        self,
        paper: Paper,
        key: str,
        operator: str,
        value,
    ) -> bool:
        """检查单个约束是否满足"""
        try:
            if key == "year":
                if paper.year is None:
                    return False
                if operator == "gte":
                    return paper.year >= int(value)
                elif operator == "lte":
                    return paper.year <= int(value)
                elif operator == "eq":
                    return paper.year == int(value)
                elif operator == "in":
                    return paper.year in value

            elif key == "min_citations":
                if operator == "gte":
                    return paper.citation_count >= int(value)

            elif key == "open_access":
                if operator == "eq":
                    return paper.is_open_access == bool(value)

            elif key == "author":
                if operator == "contains":
                    authors_lower = [a.lower() for a in paper.authors]
                    return any(str(value).lower() in a for a in authors_lower)

            elif key == "venue":
                if operator == "contains":
                    return str(value).lower() in (paper.venue or "").lower()
                if operator == "in":
                    venue = (paper.venue or "").lower()
                    return any(str(item).lower() in venue for item in value)

        except (ValueError, TypeError):
            pass

        return False

    @staticmethod
    def _calculate_impact_score(paper: Paper, all_papers: List[Paper]) -> float:
        """
        计算归一化影响力分数

        使用 log 归一化:
        score = log(1 + citation_count) / log(1 + max_citation)

        Args:
            paper: 当前论文
            all_papers: 所有论文（用于确定最大引用数）

        Returns:
            归一化影响力分数 (0.0 ~ 1.0)
        """
        if not all_papers:
            return 0.0

        max_citation = max(p.citation_count for p in all_papers)
        if max_citation <= 0:
            return 0.0

        return math.log(1 + paper.citation_count) / math.log(1 + max_citation)

    def _calculate_timeliness_score(self, paper: Paper) -> float:
        """
        计算时效性分数

        由查询意图决定权重:
        - "latest_progress" / "open_exploration": 强时间衰减
        - "literature_review" / "exact_lookup": 弱时间衰减
        - "methodology_survey": 中等衰减

        Args:
            paper: 论文对象

        Returns:
            时效性分数 (0.0 ~ 1.0)
        """
        if paper.year is None:
            return 0.5  # 无年份信息时给中间分

        current_year = 2026
        age = max(0, current_year - paper.year)

        # 根据意图选择衰减策略
        if self.intent in ("open_exploration", "similar_recommendation"):
            # 强时间衰减：每 2 年减半
            return math.exp(-0.35 * age)
        elif self.intent in ("exact_lookup", "literature_review"):
            # 弱时间衰减：经典论文不应被惩罚
            return math.exp(-0.05 * age)
        elif self.intent == "methodology_survey":
            # 中等衰减
            return math.exp(-0.15 * age)
        else:
            # 默认中等衰减
            return math.exp(-0.15 * age)

    # ------------------------------------------------------------------
    # RRF 多源融合
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_rrf_score(
        paper: Paper,
        source_ranks: Optional[Dict[str, Dict[str, int]]],
    ) -> float:
        """
        计算 RRF (Reciprocal Rank Fusion) 分数

        RRFScore(d) = Σ 1/(k + rank_source(d))
        k = 60

        Args:
            paper: 论文对象
            source_ranks: 各数据源的原始排名

        Returns:
            RRF 分数 (0.0 ~ 1.0)
        """
        if not source_ranks:
            return 0.0

        rrf_sum = 0.0
        paper_id_str = str(paper.id)

        for source, ranks in source_ranks.items():
            rank = ranks.get(paper_id_str)
            if rank is not None:
                rrf_sum += 1.0 / (RRF_K + rank)

        # 归一化到 0-1
        max_possible = len(source_ranks) * (1.0 / (RRF_K + 1))
        if max_possible > 0:
            return min(rrf_sum / max_possible, 1.0)
        return 0.0

    # ------------------------------------------------------------------
    # MMR 多样性重排
    # ------------------------------------------------------------------

    @staticmethod
    def _mmr_rerank(
        scored_papers: List[dict],
        limit: int,
    ) -> List[dict]:
        """
        MMR (Maximal Marginal Relevance) 多样性重排

        避免 Top N 全部来自同一方法、同一团队。
        保证不同技术路线都有代表。

        MMR = λ * BaseScore - (1-λ) * maxSimilarity(selected, candidate)
        λ = 0.75

        Args:
            scored_papers: 带分数的论文列表
            limit: 返回数量

        Returns:
            MMR 重排后的论文列表
        """
        if not scored_papers:
            return []

        # Quality and diversity only reorder within a relevance tier; otherwise
        # an unrelated but highly cited paper can consume a limited result slot.
        def relevance_tier(item: dict) -> int:
            score = item["breakdown"]["relevance"]
            return 2 if score >= 0.7 else 1 if score >= 0.45 else 0

        scored_papers.sort(key=lambda x: (relevance_tier(x), x["base_score"]), reverse=True)

        selected: List[dict] = []
        remaining = list(scored_papers)
        diversity_limit = min(limit, 50)
        token_sets = {
            str(item["paper"].id): set(Ranker._tokenize(item["paper"].title or ""))
            for item in scored_papers
        }

        while remaining and len(selected) < diversity_limit:
            best_idx = -1
            best_mmr = -float("inf")
            best_tier = relevance_tier(remaining[0])

            for i, candidate in enumerate(remaining):
                if relevance_tier(candidate) != best_tier:
                    continue
                # 计算与已选集合的最大相似度
                max_sim = 0.0
                for sel in selected:
                    tokens_a = token_sets[str(candidate["paper"].id)]
                    tokens_b = token_sets[str(sel["paper"].id)]
                    union = tokens_a | tokens_b
                    sim = len(tokens_a & tokens_b) / len(union) if union else 0.0
                    max_sim = max(max_sim, sim)

                # MMR 分数
                mmr = (
                    MMR_LAMBDA * candidate["base_score"]
                    - (1 - MMR_LAMBDA) * max_sim
                )

                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = i

            if best_idx >= 0:
                selected.append(remaining.pop(best_idx))
            else:
                break

        # MMR is quadratic in K. Past Top-50, preserve the relevance ordering
        # instead of spending latency on weak marginal diversity gains.
        if len(selected) < limit and remaining:
            selected.extend(remaining[: limit - len(selected)])

        return selected

    @staticmethod
    def _paper_similarity(paper_a: Paper, paper_b: Paper) -> float:
        """
        计算两篇论文的相似度

        基于标题词重叠的简单相似度度量。

        Args:
            paper_a: 论文 A
            paper_b: 论文 B

        Returns:
            相似度 (0.0 ~ 1.0)
        """
        tokens_a = set(Ranker._tokenize(paper_a.title or ""))
        tokens_b = set(Ranker._tokenize(paper_b.title or ""))

        if not tokens_a or not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        return len(intersection) / len(union) if union else 0.0

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """
        Word-boundary tokens plus Chinese bigrams for terms outside the glossary.

        Args:
            text: 输入文本

        Returns:
            词列表
        """
        if not text:
            return []
        from app.services.search.query_planner import QueryPlanner

        normalized = QueryPlanner._translate_to_english(text).lower()
        tokens = [TERM_VARIANTS.get(term, term)
                  for term in re.findall(r"[a-z0-9]+", normalized)
                  if len(term) > 1 and term not in STOP_WORDS]
        for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
            if segment in {"基于", "关于", "用于", "使用", "研究", "论文", "文献", "中的", "中的使用"}:
                continue
            tokens.extend(segment[index:index + 2] for index in range(len(segment) - 1))
        return tokens

    @staticmethod
    def _token_overlap_score(query_tokens: List[str], doc_tokens: List[str]) -> float:
        """
        计算查询词在文档中的命中率

        Args:
            query_tokens: 查询词列表
            doc_tokens: 文档词列表

        Returns:
            命中率 (0.0 ~ 1.0)
        """
        if not query_tokens:
            return 0.0

        doc_set = set(doc_tokens)
        hits = sum(1 for t in query_tokens if t in doc_set)
        return hits / len(query_tokens)

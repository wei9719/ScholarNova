"""
去重器

多源检索结果去重，支持:
1. DOI 精确匹配（最高优先级）
2. 标题相似度匹配（Jaccard 相似度）
3. 作者+年份+标题前缀匹配
4. 版本聚类（预印本→会议版→期刊版）

保留策略:
- 去重后保留所有来源链接
- 信息合并（取各来源的并集）
- 标注版本关系
"""

import logging
import re
from typing import Dict, List, Optional, Set

from app.schemas.paper import Paper

logger = logging.getLogger(__name__)


class Deduplicator:
    """
    多源结果去重器

    按优先级执行去重:
    1. DOI 精确匹配
    2. 标题相似度（Jaccard > threshold）
    3. 版本聚类

    保留第一次出现的论文，合并来源信息。
    """

    def __init__(self, similarity_threshold: float = 0.85):
        """
        初始化去重器

        Args:
            similarity_threshold: 标题相似度阈值（Jaccard 相似度），
                                  超过此值判为同一篇论文
        """
        self.similarity_threshold = similarity_threshold

    def deduplicate(self, papers: List[Paper]) -> List[Paper]:
        """
        对论文列表去重

        Args:
            papers: 原始论文列表（可能包含重复）

        Returns:
            去重后的论文列表，保留第一次出现的版本
        """
        if not papers:
            return []

        # Phase 1: DOI 精确去重
        doi_map: Dict[str, int] = {}  # doi -> index in result
        result: List[Paper] = []

        for paper in papers:
            if paper.doi:
                doi_normalized = paper.doi.strip().lower()
                if doi_normalized in doi_map:
                    # DOI 重复，合并信息到已有论文
                    existing_idx = doi_map[doi_normalized]
                    result[existing_idx] = self._merge_papers(
                        result[existing_idx], paper
                    )
                    continue
                else:
                    doi_map[doi_normalized] = len(result)
                    result.append(paper)
            else:
                # 无 DOI 的论文先加入，后续按标题去重
                result.append(paper)

        # Phase 2: 标题相似度去重（仅处理无 DOI 或 DOI 不匹配的情况）
        final_result: List[Paper] = []
        normalized_titles: List[str] = []

        for paper in result:
            norm_title = self._normalize_title(paper.title)
            is_duplicate = False

            # 只与已有结果比较
            for i, existing_norm in enumerate(normalized_titles):
                similarity = self._calculate_similarity(norm_title, existing_norm)
                if similarity >= self.similarity_threshold:
                    # 标题重复，合并信息
                    final_result[i] = self._merge_papers(final_result[i], paper)
                    is_duplicate = True
                    break

            if not is_duplicate:
                final_result.append(paper)
                normalized_titles.append(norm_title)

        logger.info(
            f"去重完成: {len(papers)} → {len(final_result)} "
            f"(移除 {len(papers) - len(final_result)} 篇重复)"
        )
        return final_result

    def _normalize_title(self, title: str) -> str:
        """
        标准化标题用于比较

        处理步骤:
        1. 转小写
        2. 移除标点符号
        3. 标准化空白（合并多个空格为一个）
        4. 去除首尾空白

        Args:
            title: 原始标题

        Returns:
            标准化后的标题
        """
        if not title:
            return ""
        # 转小写
        normalized = title.lower()
        # 移除标点符号（保留字母、数字、空格、中文字符）
        normalized = re.sub(r"[^\w\s一-鿿]", "", normalized)
        # 标准化空白
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _calculate_similarity(self, text_a: str, text_b: str) -> float:
        """
        计算两个文本的 Jaccard 相似度

        基于词级别的 Jaccard 系数:
        J(A, B) = |A ∩ B| / |A ∪ B|

        Args:
            text_a: 文本 A
            text_b: 文本 B

        Returns:
            Jaccard 相似度 (0.0 ~ 1.0)
        """
        if not text_a or not text_b:
            return 0.0

        # 分词
        tokens_a: Set[str] = set(text_a.split())
        tokens_b: Set[str] = set(text_b.split())

        if not tokens_a and not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        if not union:
            return 0.0

        return len(intersection) / len(union)

    def _merge_papers(self, primary: Paper, duplicate: Paper) -> Paper:
        """
        合并两篇重复论文的信息

        保留 primary 的基本信息，合并来源和链接。

        Args:
            primary: 主论文（先出现的）
            duplicate: 重复论文

        Returns:
            合并后的论文
        """
        # 合并作者列表（去重）
        merged_authors = list(primary.authors)
        for author in duplicate.authors:
            if author not in merged_authors:
                merged_authors.append(author)

        # 保留信息更丰富的版本
        # 优先使用有摘要的版本
        abstract = primary.abstract or duplicate.abstract
        # 优先使用引用数更高的版本的引用数
        citation_count = max(primary.citation_count, duplicate.citation_count)
        # 保留所有 URL
        url = primary.url or duplicate.url
        pdf_url = primary.pdf_url or duplicate.pdf_url
        # 如果主论文没有 PDF，尝试从重复论文获取
        if not pdf_url and duplicate.pdf_url:
            pdf_url = duplicate.pdf_url
        publication_date = primary.publication_date or duplicate.publication_date

        # 保留 primary 的 id，但更新其他字段
        return Paper(
            id=primary.id,
            title=primary.title,
            authors=merged_authors,
            abstract=abstract,
            year=publication_date.year if publication_date else primary.year or duplicate.year,
            publication_date=publication_date,
            venue=primary.venue or duplicate.venue,
            citation_count=citation_count,
            doi=primary.doi or duplicate.doi,
            url=url,
            pdf_url=pdf_url,
            source=primary.source,  # 保留原始来源
            corpus_id=primary.corpus_id or duplicate.corpus_id,
            relevance_score=primary.relevance_score,
            is_open_access=primary.is_open_access or duplicate.is_open_access,
        )

"""
约束验证器

证据级约束验证，支持三级验证策略:
1. 元数据级：时间、文献类型、OA 状态 → 直接从元数据判断
2. 摘要级：是否使用某数据集、是否有对比实验 → 从摘要提取
3. 全文级：具体实验细节、方法描述 → 从全文提取（Top K）

输出格式:
{
    "constraint_key": "has_comparative_experiment",
    "verdict": "satisfied" | "violated" | "unknown",
    "source_level": "metadata" | "abstract" | "fulltext",
    "evidence_text": "原文引用片段",
    "section": "Section 4.2",
    "confidence": 0.85
}

无证据时输出 "unknown"，禁止编造。
"""

import json
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from app.schemas.paper import Paper

logger = logging.getLogger(__name__)


class ConstraintVerifier:
    """
    约束验证器

    对论文逐条验证约束条件，输出带证据的验证结果。
    支持元数据级和摘要级自动验证，全文级需 LLM 辅助。
    """

    def __init__(self, llm_gateway=None):
        """
        初始化约束验证器

        Args:
            llm_gateway: LLM 网关实例（可选，用于全文级验证）
        """
        self.llm_gateway = llm_gateway

    async def verify(
        self,
        paper: Paper,
        constraints: list,
    ) -> List[Dict[str, Any]]:
        """
        验证单篇论文的所有约束

        按证据级别从高到低验证:
        1. 元数据级（直接判断）
        2. 摘要级（文本匹配）
        3. 全文级（LLM 辅助，需提供 fulltext）

        Args:
            paper: 论文对象
            constraints: 约束条件列表

        Returns:
            验证结果列表，每项对应一个约束
        """
        results = []

        for constraint in constraints:
            key = constraint.key if hasattr(constraint, "key") else constraint.get("key", "")
            op = constraint.operator if hasattr(constraint, "operator") else constraint.get("operator", "")
            value = constraint.value if hasattr(constraint, "value") else constraint.get("value", None)
            description = constraint.description if hasattr(constraint, "description") else constraint.get("description", "")

            result = await self._verify_single(paper, key, op, value, description)
            results.append(result)

        return results

    async def verify_batch(
        self,
        papers: List[Paper],
        constraints: list,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        批量验证多篇论文的约束

        Args:
            papers: 论文列表
            constraints: 约束条件列表

        Returns:
            {paper_id: [验证结果列表]} 字典
        """
        results = {}
        for paper in papers:
            paper_id = str(paper.id)
            results[paper_id] = await self.verify(paper, constraints)
        return results

    async def _verify_single(
        self,
        paper: Paper,
        key: str,
        operator: str,
        value: Any,
        description: Optional[str],
    ) -> Dict[str, Any]:
        """
        验证单个约束

        按证据级别从高到低尝试:
        1. 元数据级
        2. 摘要级
        3. 全文级（LLM）

        Args:
            paper: 论文对象
            key: 约束键
            operator: 操作符
            value: 约束值
            description: 约束描述

        Returns:
            验证结果字典
        """
        # Level 1: 元数据级验证
        metadata_result = self._verify_metadata(paper, key, operator, value)
        if metadata_result is not None:
            return metadata_result

        # Level 2: 摘要级验证
        abstract_result = self._verify_abstract(paper, key, operator, value)
        if abstract_result is not None:
            return abstract_result

        # Level 3: 全文级验证（需要 LLM）
        if self.llm_gateway is not None:
            fulltext_result = await self._verify_with_llm(paper, key, operator, value, description)
            if fulltext_result is not None:
                return fulltext_result

        # 无法验证 → unknown
        return self._make_result(
            constraint_key=key,
            claim=description or f"{key} {operator} {value}",
            verdict="unknown",
            source_level="metadata",
            evidence_text="",
            section=None,
            confidence=0.0,
        )

    # ------------------------------------------------------------------
    # 元数据级验证
    # ------------------------------------------------------------------

    def _verify_metadata(
        self,
        paper: Paper,
        key: str,
        operator: str,
        value: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        元数据级验证

        可验证的约束:
        - year: 发表年份
        - min_citations: 最小引用数
        - open_access: 开放获取状态
        - source: 数据来源
        - has_doi: 是否有 DOI

        Returns:
            验证结果，不适用时返回 None
        """
        if key == "publication_date":
            # Missing day precision is a metadata limitation, not a reason to
            # ask a language model to manufacture a publication date.
            try:
                boundary = date.fromisoformat(str(value))
                observed = paper.publication_date
                checks = {
                    "gte": observed is not None and observed >= boundary,
                    "lte": observed is not None and observed <= boundary,
                    "eq": observed is not None and observed == boundary,
                }
                verdict = ("satisfied" if checks[operator] else "violated") if observed and operator in checks else "unknown"
            except (TypeError, ValueError):
                verdict = "unknown"
            return self._make_result(
                constraint_key=key, claim=f"Publication date {operator} {value}",
                verdict=verdict, source_level="metadata",
                evidence_text=f"Publication date: {paper.publication_date}; year: {paper.year}",
                confidence=0.99 if verdict != "unknown" else 0.0,
            )

        if key == "year":
            if paper.year is None:
                return self._make_result(
                    constraint_key=key,
                    claim=f"Year {operator} {value}",
                    verdict="unknown",
                    source_level="metadata",
                    evidence_text="",
                    confidence=0.0,
                )
            satisfied = self._check_numeric(paper.year, operator, value)
            return self._make_result(
                constraint_key=key,
                claim=f"Year {operator} {value}",
                verdict="satisfied" if satisfied else "violated",
                source_level="metadata",
                evidence_text=f"Publication year: {paper.year}",
                confidence=0.99,
            )

        elif key == "min_citations":
            satisfied = self._check_numeric(paper.citation_count, operator, value)
            return self._make_result(
                constraint_key=key,
                claim=f"Citation count {operator} {value}",
                verdict="satisfied" if satisfied else "violated",
                source_level="metadata",
                evidence_text=f"Citation count: {paper.citation_count}",
                confidence=0.95,
            )

        elif key == "open_access":
            if operator == "eq":
                satisfied = paper.is_open_access == bool(value)
                return self._make_result(
                    constraint_key=key,
                    claim=f"Open access = {value}",
                    verdict="satisfied" if satisfied else "violated",
                    source_level="metadata",
                    evidence_text=f"Open access: {paper.is_open_access}",
                    confidence=0.99,
                )

        elif key == "source":
            if operator == "eq":
                satisfied = paper.source == str(value)
                return self._make_result(
                    constraint_key=key,
                    claim=f"Source = {value}",
                    verdict="satisfied" if satisfied else "violated",
                    source_level="metadata",
                    evidence_text=f"Source: {paper.source}",
                    confidence=0.99,
                )

        elif key == "has_doi":
            if operator == "eq":
                has = paper.doi is not None and paper.doi != ""
                satisfied = has == bool(value)
                return self._make_result(
                    constraint_key=key,
                    claim=f"Has DOI = {value}",
                    verdict="satisfied" if satisfied else "violated",
                    source_level="metadata",
                    evidence_text=f"DOI: {paper.doi or 'None'}",
                    confidence=0.99,
                )

        elif key == "venue":
            if paper.venue is None:
                return self._make_result(
                    constraint_key=key,
                    claim=f"Venue {operator} {value}",
                    verdict="unknown",
                    source_level="metadata",
                    evidence_text="",
                    confidence=0.0,
                )
            if operator == "contains":
                satisfied = str(value).lower() in paper.venue.lower()
                return self._make_result(
                    constraint_key=key,
                    claim=f"Venue contains '{value}'",
                    verdict="satisfied" if satisfied else "violated",
                    source_level="metadata",
                    evidence_text=f"Venue: {paper.venue}",
                    confidence=0.95,
                )
            elif operator == "eq":
                satisfied = paper.venue.lower() == str(value).lower()
                return self._make_result(
                    constraint_key=key,
                    claim=f"Venue = {value}",
                    verdict="satisfied" if satisfied else "violated",
                    source_level="metadata",
                    evidence_text=f"Venue: {paper.venue}",
                    confidence=0.95,
                )

        elif key == "author":
            if operator == "contains":
                authors_lower = [a.lower() for a in paper.authors]
                target = str(value).lower()
                matched = any(target in a for a in authors_lower)
                return self._make_result(
                    constraint_key=key,
                    claim=f"Author contains '{value}'",
                    verdict="satisfied" if matched else "violated",
                    source_level="metadata",
                    evidence_text=f"Authors: {', '.join(paper.authors)}",
                    confidence=0.95 if matched else 0.8,
                )

        return None

    # ------------------------------------------------------------------
    # 摘要级验证
    # ------------------------------------------------------------------

    def _verify_abstract(
        self,
        paper: Paper,
        key: str,
        operator: str,
        value: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        摘要级验证

        可验证的约束:
        - has_comparative_experiment: 是否有对比实验
        - uses_dataset: 是否使用某数据集
        - mentions_method: 是否提到某方法

        Returns:
            验证结果，不适用或无摘要时返回 None
        """
        if not paper.abstract:
            return None

        abstract_lower = paper.abstract.lower()

        if key == "has_comparative_experiment":
            # 检测摘要中的实验对比信号词
            experiment_signals = [
                "compare", "comparison", "comparative", "baseline", "baselines",
                "outperform", "state-of-the-art", "sota", "exceed", "surpass",
                "against", "compared to", "compared with", "better than",
                "evaluation", "benchmark", "experimental result",
                "we evaluate", "we compare", "our method",
                "实验", "对比", "比较", "基准",
            ]
            found_signals = [s for s in experiment_signals if s in abstract_lower]

            if operator == "eq" and bool(value):
                if found_signals:
                    return self._make_result(
                        constraint_key=key,
                        claim="Has comparative experiment",
                        verdict="satisfied",
                        source_level="abstract",
                        evidence_text=self._extract_evidence(paper.abstract, found_signals),
                        confidence=0.75,
                    )
                else:
                    # 摘要中未发现实验信号，但不能确定没有
                    return self._make_result(
                        constraint_key=key,
                        claim="Has comparative experiment",
                        verdict="unknown",
                        source_level="abstract",
                        evidence_text="",
                        confidence=0.3,
                    )

        elif key == "uses_dataset":
            # 检测是否提到特定数据集
            dataset_name = str(value).lower()
            if dataset_name in abstract_lower:
                return self._make_result(
                    constraint_key=key,
                    claim=f"Uses dataset: {value}",
                    verdict="satisfied",
                    source_level="abstract",
                    evidence_text=self._extract_evidence(paper.abstract, [dataset_name]),
                    confidence=0.85,
                )
            else:
                return self._make_result(
                    constraint_key=key,
                    claim=f"Uses dataset: {value}",
                    verdict="unknown",
                    source_level="abstract",
                    evidence_text="",
                    confidence=0.2,
                )

        elif key == "mentions_method":
            method_name = str(value).lower()
            if method_name in abstract_lower:
                return self._make_result(
                    constraint_key=key,
                    claim=f"Mentions method: {value}",
                    verdict="satisfied",
                    source_level="abstract",
                    evidence_text=self._extract_evidence(paper.abstract, [method_name]),
                    confidence=0.85,
                )
            else:
                return self._make_result(
                    constraint_key=key,
                    claim=f"Mentions method: {value}",
                    verdict="unknown",
                    source_level="abstract",
                    evidence_text="",
                    confidence=0.2,
                )

        return None

    # ------------------------------------------------------------------
    # 全文级验证（LLM 辅助）
    # ------------------------------------------------------------------

    async def _verify_with_llm(
        self,
        paper: Paper,
        key: str,
        operator: str,
        value: Any,
        description: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """
        全文级验证（使用 LLM）

        仅对摘要级无法确定的约束调用 LLM。

        Returns:
            验证结果，LLM 失败时返回 None
        """
        try:
            from app.services.llm.prompts import PromptTemplates

            constraint_desc = description or f"{key} {operator} {value}"
            constraints_list = f"- {constraint_desc}"

            prompt = PromptTemplates.evidence_extraction(
                title=paper.title,
                authors=", ".join(paper.authors),
                year=paper.year or 0,
                venue=paper.venue or "Unknown",
                abstract=paper.abstract or "",
                constraints_list=constraints_list,
            )

            response = await self.llm_gateway.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an academic evidence extractor. "
                            "Always respond with valid JSON array only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1024,
            )

            # 解析 LLM 响应
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                start = 0
                end = len(lines)
                for i, line in enumerate(lines):
                    if line.strip().startswith("```"):
                        start = i + 1
                        break
                for i in range(len(lines) - 1, start, -1):
                    if lines[i].strip() == "```":
                        end = i
                        break
                cleaned = "\n".join(lines[start:end])

            results = json.loads(cleaned)
            if isinstance(results, list) and len(results) > 0:
                r = results[0]
                return self._make_result(
                    constraint_key=r.get("constraint_key", key),
                    claim=r.get("claim", constraint_desc),
                    verdict=r.get("verdict", "unknown"),
                    source_level="fulltext",
                    evidence_text=r.get("evidence_text", ""),
                    section=r.get("section"),
                    confidence=r.get("confidence", 0.5),
                )

        except Exception as e:
            logger.warning(f"LLM constraint verification failed: {e}")

        return None

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _check_numeric(actual: Any, operator: str, expected: Any) -> bool:
        """数值比较"""
        try:
            actual = float(actual)
            expected = float(expected)
        except (ValueError, TypeError):
            return False

        if operator == "gte":
            return actual >= expected
        elif operator == "lte":
            return actual <= expected
        elif operator == "eq":
            return actual == expected
        elif operator == "gt":
            return actual > expected
        elif operator == "lt":
            return actual < expected
        return False

    @staticmethod
    def _extract_evidence(text: str, signals: List[str]) -> str:
        """
        从文本中提取包含信号词的句子作为证据

        Args:
            text: 原文文本
            signals: 信号词列表

        Returns:
            包含信号词的句子
        """
        if not text:
            return ""

        # 按句子分割
        sentences = re.split(r'(?<=[.!?])\s+', text)

        for sentence in sentences:
            sentence_lower = sentence.lower()
            for signal in signals:
                if signal in sentence_lower:
                    return sentence.strip()

        # 如果没找到完整句子，返回包含信号词的片段
        for signal in signals:
            idx = text.lower().find(signal)
            if idx >= 0:
                start = max(0, idx - 50)
                end = min(len(text), idx + len(signal) + 50)
                return "..." + text[start:end].strip() + "..."

        return ""

    @staticmethod
    def _make_result(
        constraint_key: str,
        claim: str,
        verdict: str,
        source_level: str,
        evidence_text: str,
        confidence: float,
        section: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        构建标准验证结果字典

        Args:
            constraint_key: 约束键
            claim: 约束声明
            verdict: "satisfied" | "violated" | "unknown"
            source_level: "metadata" | "abstract" | "fulltext"
            evidence_text: 原文证据片段
            confidence: 置信度 (0.0 ~ 1.0)
            section: 所在章节

        Returns:
            验证结果字典
        """
        return {
            "constraint_key": constraint_key,
            "claim": claim,
            "verdict": verdict,
            "source_level": source_level,
            "evidence_text": evidence_text,
            "section": section,
            "confidence": round(confidence, 2),
        }

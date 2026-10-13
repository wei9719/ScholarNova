"""
OpenAlex 数据源适配器

API 文档: https://docs.openalex.org/
特点:
- 完全开放，无需 API Key
- Polite Pool（通过 email 参数获得更高速率）
- 概念标注、OA 信息丰富
- abstract_inverted_index 需要反转为正常文本
- 429 自动重试 + 指数退避
"""

import logging
from datetime import date
from typing import List, Optional

from app.schemas.paper import Paper
from app.services.sources.base import BaseSource, make_paper_uuid

logger = logging.getLogger(__name__)


class OpenAlexSource(BaseSource):
    """
    OpenAlex 数据源适配器

    API: https://api.openalex.org
    Polite Pool: 传入 email 参数可获得更快请求速率
    """

    def __init__(self, email: Optional[str] = None, **kwargs):
        """
        初始化

        Args:
            email: 邮箱（用于 Polite Pool，获得更快的请求速度）
        """
        super().__init__(**kwargs)
        self.email = email

    @property
    def name(self) -> str:
        return "openalex"

    @property
    def base_api_url(self) -> str:
        return "https://api.openalex.org"

    def _get_polite_params(self) -> dict:
        """Return optional identification and API-key parameters."""
        params = {}
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    async def search(
        self, query: str, max_results: int = 50, *,
        date_from: str | None = None, date_to: str | None = None,
    ) -> List[Paper]:
        """
        搜索论文

        Args:
            query: 查询字符串
            max_results: 最大结果数（API 上限 200）

        Returns:
            论文列表
        """
        params = {
            "search": query,
            "per_page": min(max_results, 200),
            **self._get_polite_params(),
        }
        filters = []
        if date_from:
            filters.append(f"from_publication_date:{date_from}")
        if date_to:
            filters.append(f"to_publication_date:{date_to}")
        if filters:
            params["filter"] = ",".join(filters)

        try:
            response = await self._request_with_retry("GET", "/works", params=params)
            data = response.json()

            papers = []
            for item in data.get("results", []):
                paper = self._parse_paper(item)
                if paper:
                    papers.append(paper)

            logger.info(f"[openalex] 搜索 '{query}' 返回 {len(papers)} 篇论文")
            return papers

        except Exception as e:
            logger.error(f"[openalex] 搜索失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 论文详情
    # ------------------------------------------------------------------

    async def get_paper(self, paper_id: str) -> Optional[Paper]:
        """
        获取单篇论文

        Args:
            paper_id: OpenAlex ID（如 W2741809807）

        Returns:
            论文详情
        """
        params = self._get_polite_params()

        try:
            response = await self._request_with_retry("GET", f"/works/{paper_id}", params=params)
            data = response.json()
            return self._parse_paper(data)

        except Exception as e:
            logger.error(f"[openalex] 获取论文 {paper_id} 失败: {e}")
            return None

    # ------------------------------------------------------------------
    # PDF URL
    # ------------------------------------------------------------------

    async def get_pdf_url(self, paper_id: str) -> Optional[str]:
        """
        获取论文 PDF 链接

        Args:
            paper_id: OpenAlex ID

        Returns:
            PDF URL，不可用返回 None
        """
        paper = await self.get_paper(paper_id)
        return paper.pdf_url if paper else None

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def health_check(self) -> dict:
        """
        健康检查：尝试一次简单的 works 查询

        Returns:
            {"status": "ok", ...} 或 {"status": "error", "message": "..."}
        """
        try:
            params = {"per_page": 1, **self._get_polite_params()}
            response = await self._request_with_retry("GET", "/works", params=params)
            data = response.json()
            count = data.get("meta", {}).get("count", 0)
            return {
                "status": "ok",
                "source": self.name,
                "message": f"API 可用，索引论文数: {count}",
            }
        except Exception as e:
            return {
                "status": "error",
                "source": self.name,
                "message": str(e),
            }

    # ------------------------------------------------------------------
    # 数据解析
    # ------------------------------------------------------------------

    def _parse_paper(self, data: dict) -> Optional[Paper]:
        """
        解析 API 返回的论文数据为统一 Paper 对象

        Args:
            data: API 返回的论文 JSON

        Returns:
            Paper 对象，解析失败返回 None
        """
        try:
            raw_id = data.get("id", "")
            # 去掉 URL 前缀，提取纯 ID（如 W2741809807）
            openalex_id = raw_id.replace("https://openalex.org/", "")
            if not openalex_id:
                return None

            # 作者列表
            authors = [
                a["author"]["display_name"]
                for a in data.get("authorships", [])
                if a.get("author", {}).get("display_name")
            ]

            # DOI（去掉 URL 前缀）
            doi = data.get("doi")
            if doi:
                doi = doi.replace("https://doi.org/", "")

            # OA 信息
            open_access = data.get("open_access") or {}
            is_oa = open_access.get("is_oa", False)
            pdf_url = open_access.get("oa_url") if is_oa else None

            # 年份
            year = data.get("publication_year")
            if year is not None:
                year = int(year)
            publication_date = None
            try:
                if data.get("publication_date"):
                    publication_date = date.fromisoformat(data["publication_date"])
            except (ValueError, TypeError):
                pass  # Keep usable year metadata without inventing a specific day.

            # 期刊/会议
            venue = None
            primary_location = data.get("primary_location")
            if primary_location:
                source = primary_location.get("source")
                if source:
                    venue = source.get("display_name")

            # 摘要（倒排索引反转）
            abstract = self._reconstruct_abstract(data.get("abstract_inverted_index"))

            return Paper(
                id=make_paper_uuid("openalex", openalex_id),
                title=data.get("title") or "",
                authors=authors,
                abstract=abstract,
                year=year,
                publication_date=publication_date,
                venue=venue,
                citation_count=data.get("cited_by_count", 0),
                doi=doi,
                url=raw_id,
                pdf_url=pdf_url,
                source="openalex",
                is_open_access=is_oa,
            )

        except Exception as e:
            logger.error(f"[openalex] 解析论文数据失败: {e}")
            return None

    def _reconstruct_abstract(self, inverted_index: Optional[dict]) -> Optional[str]:
        """
        重建摘要

        OpenAlex 使用倒排索引存储摘要: {"word": [pos1, pos2], ...}
        需要反转为正常文本。

        Args:
            inverted_index: 倒排索引字典

        Returns:
            重建后的摘要文本
        """
        if not inverted_index:
            return None

        try:
            word_positions: dict[int, str] = {}
            for word, positions in inverted_index.items():
                for pos in positions:
                    word_positions[pos] = word

            sorted_positions = sorted(word_positions.keys())
            return " ".join(word_positions[pos] for pos in sorted_positions)

        except Exception as e:
            logger.error(f"[openalex] 重建摘要失败: {e}")
            return None

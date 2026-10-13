"""
CrossRef 数据源适配器

API 文档: https://api.crossref.org/
特点:
- DOI 元数据权威，出版信息完整
- Polite Pool（通过 mailto User-Agent 参数获得更高速率）
- 429 自动重试 + 指数退避
"""

import html
import logging
import re
from datetime import date
from typing import List, Optional

from app.schemas.paper import Paper
from app.services.sources.base import BaseSource, make_paper_uuid

logger = logging.getLogger(__name__)


class CrossRefSource(BaseSource):
    """
    CrossRef 数据源适配器

    API: https://api.crossref.org
    Polite Pool: User-Agent 中包含 mailto 参数
    """

    def __init__(self, email: Optional[str] = None, **kwargs):
        """
        初始化

        Args:
            email: 邮箱（用于 Polite Pool）
        """
        super().__init__(**kwargs)
        self.email = email

    @property
    def name(self) -> str:
        return "crossref"

    @property
    def base_api_url(self) -> str:
        return "https://api.crossref.org"

    def _get_headers(self) -> dict:
        headers = super()._get_headers()
        if self.email:
            headers["User-Agent"] = f"ScholarNova/1.0 (mailto:{self.email})"
        return headers

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
            max_results: 最大结果数（API 上限 1000）

        Returns:
            论文列表
        """
        params = {
            "query": query,
            "rows": min(max_results, 1000),
            "sort": "relevance",
            "order": "desc",
        }
        filters = []
        if date_from:
            filters.append(f"from-pub-date:{date_from}")
        if date_to:
            filters.append(f"until-pub-date:{date_to}")
        if filters:
            params["filter"] = ",".join(filters)

        try:
            response = await self._request_with_retry("GET", "/works", params=params)
            data = response.json()

            papers = []
            for item in data.get("message", {}).get("items", []):
                paper = self._parse_paper(item)
                if paper:
                    papers.append(paper)

            logger.info(f"[crossref] 搜索 '{query}' 返回 {len(papers)} 篇论文")
            return papers

        except Exception as e:
            logger.error(f"[crossref] 搜索失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 论文详情
    # ------------------------------------------------------------------

    async def get_paper(self, paper_id: str) -> Optional[Paper]:
        """
        获取单篇论文

        Args:
            paper_id: DOI（如 10.1000/test1）

        Returns:
            论文详情
        """
        try:
            # DOI 中的 / 需要 URL 编码
            encoded_doi = paper_id.replace("/", "%2F")
            response = await self._request_with_retry("GET", f"/works/{encoded_doi}")
            data = response.json()
            return self._parse_paper(data.get("message", {}))

        except Exception as e:
            logger.error(f"[crossref] 获取论文 {paper_id} 失败: {e}")
            return None

    # ------------------------------------------------------------------
    # PDF URL
    # ------------------------------------------------------------------

    async def get_pdf_url(self, paper_id: str) -> Optional[str]:
        """
        获取论文 PDF 链接

        Args:
            paper_id: DOI

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
            params = {"rows": 1}
            response = await self._request_with_retry("GET", "/works", params=params)
            data = response.json()
            count = data.get("message", {}).get("total-results", 0)
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

    @staticmethod
    def _clean_abstract(value: Optional[str]) -> Optional[str]:
        """Convert CrossRef JATS/HTML abstracts into readable plain text."""
        if not value:
            return None
        text = re.sub(r"<[^>]+>", " ", value)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip() or None

    def _parse_paper(self, data: dict) -> Optional[Paper]:
        """
        解析 API 返回的论文数据为统一 Paper 对象

        Args:
            data: API 返回的论文 JSON（message 层）

        Returns:
            Paper 对象，解析失败返回 None
        """
        try:
            doi = data.get("DOI", "")
            if not doi:
                return None

            # 作者列表（given + family）
            authors = []
            for author in data.get("author", []):
                parts = []
                if author.get("given"):
                    parts.append(author["given"])
                if author.get("family"):
                    parts.append(author["family"])
                if parts:
                    authors.append(" ".join(parts))

            # Prefer the earliest reported publication, not DOI registration.
            # A year/month-only value must not become a fabricated exact date.
            publication_parts = []
            for date_field in ("published-online", "published-print", "published", "issued"):
                parts = (data.get(date_field) or {}).get("date-parts", [[]])
                if parts and parts[0]:
                    try:
                        values = tuple(int(value) for value in parts[0][:3])
                        date(*values, *([1] * (3 - len(values))))
                        publication_parts.append(values)
                    except (ValueError, TypeError):
                        continue
            earliest = min(publication_parts) if publication_parts else ()
            year = earliest[0] if earliest else None
            publication_date = date(*earliest) if len(earliest) == 3 else None

            # 期刊/会议
            venue = None
            container = data.get("container-title")
            if container:
                venue = container[0] if isinstance(container, list) else container

            # 标题
            title = data.get("title", [])
            title = title[0] if isinstance(title, list) and title else (title if isinstance(title, str) else "")

            # PDF 链接
            pdf_url = None
            for link in data.get("link", []):
                if link.get("content-type") == "application/pdf":
                    pdf_url = link.get("URL")
                    break
            # 也检查 free-to-read
            if not pdf_url:
                free_to_read = data.get("free-to-read")
                if free_to_read:
                    pdf_url = free_to_read.get("URL")

            return Paper(
                id=make_paper_uuid("crossref", doi),
                title=title,
                authors=authors,
                abstract=self._clean_abstract(data.get("abstract")),
                year=year,
                publication_date=publication_date,
                venue=venue,
                citation_count=data.get("is-referenced-by-count", 0),
                doi=doi,
                url=data.get("URL", f"https://doi.org/{doi}"),
                pdf_url=pdf_url,
                source="crossref",
                is_open_access=pdf_url is not None,
            )

        except Exception as e:
            logger.error(f"[crossref] 解析论文数据失败: {e}")
            return None

"""
arXiv 数据源适配器

API 文档: https://info.arxiv.org/help/api/
特点:
- CS/物理/数学预印本，大部分 PDF 免费
- 返回 Atom XML 格式
- 官方要求请求间隔不少于 3 秒
- 无引用数数据
"""

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import List, Optional

from app.schemas.paper import Paper
from app.services.sources.base import BaseSource, make_paper_uuid

logger = logging.getLogger(__name__)

# arXiv 命名空间
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# arXiv 官方要求的最小请求间隔（秒）
_MIN_REQUEST_INTERVAL = 3.0


class ArxivSource(BaseSource):
    """
    arXiv 数据源适配器

    API: http://export.arxiv.org/api/query
    限流: 遵守官方 3 秒请求间隔
    注意: 返回 Atom XML，需解析 XML
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("base_delay", 5.0)
        super().__init__(**kwargs)
        self._last_request_time: float = 0.0

    @property
    def name(self) -> str:
        return "arxiv"

    @property
    def base_api_url(self) -> str:
        return "https://export.arxiv.org/api"

    def _get_headers(self) -> dict:
        # arXiv 不需要 JSON Accept 头
        headers = {
            "User-Agent": "ScholarNova/1.0 (https://github.com/scholar-nova)",
        }
        return headers

    # ------------------------------------------------------------------
    # 限流：遵守 arXiv 3 秒间隔
    # ------------------------------------------------------------------

    async def _enforce_rate_limit(self):
        """确保两次请求之间至少间隔 _MIN_REQUEST_INTERVAL 秒"""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            wait = _MIN_REQUEST_INTERVAL - elapsed
            logger.debug(f"[arxiv] 限流等待 {wait:.1f}s")
            await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()

    async def _request_with_arxiv_limit(self, url: str, **kwargs):
        """
        带 arXiv 限流的 GET 请求

        先执行 3 秒间隔限流，再调用基类的 _request_with_retry 处理 429 重试。
        """
        await self._enforce_rate_limit()
        return await self._request_with_retry("GET", url, **kwargs)

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
            max_results: 最大结果数（API 上限 30000，但建议不超过 300）

        Returns:
            论文列表
        """
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(max_results, 300),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        if date_from or date_to:
            # arXiv's date range is first-submission time (GMT), not journal publication.
            start = (date_from or "1991-01-01").replace("-", "") + "0000"
            end = (date_to or date.today().isoformat()).replace("-", "") + "2359"
            params["search_query"] = f"(all:{query}) AND submittedDate:[{start} TO {end}]"

        try:
            response = await self._request_with_arxiv_limit("/query", params=params)
            papers = self._parse_atom_feed(response.text)
            logger.info(f"[arxiv] 搜索 '{query}' 返回 {len(papers)} 篇论文")
            return papers

        except Exception as e:
            logger.error(f"[arxiv] 搜索失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 论文详情
    # ------------------------------------------------------------------

    async def get_paper(self, paper_id: str) -> Optional[Paper]:
        """
        获取单篇论文

        Args:
            paper_id: arXiv ID（如 2301.07041 或 2301.07041v2）

        Returns:
            论文详情
        """
        params = {"id_list": paper_id}

        try:
            response = await self._request_with_arxiv_limit("/query", params=params)
            papers = self._parse_atom_feed(response.text)
            return papers[0] if papers else None

        except Exception as e:
            logger.error(f"[arxiv] 获取论文 {paper_id} 失败: {e}")
            return None

    # ------------------------------------------------------------------
    # PDF URL
    # ------------------------------------------------------------------

    async def get_pdf_url(self, paper_id: str) -> Optional[str]:
        """
        获取论文 PDF 链接

        arXiv 论文 PDF 链接格式固定: http://arxiv.org/pdf/{id}

        Args:
            paper_id: arXiv ID

        Returns:
            PDF URL
        """
        # arXiv PDF 链接格式固定，无需查询 API
        clean_id = paper_id.strip()
        if clean_id:
            return f"http://arxiv.org/pdf/{clean_id}"
        return None

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def health_check(self) -> dict:
        """
        健康检查：尝试一次简单的查询

        Returns:
            {"status": "ok", ...} 或 {"status": "error", "message": "..."}
        """
        try:
            params = {
                "search_query": "all:test",
                "max_results": 1,
            }
            response = await self._request_with_arxiv_limit("/query", params=params)
            papers = self._parse_atom_feed(response.text)
            return {
                "status": "ok",
                "source": self.name,
                "message": f"API 可用，返回 {len(papers)} 条结果",
            }
        except Exception as e:
            return {
                "status": "error",
                "source": self.name,
                "message": str(e),
            }

    # ------------------------------------------------------------------
    # XML 解析
    # ------------------------------------------------------------------

    def _parse_atom_feed(self, xml_text: str) -> List[Paper]:
        """
        解析 Atom feed XML

        Args:
            xml_text: XML 文本

        Returns:
            论文列表
        """
        papers = []

        try:
            root = ET.fromstring(xml_text)

            for entry in root.findall("atom:entry", _NS):
                paper = self._parse_entry(entry)
                if paper:
                    papers.append(paper)

        except ET.ParseError as e:
            logger.error(f"[arxml] XML 解析失败: {e}")
        except Exception as e:
            logger.error(f"[arxiv] 解析 Atom feed 失败: {e}")

        return papers

    def _parse_entry(self, entry: ET.Element) -> Optional[Paper]:
        """
        解析单个 Atom entry

        Args:
            entry: XML 元素

        Returns:
            Paper 对象，解析失败返回 None
        """
        try:
            # arXiv ID 和 URL
            id_elem = entry.find("atom:id", _NS)
            arxiv_url = id_elem.text.strip() if id_elem is not None and id_elem.text else ""
            arxiv_id = arxiv_url.split("/abs/")[-1] if "/abs/" in arxiv_url else ""

            if not arxiv_id:
                return None

            # 标题（清理换行和多余空格）
            title_elem = entry.find("atom:title", _NS)
            title = ""
            if title_elem is not None and title_elem.text:
                title = re.sub(r"\s+", " ", title_elem.text.strip())

            # 摘要
            summary_elem = entry.find("atom:summary", _NS)
            abstract = summary_elem.text.strip() if summary_elem is not None and summary_elem.text else None

            # 作者
            authors = []
            for author_elem in entry.findall("atom:author", _NS):
                name_elem = author_elem.find("atom:name", _NS)
                if name_elem is not None and name_elem.text:
                    authors.append(name_elem.text.strip())

            # 发表年份
            published_elem = entry.find("atom:published", _NS)
            year = None
            publication_date = None
            if published_elem is not None and published_elem.text:
                match = re.match(r"(\d{4})", published_elem.text)
                if match:
                    year = int(match.group(1))
                try:
                    publication_date = date.fromisoformat(published_elem.text[:10])
                except ValueError:
                    pass

            # PDF 链接
            pdf_url = None
            for link_elem in entry.findall("atom:link", _NS):
                if link_elem.get("title") == "pdf":
                    pdf_url = link_elem.get("href")
                    break

            # 分类/关键词
            categories = []
            for cat_elem in entry.findall("atom:category", _NS):
                term = cat_elem.get("term")
                if term:
                    categories.append(term)

            # DOI（arXiv 有时会提供）
            doi = None
            doi_elem = entry.find("arxiv:doi", _NS)
            if doi_elem is not None and doi_elem.text:
                doi = doi_elem.text.strip()

            return Paper(
                id=make_paper_uuid("arxiv", arxiv_id),
                title=title,
                authors=authors,
                abstract=abstract,
                year=year,
                publication_date=publication_date,
                venue="arXiv",
                citation_count=0,  # arXiv 不提供引用数
                doi=doi,
                url=arxiv_url,
                pdf_url=pdf_url,
                source="arxiv",
                is_open_access=True,  # arXiv 论文全部开放获取
            )

        except Exception as e:
            logger.error(f"[arxiv] 解析 entry 失败: {e}")
            return None

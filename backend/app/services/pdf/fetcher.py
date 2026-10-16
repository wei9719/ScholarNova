"""
PDF 全文获取器

从合法 OA（Open Access）来源获取 PDF 文件。
支持的来源（按优先级）：
1. 直接 PDF 链接（来自数据源适配器）
2. Unpaywall API（通过 DOI 检测 OA 版本）
3. arXiv 直连（预印本）
4. 会议官网直连（NeurIPS/ICLR/ICML/ACL）
5. PubMed Central（生物医学）
"""

import asyncio
import hashlib
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import httpx

from app.core.ssrf import validate_url
from app.services.sources.semantic_scholar import SemanticScholarSource

logger = logging.getLogger(__name__)

# 下载限制
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
DOWNLOAD_TIMEOUT = 30  # 秒

# Unpaywall API 基础 URL
UNPAYWALL_API = "https://api.unpaywall.org/v2"
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"
USER_AGENT = "ScholarNova/1.2.0 (+https://github.com/zhangweiguo9719-web/ScholarNova)"


async def _pdf_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    """Validate each hop before sending a request; never log signed URLs."""
    current = url
    for hop in range(6):
        parsed = urlparse(current)
        if parsed.username or parsed.password:
            raise ValueError("PDF 地址不能包含账号密码")
        valid, reason = await asyncio.to_thread(validate_url, current)
        if not valid:
            raise ValueError(reason or "PDF 地址不安全")
        async with client.stream("GET", current) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location or hop == 5:
                    raise ValueError("PDF 下载重定向过多或缺少目标地址")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            declared = int(response.headers.get("content-length") or 0)
            if declared > MAX_FILE_SIZE:
                raise ValueError("PDF 不能超过 50 MB")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise ValueError("PDF 不能超过 50 MB")
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content.startswith(b"%PDF-"):
                raise ValueError("返回内容不是 PDF，请通过机构授权获取或手动导入")
            return content
    raise ValueError("PDF 下载重定向过多")

# arXiv PDF 基础 URL
ARXIV_PDF_BASE = "https://arxiv.org/pdf"

# 已知会议的 PDF 基础 URL
_CONFERENCE_PATTERNS: dict[str, str] = {
    "neurips": "https://proceedings.neurips.cc/paper_files/paper",
    "nips": "https://proceedings.neurips.cc/paper_files/paper",
    "iclr": "https://openreview.net/pdf",
    "icml": "https://proceedings.mlr.press",
    "acl": "https://aclanthology.org",
    "emnlp": "https://aclanthology.org",
    "naacl": "https://aclanthology.org",
    "aaai": "https://ojs.aaai.org/index.php/AAAI/article/view",
}

# PubMed Central PDF 基础 URL
PMC_PDF_BASE = "https://www.ncbi.nlm.nih.gov/pmc/articles"


@dataclass
class FetchResult:
    """PDF 获取结果"""

    success: bool
    pdf_path: Optional[Path] = None
    source: str = ""  # 获取来源（如 "direct", "unpaywall", "arxiv"）
    url: str = ""
    error: str = ""
    file_size: int = 0


class PDFFetcher:
    """
    PDF 全文获取器

    从合法 OA 来源获取 PDF 文件，支持缓存。
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        unpaywall_email: Optional[str] = None,
        semantic_scholar_api_key: Optional[str] = None,
        openalex_email: Optional[str] = None,
        openalex_api_key: Optional[str] = None,
    ):
        """
        初始化获取器。

        Args:
            cache_dir: PDF 缓存目录。为 None 时使用系统临时目录。
            unpaywall_email: Unpaywall API 邮箱（推荐提供以获得更高速率）。
        """
        self.cache_dir = cache_dir or Path(tempfile.gettempdir()) / "scholar_pdf_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.unpaywall_email = unpaywall_email
        self.semantic_scholar_api_key = semantic_scholar_api_key
        self.openalex_email = openalex_email
        self.openalex_api_key = openalex_api_key
        self._failed_urls: set[str] = set()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def fetch(
        self,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        pdf_url: Optional[str] = None,
        venue: Optional[str] = None,
        title: Optional[str] = None,
    ) -> FetchResult:
        """
        获取 PDF 文件。按优先级尝试多种来源。

        Args:
            doi: 论文 DOI
            arxiv_id: arXiv ID（如 "2301.12345"）
            pdf_url: 直接 PDF 链接
            venue: 发表场所（用于会议官网直连）
            title: 论文标题（用于缓存键生成）

        Returns:
            FetchResult
        """
        # 检查缓存
        cache_key = self._make_cache_key(doi or arxiv_id or pdf_url or title or "")
        cached = self._check_cache(cache_key)
        if cached:
            logger.info(f"PDF cache hit: {cache_key}")
            return FetchResult(
                success=True,
                pdf_path=cached,
                source="cache",
                file_size=cached.stat().st_size,
            )

        errors: list[str] = []

        # 1. 数据源提供的直接 PDF 链接
        if pdf_url:
            result = await self._download_pdf(pdf_url, "direct", cache_key)
            if result.success:
                return result
            errors.append(f"direct: {result.error}")

        # 2. arXiv 直连（已知 arXiv ID 时最可靠）
        if arxiv_id:
            result = await self._fetch_from_arxiv(arxiv_id, cache_key)
            if result.success:
                return result
            errors.append(f"arxiv: {result.error}")

        # 3. DOI 聚合服务：OpenAlex、Unpaywall、Semantic Scholar、Crossref
        if doi:
            result = await self._fetch_from_openalex(doi, cache_key)
            if result.success:
                return result
            errors.append(f"openalex: {result.error}")

            result = await self._fetch_from_unpaywall(doi, cache_key)
            if result.success:
                return result
            errors.append(f"unpaywall: {result.error}")

            result = await self._fetch_from_semantic_scholar(doi, cache_key)
            if result.success:
                return result
            errors.append(f"semantic_scholar: {result.error}")

            result = await self._fetch_from_crossref(doi, cache_key)
            if result.success:
                return result
            errors.append(f"crossref: {result.error}")

        # 4. 会议官网直连
        if venue and doi:
            result = await self._fetch_from_conference(venue, doi, cache_key)
            if result.success:
                return result
            errors.append(f"conference: {result.error}")

        # 5. PubMed Central
        if doi:
            result = await self._fetch_from_pmc(doi, cache_key)
            if result.success:
                return result
            errors.append(f"pmc: {result.error}")

        logger.warning(
            f"Failed to fetch PDF from any source: doi={doi}, arxiv_id={arxiv_id}"
        )
        return FetchResult(
            success=False,
            error="未能从合法开放来源取得 PDF。" + "；".join(errors)[:1200],
        )

    async def fetch_batch(
        self,
        papers: list[dict],
        max_concurrent: int = 5,
    ) -> list[FetchResult]:
        """
        批量获取 PDF。

        Args:
            papers: 论文信息列表，每个元素包含 doi、arxiv_id、pdf_url 等字段
            max_concurrent: 最大并发数

        Returns:
            FetchResult 列表
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _fetch_one(paper: dict) -> FetchResult:
            async with semaphore:
                return await self.fetch(
                    doi=paper.get("doi"),
                    arxiv_id=paper.get("arxiv_id"),
                    pdf_url=paper.get("pdf_url"),
                    venue=paper.get("venue"),
                    title=paper.get("title"),
                )

        tasks = [_fetch_one(p) for p in papers]
        return await asyncio.gather(*tasks)

    # ------------------------------------------------------------------
    # 来源1：直接下载
    # ------------------------------------------------------------------

    async def _download_pdf(
        self, url: str, source: str, cache_key: str
    ) -> FetchResult:
        """
        从 URL 下载 PDF。

        Args:
            url: PDF URL
            source: 来源标识
            cache_key: 缓存键

        Returns:
            FetchResult
        """
        if url in self._failed_urls:
            return FetchResult(
                success=False,
                source=source,
                url=url,
                error="同一 PDF 地址已失败，已跳过重复下载",
            )
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=DOWNLOAD_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"},
            ) as client:
                content = await _pdf_bytes(client, url)

                # 保存到缓存
                pdf_path = self._save_to_cache(cache_key, content)
                logger.info("Downloaded PDF from %s", source)
                return FetchResult(
                    success=True,
                    pdf_path=pdf_path,
                    source=source,
                    url=url,
                    file_size=len(content),
                )

        except httpx.TimeoutException:
            self._failed_urls.add(url)
            logger.warning("PDF download timed out (%s)", source)
            return FetchResult(
                success=False, source=source, url=url, error="Download timeout"
            )
        except Exception as e:
            self._failed_urls.add(url)
            logger.warning("PDF download failed (%s, %s)", source, type(e).__name__)
            return FetchResult(
                success=False, source=source, url=url,
                error=str(e) if isinstance(e, ValueError) else "PDF 下载失败，请检查访问权限或手动导入",
            )

    # ------------------------------------------------------------------
    # 来源2：Unpaywall
    # ------------------------------------------------------------------

    async def _fetch_from_unpaywall(
        self, doi: str, cache_key: str
    ) -> FetchResult:
        """
        通过 Unpaywall API 获取 OA 版本 PDF。

        Args:
            doi: 论文 DOI
            cache_key: 缓存键

        Returns:
            FetchResult
        """
        if not self._valid_email(self.unpaywall_email):
            return FetchResult(
                success=False,
                source="unpaywall",
                error="未配置有效联系邮箱，已跳过 Unpaywall",
            )
        url = f"{UNPAYWALL_API}/{quote(doi, safe='')}"
        params = {"email": self.unpaywall_email}

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    return FetchResult(
                        success=False,
                        source="unpaywall",
                        error=f"DOI not found in Unpaywall: {doi}",
                    )
                resp.raise_for_status()
                data = resp.json()

                # 查找最佳 OA 位置
                best_oa = data.get("best_oa_location")
                if best_oa and best_oa.get("url_for_pdf"):
                    pdf_url = best_oa["url_for_pdf"]
                    return await self._download_pdf(
                        pdf_url, "unpaywall", cache_key
                    )

                # 遍历所有 OA 位置
                for location in data.get("oa_locations", []):
                    pdf_url = location.get("url_for_pdf")
                    if pdf_url:
                        result = await self._download_pdf(
                            pdf_url, "unpaywall", cache_key
                        )
                        if result.success:
                            return result

                return FetchResult(
                    success=False,
                    source="unpaywall",
                    error=f"No OA PDF found for DOI: {doi}",
                )

        except Exception as e:
            logger.warning(f"Unpaywall lookup failed for {doi}: {e}")
            return FetchResult(
                success=False, source="unpaywall", error=str(e)
            )

    async def _fetch_from_openalex(self, doi: str, cache_key: str) -> FetchResult:
        """Resolve legal OA locations reported by OpenAlex."""
        url = f"{OPENALEX_API}/https://doi.org/{quote(doi, safe='')}"
        params: dict[str, str] = {}
        if self._valid_email(self.openalex_email):
            params["mailto"] = str(self.openalex_email)
        if self.openalex_api_key:
            params["api_key"] = self.openalex_api_key
        try:
            async with httpx.AsyncClient(
                timeout=15, headers={"User-Agent": USER_AGENT}
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    return FetchResult(False, source="openalex", error="DOI not found")
                resp.raise_for_status()
                data = resp.json()
            candidates: list[str] = []
            for location in (
                data.get("best_oa_location"),
                data.get("primary_location"),
                *(data.get("locations") or []),
            ):
                if isinstance(location, dict) and location.get("pdf_url"):
                    candidates.append(location["pdf_url"])
            oa_url = (data.get("open_access") or {}).get("oa_url")
            if oa_url and str(oa_url).lower().endswith(".pdf"):
                candidates.append(oa_url)
            return await self._download_candidates(candidates, "openalex", cache_key)
        except Exception as exc:
            logger.warning("OpenAlex PDF lookup failed for %s: %s", doi, exc)
            return FetchResult(False, source="openalex", error=str(exc))

    async def _fetch_from_semantic_scholar(
        self, doi: str, cache_key: str
    ) -> FetchResult:
        """Resolve an open-access PDF reported by Semantic Scholar."""
        try:
            async with SemanticScholarSource(
                api_key=self.semantic_scholar_api_key,
                timeout=15,
                max_retries=2,
            ) as source:
                paper = await source.get_paper(f"DOI:{doi}")
                pdf_url = paper.pdf_url if paper else None
            return await self._download_candidates(
                [pdf_url] if pdf_url else [], "semantic_scholar", cache_key
            )
        except Exception as exc:
            logger.warning("Semantic Scholar PDF lookup failed for %s: %s", doi, exc)
            return FetchResult(False, source="semantic_scholar", error=str(exc))

    async def _fetch_from_crossref(self, doi: str, cache_key: str) -> FetchResult:
        """Resolve publisher-declared PDF links from Crossref metadata."""
        url = f"{CROSSREF_API}/{quote(doi, safe='')}"
        try:
            async with httpx.AsyncClient(
                timeout=15, headers={"User-Agent": USER_AGENT}
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    return FetchResult(False, source="crossref", error="DOI not found")
                resp.raise_for_status()
                links = (resp.json().get("message") or {}).get("link") or []
            candidates = [
                item.get("URL") or item.get("url")
                for item in links
                if "pdf" in str(item.get("content-type") or "").lower()
            ]
            return await self._download_candidates(candidates, "crossref", cache_key)
        except Exception as exc:
            logger.warning("Crossref PDF lookup failed for %s: %s", doi, exc)
            return FetchResult(False, source="crossref", error=str(exc))

    async def _download_candidates(
        self, urls: list[Optional[str]], source: str, cache_key: str
    ) -> FetchResult:
        errors: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            result = await self._download_pdf(url, source, cache_key)
            if result.success:
                return result
            errors.append(result.error)
        return FetchResult(
            False,
            source=source,
            error="；".join(errors)[:400] if errors else "未报告可下载的开放 PDF",
        )

    # ------------------------------------------------------------------
    # 来源3：arXiv
    # ------------------------------------------------------------------

    async def _fetch_from_arxiv(
        self, arxiv_id: str, cache_key: str
    ) -> FetchResult:
        """
        从 arXiv 直接下载 PDF。

        Args:
            arxiv_id: arXiv ID（如 "2301.12345" 或 "2301.12345v2"）
            cache_key: 缓存键

        Returns:
            FetchResult
        """
        # 清理 arxiv_id（移除版本号用于 PDF URL）
        clean_id = arxiv_id.strip()
        # 移除可能的前缀
        for prefix in ["arxiv:", "arXiv:"]:
            if clean_id.lower().startswith(prefix.lower()):
                clean_id = clean_id[len(prefix) :]

        url = f"{ARXIV_PDF_BASE}/{clean_id}"
        return await self._download_pdf(url, "arxiv", cache_key)

    # ------------------------------------------------------------------
    # 来源4：会议官网
    # ------------------------------------------------------------------

    async def _fetch_from_conference(
        self, venue: str, doi: str, cache_key: str
    ) -> FetchResult:
        """
        从会议官网获取 PDF。

        Args:
            venue: 发表场所
            doi: 论文 DOI
            cache_key: 缓存键

        Returns:
            FetchResult
        """
        venue_lower = venue.lower()
        for conf_key, base_url in _CONFERENCE_PATTERNS.items():
            if conf_key in venue_lower:
                # 尝试构造 PDF URL
                # 这些 URL 模式因会议而异，这里尝试常见模式
                possible_urls = self._guess_conference_pdf_urls(
                    conf_key, base_url, doi
                )
                for url in possible_urls:
                    result = await self._download_pdf(
                        url, f"conference:{conf_key}", cache_key
                    )
                    if result.success:
                        return result

        return FetchResult(
            success=False,
            source="conference",
            error=f"No conference PDF pattern matched for venue: {venue}",
        )

    def _guess_conference_pdf_urls(
        self, conf_key: str, base_url: str, doi: str
    ) -> list[str]:
        """
        根据会议类型猜测可能的 PDF URL。

        Returns:
            可能的 URL 列表
        """
        urls: list[str] = []

        if conf_key in ("neurips", "nips"):
            # NeurIPS: https://proceedings.neurips.cc/paper_files/paper/2023/hash/xxx-Paper-Conference.pdf
            # 难以直接构造，跳过
            pass
        elif conf_key == "iclr":
            # ICLR 使用 OpenReview，需要 paper ID
            pass
        elif conf_key in ("acl", "emnlp", "naacl"):
            # ACL Anthology: https://aclanthology.org/2023.acl-long.123.pdf
            # 从 DOI 提取 anthology ID
            pass

        return urls

    # ------------------------------------------------------------------
    # 来源5：PubMed Central
    # ------------------------------------------------------------------

    async def _fetch_from_pmc(self, doi: str, cache_key: str) -> FetchResult:
        """
        从 PubMed Central 获取 PDF。

        Args:
            doi: 论文 DOI
            cache_key: 缓存键

        Returns:
            FetchResult
        """
        # 通过 NCBI ID converter API 查找 PMC ID
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                # 使用 ID 转换 API
                resp = await client.get(
                    "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
                    params={"ids": doi, "format": "json"},
                )
                if resp.status_code != 200:
                    return FetchResult(
                        success=False,
                        source="pmc",
                        error=f"NCBI ID converter returned {resp.status_code}",
                    )

                data = resp.json()
                records = data.get("records", [])
                if not records:
                    return FetchResult(
                        success=False,
                        source="pmc",
                        error=f"No PMC record found for DOI: {doi}",
                    )

                pmcid = records[0].get("pmcid")
                if not pmcid:
                    return FetchResult(
                        success=False,
                        source="pmc",
                        error=f"No PMC ID for DOI: {doi}",
                    )

                # 构造 PMC PDF URL
                pmc_id_num = pmcid.replace("PMC", "")
                pdf_url = f"{PMC_PDF_BASE}/{pmcid}/pdf/"
                return await self._download_pdf(pdf_url, "pmc", cache_key)

        except Exception as e:
            logger.warning(f"PMC lookup failed for {doi}: {e}")
            return FetchResult(
                success=False, source="pmc", error=str(e)
            )

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _is_pdf_content(content: bytes) -> bool:
        """检查内容是否为有效的 PDF 文件。"""
        # PDF 文件以 %PDF 开头
        return content[:5] == b"%PDF-"

    @staticmethod
    def _valid_email(value: Optional[str]) -> bool:
        return bool(value and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()))

    @staticmethod
    def _make_cache_key(identifier: str) -> str:
        """生成缓存键。"""
        return hashlib.md5(identifier.encode()).hexdigest()

    def _check_cache(self, cache_key: str) -> Optional[Path]:
        """检查缓存中是否存在。"""
        cache_path = self.cache_dir / f"{cache_key}.pdf"
        if cache_path.exists():
            return cache_path
        return None

    def _save_to_cache(self, cache_key: str, content: bytes) -> Path:
        """保存到缓存。"""
        cache_path = self.cache_dir / f"{cache_key}.pdf"
        cache_path.write_bytes(content)
        return cache_path

    def clear_cache(self) -> int:
        """
        清理缓存。

        Returns:
            删除的文件数
        """
        count = 0
        for pdf_file in self.cache_dir.glob("*.pdf"):
            pdf_file.unlink(missing_ok=True)
            count += 1
        return count

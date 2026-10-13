"""
Semantic Scholar 数据源适配器

API 文档: https://api.semanticscholar.org/api-docs/
特点:
- 支持引用网络（被引论文、参考文献）
- 可选 API Key（提高速率限制）
- 429 自动重试 + 指数退避
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import httpx

from app.schemas.paper import Paper
from app.services.sources.base import BaseSource, make_paper_uuid

logger = logging.getLogger(__name__)

_S2_MIN_INTERVAL_SECONDS = 1.25
_S2_LOCK_STALE_SECONDS = 60.0
_S2_RATE_DIR = Path(tempfile.gettempdir()) / "scholarnova"
_S2_RATE_LOCK_FILE = _S2_RATE_DIR / "semantic-scholar-rate.lock"
_S2_RATE_STATE_FILE = _S2_RATE_DIR / "semantic-scholar-rate.json"
_S2_CACHE_DIR = _S2_RATE_DIR / "semantic-scholar-cache"
_S2_CACHE_TTL_SECONDS = 24 * 60 * 60
_S2_NEGATIVE_CACHE_TTL_SECONDS = 10 * 60
_S2_CACHE: Dict[str, tuple] = {}
_S2_CACHE_LOCKS: Dict[str, asyncio.Lock] = {}

# 请求的论文字段
_SEARCH_FIELDS = (
    "corpusId,title,abstract,authors,year,publicationDate,venue,citationCount,"
    "externalIds,openAccessPdf,fieldsOfStudy"
)

_DETAIL_FIELDS = (
    "corpusId,title,abstract,authors,year,publicationDate,venue,citationCount,"
    "externalIds,openAccessPdf,fieldsOfStudy,references,citations"
)


class SemanticScholarSource(BaseSource):
    """
    Semantic Scholar 数据源适配器

    API: https://api.semanticscholar.org/graph/v1
    限流: 无 Key 100 req/5min, 有 Key 1 req/sec
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("base_delay", 5.0)
        super().__init__(**kwargs)

    @property
    def name(self) -> str:
        return "semantic_scholar"

    @property
    def base_api_url(self) -> str:
        return "https://api.semanticscholar.org/graph/v1"

    def _get_headers(self) -> dict:
        headers = {
            "User-Agent": "ScholarNova/1.0 (https://github.com/scholar-nova)",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def _before_request(self) -> None:
        """Reserve one account-wide request slot across backend and benchmark processes."""
        await self._acquire_rate_lock()
        try:
            state = self._read_rate_state()
            now = time.time()
            wait = max(
                _S2_MIN_INTERVAL_SECONDS - (now - float(state.get("last_request_at", 0.0))),
                float(state.get("blocked_until", 0.0)) - now,
            )
            if wait > 0:
                await asyncio.sleep(wait)
            state.update({"last_request_at": time.time(), "pid": os.getpid()})
            self._write_rate_state(state)
        finally:
            self._release_rate_lock()

    async def _after_response(self, response: httpx.Response) -> None:
        """Share a server-requested cooldown across every local S2 caller."""
        if response.status_code != 429 and not 200 <= response.status_code < 400:
            return
        await self._acquire_rate_lock()
        try:
            state = self._read_rate_state()
            if response.status_code == 429:
                retry_after = max(1.0, self._retry_delay(response, 0))
                state["blocked_until"] = max(
                    float(state.get("blocked_until", 0.0)),
                    time.time() + retry_after,
                )
            else:
                state["last_success_at"] = time.time()
                state["blocked_until"] = 0.0
            state["last_status"] = response.status_code
            self._write_rate_state(state)
        finally:
            self._release_rate_lock()

    @classmethod
    def quota_snapshot(cls) -> dict:
        """Return non-sensitive local quota diagnostics without consuming API quota."""
        state = cls._read_rate_state()
        now = time.time()
        return {
            "last_status": state.get("last_status"),
            "last_success_age_seconds": (
                max(0.0, now - float(state["last_success_at"]))
                if state.get("last_success_at")
                else None
            ),
            "cooldown_remaining_seconds": max(
                0.0,
                float(state.get("blocked_until", 0.0)) - now,
            ),
        }

    async def _acquire_rate_lock(self) -> None:
        _S2_RATE_DIR.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(30.0, float(self.timeout))
        while True:
            try:
                fd = os.open(
                    _S2_RATE_LOCK_FILE,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                try:
                    os.write(fd, f"{os.getpid()} {time.time()}".encode("ascii"))
                finally:
                    os.close(fd)
                break
            except FileExistsError:
                try:
                    age = time.time() - _S2_RATE_LOCK_FILE.stat().st_mtime
                    if age > _S2_LOCK_STALE_SECONDS:
                        _S2_RATE_LOCK_FILE.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("Semantic Scholar 全局配额锁等待超时")
                await asyncio.sleep(0.05)

    @staticmethod
    def _release_rate_lock() -> None:
        _S2_RATE_LOCK_FILE.unlink(missing_ok=True)

    @staticmethod
    def _read_rate_state() -> dict:
        try:
            value = json.loads(_S2_RATE_STATE_FILE.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_rate_state(state: dict) -> None:
        state_tmp = _S2_RATE_STATE_FILE.with_suffix(".tmp")
        state_tmp.write_text(json.dumps(state), encoding="utf-8")
        state_tmp.replace(_S2_RATE_STATE_FILE)

    @staticmethod
    def _cache_lock(key: str) -> asyncio.Lock:
        lock = _S2_CACHE_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _S2_CACHE_LOCKS[key] = lock
        return lock

    @staticmethod
    def _read_cache(key: str) -> Optional[List[Paper]]:
        cached = _S2_CACHE.get(key)
        if cached:
            expires_at, payload = cached
            if expires_at > time.monotonic():
                return [Paper(**item) for item in payload]
            _S2_CACHE.pop(key, None)

        cache_path = SemanticScholarSource._disk_cache_path(key)
        try:
            disk_value = json.loads(cache_path.read_text(encoding="utf-8"))
            expires_at_wall = float(disk_value["expires_at"])
            if expires_at_wall <= time.time():
                cache_path.unlink(missing_ok=True)
                return None
            payload = disk_value["papers"]
            _S2_CACHE[key] = (
                time.monotonic() + (expires_at_wall - time.time()),
                payload,
            )
            return [Paper(**item) for item in payload]
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None

    @staticmethod
    def _write_cache(key: str, papers: Sequence[Paper], ttl: int) -> None:
        payload = [paper.model_dump(mode="json") for paper in papers]
        _S2_CACHE[key] = (
            time.monotonic() + ttl,
            payload,
        )
        _S2_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = SemanticScholarSource._disk_cache_path(key)
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(
                {"expires_at": time.time() + ttl, "papers": payload},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(cache_path)

    @staticmethod
    def _disk_cache_path(key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return _S2_CACHE_DIR / f"{digest}.json"

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
            max_results: 最大结果数（API 上限 100）

        Returns:
            论文列表
        """
        normalized_query = re.sub(r"\s+", " ", query.replace("-", " ")).strip()
        cache_key = f"search:{normalized_query.casefold()}:{min(max_results, 100)}"
        if date_from or date_to:
            cache_key += f":dates:{date_from or ''}:{date_to or ''}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            self.last_error = None
            logger.info("[semantic_scholar] 搜索缓存命中")
            return cached

        params = {
            "query": normalized_query,
            "limit": min(max_results, 100),
            "fields": _SEARCH_FIELDS,
        }
        if date_from or date_to:
            params["publicationDateOrYear"] = f"{date_from or ''}:{date_to or ''}"

        async with self._cache_lock(cache_key):
            cached = self._read_cache(cache_key)
            if cached is not None:
                self.last_error = None
                return cached
            try:
                response = await self._request_with_retry("GET", "/paper/search", params=params)
                data = response.json()

                papers = []
                for item in data.get("data", []):
                    paper = self._parse_paper(item)
                    if paper:
                        papers.append(paper)

                ttl = (
                    _S2_CACHE_TTL_SECONDS
                    if papers
                    else _S2_NEGATIVE_CACHE_TTL_SECONDS
                )
                self._write_cache(cache_key, papers, ttl)
                logger.info(
                    f"[semantic_scholar] 搜索 '{normalized_query}' 返回 {len(papers)} 篇论文"
                )
                return papers

            except Exception as e:
                if not self.last_error:
                    self.last_error = str(e)
                logger.error(f"[semantic_scholar] 搜索失败: {e}")
                return []

    # ------------------------------------------------------------------
    # 论文详情
    # ------------------------------------------------------------------

    async def get_paper(self, paper_id: str) -> Optional[Paper]:
        """
        获取单篇论文

        Args:
            paper_id: S2 paperId 或 DOI (如 'DOI:10.1145/12345')

        Returns:
            论文详情
        """
        cache_key = f"paper:{paper_id.casefold()}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            self.last_error = None
            return cached[0] if cached else None

        params = {"fields": _DETAIL_FIELDS}

        async with self._cache_lock(cache_key):
            cached = self._read_cache(cache_key)
            if cached is not None:
                self.last_error = None
                return cached[0] if cached else None
            try:
                response = await self._request_with_retry(
                    "GET",
                    f"/paper/{paper_id}",
                    params=params,
                )
                data = response.json()
                paper = self._parse_paper(data)
                self._write_cache(
                    cache_key,
                    [paper] if paper else [],
                    _S2_CACHE_TTL_SECONDS,
                )
                return paper

            except Exception as e:
                if not self.last_error:
                    self.last_error = str(e)
                logger.error(f"[semantic_scholar] 获取论文 {paper_id} 失败: {e}")
                return None

    async def search_match(self, query: str) -> Optional[Paper]:
        """Resolve one closest title match through the official match endpoint."""
        normalized_query = re.sub(r"\s+", " ", query.replace("-", " ")).strip()
        cache_key = f"match:{normalized_query.casefold()}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            self.last_error = None
            return cached[0] if cached else None

        async with self._cache_lock(cache_key):
            cached = self._read_cache(cache_key)
            if cached is not None:
                self.last_error = None
                return cached[0] if cached else None
            try:
                response = await self._request_with_retry(
                    "GET",
                    "/paper/search/match",
                    params={"query": normalized_query, "fields": _SEARCH_FIELDS},
                )
                paper = self._parse_paper(response.json())
                self._write_cache(
                    cache_key,
                    [paper] if paper else [],
                    _S2_CACHE_TTL_SECONDS,
                )
                return paper
            except Exception as e:
                if not self.last_error:
                    self.last_error = str(e)
                logger.error(f"[semantic_scholar] 标题匹配失败: {e}")
                return None

    async def get_papers_batch(self, paper_ids: Sequence[str]) -> List[Paper]:
        """Use one official batch request to resolve up to 500 paper identifiers."""
        unique_ids = list(dict.fromkeys(paper_id for paper_id in paper_ids if paper_id))[:500]
        if not unique_ids:
            return []
        cache_key = "batch:" + "|".join(identifier.casefold() for identifier in unique_ids)
        cached = self._read_cache(cache_key)
        if cached is not None:
            self.last_error = None
            return cached

        async with self._cache_lock(cache_key):
            cached = self._read_cache(cache_key)
            if cached is not None:
                self.last_error = None
                return cached
            try:
                response = await self._request_with_retry(
                    "POST",
                    "/paper/batch",
                    params={"fields": _SEARCH_FIELDS},
                    json={"ids": unique_ids},
                )
                papers = [
                    paper
                    for item in response.json()
                    if item and (paper := self._parse_paper(item))
                ]
                self._write_cache(cache_key, papers, _S2_CACHE_TTL_SECONDS)
                return papers
            except Exception as e:
                if not self.last_error:
                    self.last_error = str(e)
                logger.error(f"[semantic_scholar] 批量论文解析失败: {e}")
                return []

    async def get_citations(
        self,
        paper_id: str,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[Paper]:
        """Fetch papers citing one seed paper through the official graph endpoint."""
        bounded_limit = min(max(1, limit), 1000)
        bounded_offset = max(0, offset)
        cache_key = (
            f"citations:v2:{paper_id.casefold()}:{bounded_offset}:{bounded_limit}"
        )
        cached = self._read_cache(cache_key)
        if cached is not None:
            self.last_error = None
            return cached

        async with self._cache_lock(cache_key):
            cached = self._read_cache(cache_key)
            if cached is not None:
                self.last_error = None
                return cached
            try:
                response = await self._request_with_retry(
                    "GET",
                    f"/paper/{paper_id}/citations",
                    params={
                        "limit": bounded_limit,
                        "offset": bounded_offset,
                        "fields": _SEARCH_FIELDS,
                    },
                )
                papers = [
                    paper
                    for item in (response.json().get("data") or [])
                    if isinstance(item, dict)
                    and isinstance(item.get("citingPaper"), dict)
                    and (paper := self._parse_paper(item["citingPaper"]))
                ]
                self._write_cache(cache_key, papers, _S2_CACHE_TTL_SECONDS)
                return papers
            except Exception as e:
                if not self.last_error:
                    self.last_error = str(e)
                logger.error(f"[semantic_scholar] 获取引用网络失败: {e}")
                return []

    async def get_references(
        self,
        paper_id: str,
        limit: int = 1000,
    ) -> List[Paper]:
        """Fetch papers referenced by one candidate paper."""
        bounded_limit = min(max(1, limit), 1000)
        cache_key = f"references:{paper_id.casefold()}:{bounded_limit}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            self.last_error = None
            return cached
        async with self._cache_lock(cache_key):
            cached = self._read_cache(cache_key)
            if cached is not None:
                self.last_error = None
                return cached
            try:
                response = await self._request_with_retry(
                    "GET",
                    f"/paper/{paper_id}/references",
                    params={"limit": bounded_limit, "fields": _SEARCH_FIELDS},
                )
                papers = [
                    paper
                    for item in (response.json().get("data") or [])
                    if isinstance(item, dict)
                    and isinstance(item.get("citedPaper"), dict)
                    and (paper := self._parse_paper(item["citedPaper"]))
                ]
                self._write_cache(cache_key, papers, _S2_CACHE_TTL_SECONDS)
                return papers
            except Exception as e:
                if not self.last_error:
                    self.last_error = str(e)
                logger.error(f"[semantic_scholar] 获取参考文献失败: {e}")
                return []

    async def find_author_id(self, name: str) -> Optional[str]:
        """Resolve a Semantic Scholar author id, preferring an exact name match."""
        try:
            response = await self._request_with_retry(
                "GET",
                "/author/search",
                params={
                    "query": name,
                    "limit": 10,
                    "fields": "name,paperCount,citationCount",
                },
            )
            authors = response.json().get("data", [])
            if not authors:
                return None
            query_parts = re.findall(r"[a-z]+", name.casefold())
            query_first = query_parts[0][0] if query_parts else ""
            query_last = query_parts[-1] if query_parts else ""
            compatible = [
                author
                for author in authors
                if (
                    (parts := re.findall(
                        r"[a-z]+",
                        (author.get("name") or "").casefold(),
                    ))
                    and parts[-1] == query_last
                    and parts[0][0] == query_first
                )
            ]
            selected = max(
                compatible or authors,
                key=lambda author: (
                    int(author.get("citationCount") or 0),
                    int(author.get("paperCount") or 0),
                ),
            )
            author_id = selected.get("authorId")
            return str(author_id) if author_id else None
        except Exception as e:
            if not self.last_error:
                self.last_error = str(e)
            logger.error(f"[semantic_scholar] 作者解析失败: {e}")
            return None

    async def get_author_papers(
        self,
        author_id: str,
        limit: int = 1000,
    ) -> List[Paper]:
        """Fetch an author's papers once and cache the normalized metadata."""
        bounded_limit = min(max(1, limit), 1000)
        cache_key = f"author-papers:{author_id}:{bounded_limit}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            self.last_error = None
            return cached

        async with self._cache_lock(cache_key):
            cached = self._read_cache(cache_key)
            if cached is not None:
                self.last_error = None
                return cached
            try:
                response = await self._request_with_retry(
                    "GET",
                    f"/author/{author_id}/papers",
                    params={
                        "limit": bounded_limit,
                        "fields": _SEARCH_FIELDS,
                    },
                )
                papers = [
                    paper
                    for item in response.json().get("data", [])
                    if isinstance(item, dict)
                    and (paper := self._parse_paper(item))
                ]
                self._write_cache(cache_key, papers, _S2_CACHE_TTL_SECONDS)
                return papers
            except Exception as e:
                if not self.last_error:
                    self.last_error = str(e)
                logger.error(f"[semantic_scholar] 获取作者论文失败: {e}")
                return []

    async def get_author_paper_records(
        self,
        author_id: str,
        limit: int = 1000,
    ) -> List[Dict]:
        """Fetch author papers with graph metadata needed by structured queries."""
        bounded_limit = min(max(1, limit), 1000)
        try:
            response = await self._request_with_retry(
                "GET",
                f"/author/{author_id}/papers",
                params={
                    "limit": bounded_limit,
                    "fields": (
                        f"{_SEARCH_FIELDS},publicationTypes,journal,references"
                    ),
                },
            )
            return [
                item
                for item in response.json().get("data", [])
                if isinstance(item, dict) and item.get("paperId")
            ]
        except Exception as e:
            if not self.last_error:
                self.last_error = str(e)
            logger.error(f"[semantic_scholar] 获取作者图谱记录失败: {e}")
            return []

    async def search_bulk_records(
        self,
        *,
        query: Optional[str] = None,
        venue: Optional[str] = None,
        publication_range: Optional[str] = None,
        min_citations: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """Use S2 bulk filters to obtain a bounded candidate set."""
        params: Dict[str, object] = {
            "fields": _SEARCH_FIELDS,
        }
        if query:
            params["query"] = query
        if venue:
            params["venue"] = venue
        if publication_range:
            params["publicationDateOrYear"] = publication_range
        if min_citations is not None:
            params["minCitationCount"] = max(0, min_citations)
        try:
            response = await self._request_with_retry(
                "GET",
                "/paper/search/bulk",
                params=params,
            )
            return [
                item
                for item in response.json().get("data", [])[: max(1, limit)]
                if isinstance(item, dict) and item.get("paperId")
            ]
        except Exception as e:
            if not self.last_error:
                self.last_error = str(e)
            logger.error(f"[semantic_scholar] 批量候选检索失败: {e}")
            return []

    async def get_paper_records_batch(
        self,
        paper_ids: Sequence[str],
        *,
        include_references: bool = False,
    ) -> List[Dict]:
        """Fetch raw records for up to 500 candidate papers per request."""
        unique_ids = list(dict.fromkeys(str(value) for value in paper_ids if value))
        records: List[Dict] = []
        fields = _SEARCH_FIELDS
        batch_size = 500
        if include_references:
            fields = (
                "corpusId,title,authors,year,venue,citationCount,externalIds,"
                "openAccessPdf,references.paperId,references.corpusId,"
                "references.venue"
            )
            # Keep nested graph payloads below the endpoint's 10 MB response cap.
            batch_size = 100
        try:
            for start in range(0, len(unique_ids), batch_size):
                response = await self._request_with_retry(
                    "POST",
                    "/paper/batch",
                    params={"fields": fields},
                    json={"ids": unique_ids[start : start + batch_size]},
                )
                records.extend(
                    item
                    for item in response.json()
                    if isinstance(item, dict) and item.get("paperId")
                )
            return records
        except Exception as e:
            if not self.last_error:
                self.last_error = str(e)
            logger.error(f"[semantic_scholar] 批量图谱详情失败: {e}")
            return records

    async def enrich_with_corpus_ids(self, papers: Sequence[Paper]) -> List[Paper]:
        """Backfill CorpusId for DOI/arXiv papers with a single batch call."""
        identifiers = [
            identifier
            for paper in papers
            if not paper.corpus_id
            and (identifier := self.identifier_for_paper(paper))
        ]
        if not identifiers:
            return list(papers)
        resolved = await self.get_papers_batch(identifiers)
        by_doi = {
            paper.doi.casefold(): paper
            for paper in resolved
            if paper.doi and paper.corpus_id
        }
        by_title = {
            re.sub(r"\W+", " ", paper.title.casefold()).strip(): paper
            for paper in resolved
            if paper.corpus_id and paper.title
        }
        enriched: List[Paper] = []
        for paper in papers:
            match = by_doi.get(paper.doi.casefold()) if paper.doi else None
            if not match and paper.title:
                normalized_title = re.sub(
                    r"\W+",
                    " ",
                    paper.title.casefold(),
                ).strip()
                match = by_title.get(normalized_title)
            if match and not paper.corpus_id:
                enriched.append(
                    paper.model_copy(
                        update={
                            "corpus_id": match.corpus_id,
                            "citation_count": max(
                                paper.citation_count,
                                match.citation_count,
                            ),
                        }
                    )
                )
            else:
                enriched.append(paper)
        return enriched

    @staticmethod
    def identifier_for_paper(paper: Paper) -> Optional[str]:
        """Return an identifier accepted by S2 exact and batch endpoints."""
        if paper.doi:
            return f"DOI:{paper.doi}"
        arxiv_match = re.search(
            r"arxiv\.org/(?:abs|pdf)/([^/?#]+)",
            paper.url or paper.pdf_url or "",
            flags=re.IGNORECASE,
        )
        if arxiv_match:
            arxiv_id = re.sub(r"v\d+$", "", arxiv_match.group(1))
            return f"ARXIV:{arxiv_id}"
        s2_match = re.search(
            r"semanticscholar\.org/paper/([a-f0-9]{40})",
            paper.url or "",
            flags=re.IGNORECASE,
        )
        if s2_match:
            return s2_match.group(1)
        return None

    # ------------------------------------------------------------------
    # PDF URL
    # ------------------------------------------------------------------

    async def get_pdf_url(self, paper_id: str) -> Optional[str]:
        """
        获取论文 PDF 链接

        Args:
            paper_id: S2 paperId 或 DOI

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
        健康检查：尝试获取一篇已知论文

        Returns:
            {"status": "ok", ...} 或 {"status": "error", "message": "..."}
        """
        try:
            response = await self._request_with_retry(
                "GET",
                "/paper/search",
                params={"query": "test", "limit": 1, "fields": "title"},
            )
            data = response.json()
            count = data.get("total", 0)
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
            paper_id = data.get("paperId", "")
            if not paper_id:
                return None

            # 作者列表
            authors = [
                a["name"]
                for a in (data.get("authors") or [])
                if a and a.get("name")
            ]

            # 外部 ID
            external_ids = data.get("externalIds") or {}
            doi = external_ids.get("DOI")

            # PDF
            open_access_pdf = data.get("openAccessPdf")
            pdf_url = open_access_pdf.get("url") if open_access_pdf else None

            # 研究领域
            fields_of_study = data.get("fieldsOfStudy") or []
            publication_date = None
            if raw_publication_date := data.get("publicationDate"):
                try:
                    publication_date = date.fromisoformat(str(raw_publication_date))
                except ValueError:
                    logger.debug(
                        "[semantic_scholar] Invalid publicationDate: %s",
                        raw_publication_date,
                    )

            return Paper(
                id=make_paper_uuid("semantic_scholar", paper_id),
                title=data.get("title", ""),
                authors=authors,
                abstract=data.get("abstract"),
                year=data.get("year"),
                publication_date=publication_date,
                venue=data.get("venue"),
                citation_count=data.get("citationCount", 0),
                doi=doi,
                url=f"https://www.semanticscholar.org/paper/{paper_id}",
                pdf_url=pdf_url,
                source="semantic_scholar",
                corpus_id=(
                    str(data.get("corpusId"))
                    if data.get("corpusId") is not None
                    else None
                ),
                is_open_access=pdf_url is not None,
            )

        except Exception as e:
            logger.error(f"[semantic_scholar] 解析论文数据失败: {e}")
            return None

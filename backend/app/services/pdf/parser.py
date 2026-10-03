"""
PDF 解析器

使用 PyMuPDF 解析 PDF 文件，提取结构化文档对象。
支持标题层级识别、参考文献提取、表格和图片描述提取。
"""

import asyncio
import logging
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
# PyMuPDF does not support multiple threads accessing MuPDF. Every application
# entry point must use this one dedicated thread, including validation/rendering.
# https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html
# This isolates blocking work; it is not parallel PDF processing.
_PDF_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scholarnova-pdf")
_PDF_CAPACITY = threading.BoundedSemaphore(1)


class PDFBusyError(RuntimeError):
    """The sole PDF worker is occupied; no unbounded work queue is accepted."""


def _submit_pdf_work(function: Callable[..., _T], *args) -> Future[_T]:
    if not _PDF_CAPACITY.acquire(blocking=False):
        raise PDFBusyError("PDF 解析服务正忙，请稍后重试")
    try:
        future = _PDF_EXECUTOR.submit(function, *args)
    except BaseException:
        _PDF_CAPACITY.release()
        raise
    # A cancelled asyncio waiter cannot terminate running native PDF work.
    # Release only when the concurrent Future actually finishes.
    future.add_done_callback(lambda _future: _PDF_CAPACITY.release())
    return future


async def _await_pdf_work(future: Future[_T]) -> _T:
    wrapped = asyncio.wrap_future(future)
    # Consume abandoned exceptions without swallowing errors for active waiters.
    wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    return await asyncio.shield(wrapped)


async def run_pdf_work(function: Callable[..., _T], *args) -> _T:
    """Run on the sole PDF thread; cancellation stops waiting, not native work."""
    return await _await_pdf_work(_submit_pdf_work(function, *args))


def run_pdf_work_sync(function: Callable[..., _T], *args) -> _T:
    """Compatibility for synchronous callers; async callers use run_pdf_work."""
    return _submit_pdf_work(function, *args).result()

# 常见学术论文章节标题关键词（不区分大小写匹配）
_SECTION_PATTERNS: list[tuple[str, int]] = [
    # 一级标题
    ("abstract", 1),
    ("introduction", 1),
    ("related work", 1),
    ("background", 1),
    ("preliminar", 1),
    ("methodology", 1),
    ("methods", 1),
    ("method", 1),
    ("approach", 1),
    ("proposed method", 1),
    ("experiments", 1),
    ("experimental setup", 1),
    ("experimental results", 1),
    ("results", 1),
    ("evaluation", 1),
    ("discussion", 1),
    ("analysis", 1),
    ("conclusion", 1),
    ("conclusions", 1),
    ("future work", 1),
    ("acknowledgment", 1),
    ("acknowledgement", 1),
    ("references", 1),
    ("bibliography", 1),
    # 二级标题
    ("problem formulation", 2),
    ("problem definition", 2),
    ("dataset", 2),
    ("datasets", 2),
    ("implementation details", 2),
    ("training details", 2),
    ("hyperparameters", 2),
    ("ablation study", 2),
    ("ablation", 2),
    ("case study", 2),
    ("qualitative analysis", 2),
    ("quantitative analysis", 2),
    ("limitations", 2),
]


@dataclass
class DocumentSection:
    """文档章节"""

    heading: str
    level: int  # 1=一级标题, 2=二级标题
    text: str
    paragraph_index: int  # 在文档中的段落位置
    page_start: Optional[int] = None
    page_end: Optional[int] = None


@dataclass
class ParsedDocument:
    """结构化文档对象"""

    title: str
    abstract: str
    sections: list[DocumentSection] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    figures: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    full_text: str = ""


class PDFParser:
    """
    PDF 解析器

    使用 PyMuPDF 解析 PDF 文件，提取结构化文档对象。
    """

    def __init__(self, *, extract_tables: bool = False):
        """Keep online parsing text-first; native table detection is opt-in.

        find_tables() can take tens of seconds on a single graphics-heavy page
        and cannot be interrupted safely by cancelling its asyncio waiter.
        Explicit offline extraction still uses the same sole PDF worker.
        """
        self._include_tables = extract_tables

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def parse(self, pdf_path: Union[str, Path]) -> Optional[ParsedDocument]:
        """
        解析本地 PDF 文件，返回结构化文档对象。

        Args:
            pdf_path: PDF 文件路径

        Returns:
            ParsedDocument 或 None（解析失败时）

        Raises:
            PDFBusyError: 解析线程正忙，未提交本次工作。
        """
        return await run_pdf_work(self._parse_sync, Path(pdf_path))

    def _parse_sync(self, pdf_path: Path) -> Optional[ParsedDocument]:
        """Open, extract and close in the PDF worker; parser instances are stateless."""
        try:
            import pymupdf

            pdf_path = Path(pdf_path)
            if not pdf_path.exists():
                logger.error(f"PDF file not found: {pdf_path}")
                return None

            doc = pymupdf.open(str(pdf_path))
            try:
                result = self._extract_document(doc)
            finally:
                doc.close()

            logger.info(
                f"Parsed PDF: title={result.title!r}, "
                f"sections={len(result.sections)}, refs={len(result.references)}"
            )
            return result

        except ImportError:
            logger.error("PyMuPDF not installed. Install with: pip install pymupdf")
            return None
        except Exception as e:
            logger.error(f"Error parsing PDF: {e}")
            return None

    async def parse_from_url(self, url: str) -> Optional[ParsedDocument]:
        """
        从 URL 下载并解析 PDF。

        Args:
            url: PDF URL

        Returns:
            ParsedDocument 或 None
        """
        import tempfile

        import httpx

        tmp_path: Optional[Path] = None
        worker_owns_file = False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url, follow_redirects=True, timeout=60
                )
                response.raise_for_status()

                # 保存到临时文件
                with tempfile.NamedTemporaryFile(
                    suffix=".pdf", delete=False
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(response.content)

                # Transfer cleanup to the worker before awaiting. Cancellation
                # must not unlink a PDF while MuPDF still has it open on Windows.
                future = _submit_pdf_work(self._parse_temporary, tmp_path)
                worker_owns_file = True
                return await _await_pdf_work(future)

        except PDFBusyError:
            raise
        except Exception as e:
            logger.error(f"Error downloading PDF from {url}: {e}")
            return None
        finally:
            if tmp_path is not None and not worker_owns_file:
                tmp_path.unlink(missing_ok=True)

    def _parse_temporary(self, pdf_path: Path) -> Optional[ParsedDocument]:
        try:
            return self._parse_sync(pdf_path)
        finally:
            pdf_path.unlink(missing_ok=True)

    async def extract_text(self, pdf_path: Union[str, Path]) -> Optional[str]:
        """
        提取 PDF 纯文本（兼容旧接口）。

        Args:
            pdf_path: PDF 文件路径

        Returns:
            提取的文本内容，失败返回 None
        """
        doc = await self.parse(pdf_path)
        return doc.full_text if doc else None

    def extract_sections(self, text: str) -> dict:
        """
        提取论文章节（兼容旧接口）。

        Args:
            text: 论文全文

        Returns:
            章节字典
        """
        sections_map: dict[str, str] = {
            "abstract": "",
            "introduction": "",
            "methods": "",
            "results": "",
            "discussion": "",
            "conclusion": "",
            "references": "",
        }

        current_section: Optional[str] = None
        for line in text.split("\n"):
            line_lower = line.strip().lower()
            if "abstract" in line_lower and len(line.strip()) < 30:
                current_section = "abstract"
            elif "introduction" in line_lower and len(line.strip()) < 30:
                current_section = "introduction"
            elif (
                any(
                    kw in line_lower
                    for kw in ["method", "methodology", "experiment"]
                )
                and len(line.strip()) < 30
            ):
                current_section = "methods"
            elif "result" in line_lower and len(line.strip()) < 30:
                current_section = "results"
            elif "discussion" in line_lower and len(line.strip()) < 30:
                current_section = "discussion"
            elif "conclusion" in line_lower and len(line.strip()) < 30:
                current_section = "conclusion"
            elif "reference" in line_lower and len(line.strip()) < 30:
                current_section = "references"
            elif current_section:
                sections_map[current_section] += line + "\n"

        return sections_map

    # ------------------------------------------------------------------
    # 内部方法：文档级提取
    # ------------------------------------------------------------------

    def _extract_document(self, doc) -> ParsedDocument:
        """从已打开的 PyMuPDF 文档中提取结构化数据。"""
        # 逐页提取文本和元数据
        page_texts: list[str] = []
        all_text_parts: list[str] = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            text = page.get_text() or ""
            page_texts.append(text)
            if text:
                all_text_parts.append(f"[Page {page_num + 1}]\n{text}")

        full_text = "\n\n".join(all_text_parts)
        combined_text = "\n".join(text for text in page_texts if text)

        # 提取元数据
        metadata = self._extract_metadata(doc)

        # 提取标题
        title = self._extract_title(doc, metadata)

        # 提取摘要
        abstract = self._extract_abstract(combined_text)

        # 提取章节
        sections = self._extract_structured_sections(full_text)

        # 提取参考文献
        references = self._extract_references(combined_text)

        # 提取图片描述
        figures = self._extract_figure_captions_from_pages(page_texts)

        # Optional structure recovery must not delay usable online text/figures.
        # The page text above retains extractable table text, but not reliable
        # row/column relationships. Do not describe this as full table reading.
        warnings: list[str] = []
        tables = self._extract_tables(doc, warnings) if self._include_tables else []
        if not self._include_tables:
            warnings.append("已保留页文本中可提取的表格文字，未执行结构化表格识别；行列对应关系需结合原页核对")
        if warnings:
            metadata["parse_warnings"] = warnings

        return ParsedDocument(
            title=title,
            abstract=abstract,
            sections=sections,
            references=references,
            tables=tables,
            figures=figures,
            metadata=metadata,
            full_text=full_text,
        )

    # ------------------------------------------------------------------
    # 内部方法：元数据
    # ------------------------------------------------------------------

    def _extract_metadata(self, doc) -> dict:
        """从 PDF 元数据中提取作者、DOI 等信息。"""
        meta = doc.metadata or {}
        result: dict = {
            "author": meta.get("author", ""),
            "subject": meta.get("subject", ""),
            "keywords": meta.get("keywords", ""),
            "creator": meta.get("creator", ""),
            "producer": meta.get("producer", ""),
            "creation_date": meta.get("creationDate", ""),
        }

        # 尝试从全文中提取 DOI
        full_text_sample = ""
        for i in range(min(3, doc.page_count)):
            full_text_sample += doc[i].get_text()

        doi_match = re.search(
            r"(?:doi[:\s]*|https?://doi\.org/)(10\.\d{4,}/\S+)",
            full_text_sample,
            re.IGNORECASE,
        )
        if doi_match:
            result["doi"] = doi_match.group(1).rstrip(".,;")

        # 尝试提取年份
        year_match = re.search(
            r"(?:©|Copyright|\(c\)|Published in)\s*(?:\d{4}\s*.*?|.*?)(20[0-2]\d)",
            full_text_sample,
            re.IGNORECASE,
        )
        if not year_match:
            year_match = re.search(r"\b(20[0-2]\d)\b", full_text_sample[:2000])
        if year_match:
            result["year"] = int(year_match.group(1))

        return result

    # ------------------------------------------------------------------
    # 内部方法：标题
    # ------------------------------------------------------------------

    def _extract_title(self, doc, metadata: dict) -> str:
        """
        提取论文标题。
        优先使用 PDF 元数据中的标题，否则从首页文本中推断。
        """
        # 优先从元数据获取
        meta_title = (metadata.get("author") or "").strip()
        # PyMuPDF 的 metadata title 字段
        raw_meta_title = (doc.metadata or {}).get("title", "").strip()
        if raw_meta_title and len(raw_meta_title) > 5:
            return raw_meta_title

        # 从第一页文本中提取前几行，取最长的非全大写行为标题
        if doc.page_count == 0:
            return ""
        first_page_text = doc[0].get_text()
        lines = [
            ln.strip()
            for ln in first_page_text.split("\n")
            if ln.strip() and len(ln.strip()) > 3
        ]

        # 过滤掉常见噪音行
        noise_keywords = [
            "abstract",
            "introduction",
            "arxiv",
            "preprint",
            "under review",
            "accepted",
            "published",
            "conference",
            "journal",
            "doi",
            "http",
            "www.",
            "©",
            "copyright",
        ]
        candidate_lines = []
        for line in lines[:15]:
            line_lower = line.lower()
            if any(nk in line_lower for nk in noise_keywords):
                continue
            if line.isupper() and len(line) < 10:
                continue
            candidate_lines.append(line)

        if candidate_lines:
            # 选择最长的候选行作为标题
            return max(candidate_lines, key=len)

        return lines[0] if lines else ""

    # ------------------------------------------------------------------
    # 内部方法：摘要
    # ------------------------------------------------------------------

    def _extract_abstract(self, text: str) -> str:
        """从全文中提取摘要部分。"""
        # 匹配 "Abstract" 标题到下一个章节标题之间的文本
        abstract_pattern = re.compile(
            r"(?:^|\n)\s*(?:abstract|ABSTRACT)\s*[:\-.\s]*\n?(.*?)(?=\n\s*(?:\d+[\.\s]|introduction|INTRODUCTION|keywords|KEYWORDS|1\s+Introduction))",
            re.DOTALL | re.IGNORECASE,
        )
        match = abstract_pattern.search(text)
        if match:
            abstract = match.group(1).strip()
            # 清理多余空白
            abstract = re.sub(r"\s+", " ", abstract)
            if len(abstract) > 50:
                return abstract

        # 备选：取前 2000 字符中 "Abstract" 后的第一段
        first_chunk = text[:3000]
        idx = first_chunk.lower().find("abstract")
        if idx != -1:
            after = first_chunk[idx + 8 :].strip()
            # 取到下一个空行或章节标题
            para_lines = []
            for line in after.split("\n"):
                stripped = line.strip()
                if not stripped:
                    if para_lines:
                        break
                    continue
                if re.match(r"^\d+[\.\s]", stripped) and len(stripped) < 60:
                    break
                if stripped.lower().startswith("introduction"):
                    break
                para_lines.append(stripped)
            if para_lines:
                return re.sub(r"\s+", " ", " ".join(para_lines))

        return ""

    # ------------------------------------------------------------------
    # 内部方法：结构化章节提取
    # ------------------------------------------------------------------

    def _extract_structured_sections(self, text: str) -> list[DocumentSection]:
        """
        提取结构化章节列表。
        使用字体大小启发式和关键词匹配两种策略。
        """
        # 策略1：基于关键词的章节分割
        sections = self._split_sections_by_keywords(text)
        if len(sections) >= 3:
            return sections

        # 策略2：基于行格式的章节分割（全大写行、数字编号行等）
        return self._split_sections_by_formatting(text)

    def _split_sections_by_keywords(self, text: str) -> list[DocumentSection]:
        """基于关键词匹配分割章节。"""
        lines = text.split("\n")
        sections: list[DocumentSection] = []
        current_heading = "Preamble"
        current_level = 1
        current_lines: list[str] = []
        para_idx = 0
        current_page: Optional[int] = None
        page_start: Optional[int] = None
        page_end: Optional[int] = None

        for line in lines:
            stripped = line.strip()
            page_match = re.fullmatch(r"\[Page\s+(\d+)\]", stripped, re.IGNORECASE)
            if page_match:
                current_page = int(page_match.group(1))
                continue
            if not stripped:
                current_lines.append("")
                continue

            detected = self._detect_section_heading(stripped)
            if detected:
                # 保存前一个章节
                section_text = "\n".join(current_lines).strip()
                if section_text:
                    sections.append(
                        DocumentSection(
                            heading=current_heading,
                            level=current_level,
                            text=section_text,
                            paragraph_index=para_idx,
                            page_start=page_start,
                            page_end=page_end,
                        )
                    )
                    para_idx += 1

                current_heading, current_level = detected
                current_lines = []
                page_start = current_page
                page_end = current_page
            else:
                current_lines.append(stripped)
                if page_start is None:
                    page_start = current_page
                if current_page is not None:
                    page_end = current_page

        # 最后一个章节
        section_text = "\n".join(current_lines).strip()
        if section_text:
            sections.append(
                DocumentSection(
                    heading=current_heading,
                    level=current_level,
                    text=section_text,
                    paragraph_index=para_idx,
                    page_start=page_start,
                    page_end=page_end,
                )
            )

        return sections

    def _split_sections_by_formatting(self, text: str) -> list[DocumentSection]:
        """基于行格式（全大写、编号标题等）分割章节。"""
        lines = text.split("\n")
        sections: list[DocumentSection] = []
        current_heading = "Preamble"
        current_level = 1
        current_lines: list[str] = []
        para_idx = 0
        current_page: Optional[int] = None
        page_start: Optional[int] = None
        page_end: Optional[int] = None

        # 匹配格式化标题的正则
        # 全大写标题行（如 "INTRODUCTION"）
        # 数字编号标题（如 "1. Introduction"、"2.1 Dataset"）
        heading_pattern = re.compile(
            r"^(?:"
            r"([A-Z][A-Z\s\-]{3,})"  # 全大写
            r"|(?:\d+\.?\s+)([A-Z][a-zA-Z\s\-]+)"  # "1. Title"
            r"|(?:\d+\.\d+\.?\s+)([A-Z][a-zA-Z\s\-]+)"  # "2.1 Title"
            r")$"
        )

        for line in lines:
            stripped = line.strip()
            page_match = re.fullmatch(r"\[Page\s+(\d+)\]", stripped, re.IGNORECASE)
            if page_match:
                current_page = int(page_match.group(1))
                continue
            if not stripped:
                current_lines.append("")
                continue

            match = heading_pattern.match(stripped)
            if match:
                # 保存前一个章节
                section_text = "\n".join(current_lines).strip()
                if section_text:
                    sections.append(
                        DocumentSection(
                            heading=current_heading,
                            level=current_level,
                            text=section_text,
                            paragraph_index=para_idx,
                            page_start=page_start,
                            page_end=page_end,
                        )
                    )
                    para_idx += 1

                heading = match.group(1) or match.group(2) or match.group(3)
                current_heading = heading.strip().title()
                # 如果是数字.数字格式则为二级标题
                current_level = (
                    2 if match.group(3) or (match.group(2) and "." in stripped[:4]) else 1
                )
                current_lines = []
                page_start = current_page
                page_end = current_page
            else:
                current_lines.append(stripped)
                if page_start is None:
                    page_start = current_page
                if current_page is not None:
                    page_end = current_page

        # 最后一个章节
        section_text = "\n".join(current_lines).strip()
        if section_text:
            sections.append(
                DocumentSection(
                    heading=current_heading,
                    level=current_level,
                    text=section_text,
                    paragraph_index=para_idx,
                    page_start=page_start,
                    page_end=page_end,
                )
            )

        return sections

    def _detect_section_heading(self, line: str) -> Optional[tuple[str, int]]:
        """
        检测一行是否为章节标题。
        返回 (heading, level) 或 None。
        """
        line_clean = line.strip().rstrip(":.")
        line_lower = line_clean.lower()

        # 检查数字编号前缀（如 "1 Introduction"、"2.1 Dataset"）
        numbered_match = re.match(
            r"^(\d+(?:\.\d+)?)\.?\s+(.+)$", line_clean
        )
        if numbered_match:
            num_part = numbered_match.group(1)
            title_part = numbered_match.group(2).strip()
            level = 2 if "." in num_part else 1

            # 验证标题部分是否匹配已知模式
            title_lower = title_part.lower()
            for pattern, pattern_level in _SECTION_PATTERNS:
                if pattern in title_lower:
                    return title_part.title(), max(level, pattern_level)

            # 即使不匹配已知模式，短行也可能是标题
            if len(title_part) < 60 and not title_part[0].islower():
                return title_part.title(), level

        # 检查已知关键词（无编号前缀）
        for pattern, level in _SECTION_PATTERNS:
            if line_lower == pattern or line_lower == pattern + ":":
                return line_clean.title(), level

        # 检查全大写短行
        if (
            line_clean.isupper()
            and 3 < len(line_clean) < 60
            and not re.match(r"^\d+$", line_clean)
        ):
            return line_clean.title(), 1

        return None

    # ------------------------------------------------------------------
    # 内部方法：参考文献
    # ------------------------------------------------------------------

    def _extract_references(self, text: str) -> list[str]:
        """
        提取参考文献列表。
        匹配 [1]、[2] 等编号格式，或作者-年份格式。
        """
        # 定位参考文献区域
        ref_start = None
        text_lower = text.lower()
        for marker in ["references", "bibliography", "参考文献"]:
            idx = text_lower.rfind(marker)
            if idx != -1:
                ref_start = idx
                break

        if ref_start is None:
            return []

        ref_text = text[ref_start:]

        # 策略1：匹配 [N] 格式
        bracket_refs = re.findall(
            r"\[(\d+)\]\s*(.+?)(?=\n\[\d+\]|\Z)", ref_text, re.DOTALL
        )
        if bracket_refs and len(bracket_refs) >= 3:
            refs = []
            for _num, content in bracket_refs:
                cleaned = re.sub(r"\s+", " ", content).strip()
                if cleaned:
                    refs.append(cleaned)
            return refs

        # 策略2：匹配作者-年份格式（如 "Smith et al. (2020)"）
        author_year_refs = re.findall(
            r"\n([A-Z][a-z]+(?:\s+(?:et\s+al|and\s+[A-Z][a-z]+))?(?:,|\s)\s*\(\d{4}\).+?)(?=\n[A-Z][a-z]+(?:\s+(?:et\s+al|and\s+[A-Z][a-z]+))?(?:,|\s)\s*\(\d{4}\)|\Z)",
            ref_text,
            re.DOTALL,
        )
        if author_year_refs and len(author_year_refs) >= 3:
            return [re.sub(r"\s+", " ", r).strip() for r in author_year_refs]

        # 策略3：按空行分割参考文献区域
        paragraphs = re.split(r"\n\s*\n", ref_text)
        refs = []
        for para in paragraphs[1:]:  # 跳过 "References" 标题行
            cleaned = re.sub(r"\s+", " ", para).strip()
            if cleaned and len(cleaned) > 20:
                refs.append(cleaned)
        return refs

    # ------------------------------------------------------------------
    # 内部方法：表格
    # ------------------------------------------------------------------

    def _extract_tables(self, doc, warnings: Optional[list[str]] = None) -> list[dict]:
        """
        提取 PDF 中的表格。
        使用 PyMuPDF 的 find_tables() 功能。
        """
        tables: list[dict] = []
        failed_pages: list[int] = []
        try:
            for page_num in range(doc.page_count):
                page = doc[page_num]
                try:
                    tab_finder = page.find_tables()
                    for i, table in enumerate(tab_finder.tables):
                        # 提取表格数据
                        rows = []
                        for row in table.extract():
                            cells = [
                                re.sub(r"\s+", " ", str(cell or "")).strip()
                                for cell in row
                            ]
                            if any(cells):
                                rows.append(cells)

                        if rows:
                            # 尝试从页面文本中找到表格标题
                            caption = self._find_table_caption(page, i)
                            tables.append(
                                {
                                    "page": page_num + 1,
                                    "caption": caption,
                                    "rows": rows,
                                    "num_rows": len(rows),
                                    "num_cols": len(rows[0]) if rows else 0,
                                }
                            )
                except Exception:
                    failed_pages.append(page_num + 1)
                    continue
        except Exception as e:
            logger.warning(f"Table extraction failed: {e}")
            if warnings is not None:
                warnings.append("结构化表格提取未完成；已保留可提取的页文本")
        if failed_pages and warnings is not None:
            pages = "、".join(str(page) for page in failed_pages[:10])
            warnings.append(
                f"第 {pages} 页等共 {len(failed_pages)} 页结构化表格提取失败；已保留可提取的页文本"
            )

        return tables

    def _find_table_caption(self, page, table_index: int) -> str:
        """尝试从页面文本中找到表格标题。"""
        text = page.get_text()
        # 匹配 "Table 1:" 或 "Table I." 等格式
        captions = re.findall(
            r"(Table\s+\d+[.:]\s*[^\n]+)", text, re.IGNORECASE
        )
        if table_index < len(captions):
            return captions[table_index].strip()
        return ""

    # ------------------------------------------------------------------
    # 内部方法：图片描述
    # ------------------------------------------------------------------

    def _extract_figure_captions(self, text: str) -> list[dict]:
        """
        提取图片标题/描述。
        匹配 "Figure 1:"、"Fig. 1." 等格式。
        """
        figures: list[dict] = []
        # 匹配 Figure/Fig 标题，捕获标题行和后续描述行
        pattern = re.compile(
            r"((?:Figure|Fig\.?)\s+\d+[.:]\s*[^\n]+(?:\n(?![A-Z][a-z]+\s+\d+[.:])[^\n]+)*)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            caption = re.sub(r"\s+", " ", match.group(1)).strip()
            fig_num_match = re.match(
                r"(?:Figure|Fig\.?)\s+(\d+)", caption, re.IGNORECASE
            )
            figures.append(
                {
                    "figure_number": (
                        int(fig_num_match.group(1)) if fig_num_match else None
                    ),
                    "caption": caption,
                }
            )
        return figures

    def _extract_figure_captions_from_pages(
        self,
        page_texts: list[str],
    ) -> list[dict]:
        """Extract captions per page so citations retain a verifiable locator."""
        figures: list[dict] = []
        for page_index, text in enumerate(page_texts):
            for figure in self._extract_figure_captions(text):
                figures.append({**figure, "page": page_index + 1})
        return figures

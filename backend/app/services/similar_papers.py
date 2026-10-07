"""Bounded external retrieval and explainable metadata-only seed recommendations."""

from __future__ import annotations

import asyncio
import re
from collections import Counter

from app.config import settings
from app.schemas.paper import Paper
from app.schemas.query import DataSource, SubQuery
from app.schemas.similar_papers import (
    SimilarMode, SimilarPaperItem, SimilarPapersResponse, SimilarSourceStatus,
    SimilarStatistics, TermFrequency,
)
from app.services.retrieval.bm25 import tokenize
from app.services.paper_persistence import normalize_doi
from app.services.search.query_planner import QueryPlanner
from app.services.search.retriever import Retriever
from app.services.sources.crossref import CrossRefSource
from app.services.sources.openalex import OpenAlexSource


SOURCE_TIMEOUT = 12
CANDIDATES_PER_SOURCE = 30
_GENERIC = set("a an to of in on by is as at be we our it its are was were has been have study studies approach approaches application applications using use used based model models analysis results result method methods proposed propose novel new evaluation evaluate performance show shows demonstrate demonstrates conclusion conclusions objective objectives background introduction discussion large language languages llm llms".split())
_GENERIC.update("data deep learning system systems framework frameworks intelligent intelligence artificial neural network networks computer computational technology technologies technical development big digital information comprehensive proposed based 数据 深度 学习 系统 应用 开发 模型 技术 人工 智能 结果 提出 方法 分析".split())
_BROAD_METHODS = set("prediction predicting predictions forecasting detection classification optimization algorithm algorithms recognition processing generation summarization reasoning repair repairing imputation restoration completion safety quality efficiency accuracy benchmark benchmarks comparison experiment experiments validation 数据 预测 分类 检测 优化 修复 插补 填补 补全 恢复 安全 效率 精度 质量 对比 实验".split())
_FAMILY = re.compile(r"(?<![a-z0-9])(?:deepseek[a-z0-9._-]*|qwen[a-z0-9._-]*|chatgpt|gpt(?:[- .]?\d[a-z0-9.-]*)?|llms?|large language models?)(?![a-z0-9])|大语言模型|大型语言模型|通义千问|千问", re.I)
_BRANDS = {
    "DeepSeek": re.compile(r"(?<![a-z0-9])deepseek[a-z0-9._-]*(?![a-z0-9])", re.I),
    "Qwen": re.compile(r"(?<![a-z0-9])qwen[a-z0-9._-]*(?![a-z0-9])|通义千问|千问", re.I),
    "GPT": re.compile(r"(?<![a-z0-9])(?:chatgpt|gpt(?:[- .]?\d[a-z0-9.-]*)?)(?![a-z0-9])", re.I),
}
_QUERY_GLUE = {"的", "中的", "在", "及", "和", "与", "对", "中", "基于", "针对", "利用", "应用于", "用于", "一种"}
_STRUCTURE = {
    "背景/问题": r"\b(?:background|objective|objectives|aim|aims|challenge|challenges|however)\b|背景|目的|针对|问题",
    "方法/方案": r"\b(?:method|methods|methodology|we propose|we develop|we present|framework|designed)\b|方法|提出|构建|设计",
    "实验/结果": r"\b(?:results?|experiments?|evaluated|evaluation|outperform|accuracy|findings)\b|实验|结果|评估|准确率|发现",
    "结论/意义": r"\b(?:conclusions?|implications?|suggests?|demonstrates?|indicates?)\b|结论|表明|意味着|意义",
}


def normalize_title(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold())


def normalize_venue(value: str | None) -> str:
    # Preserve punctuation and all scripts: no fuzzy or translated journal aliases.
    return " ".join((value or "").casefold().split())


def _terms(text: str) -> set[str]:
    text = text[:20000]
    # Synchronous built-in phrase mapping only; never a model translation request.
    translated = QueryPlanner._translate_to_english(text)
    result = set(tokenize(_FAMILY.sub(" ", text + " " + translated))) - _GENERIC
    if _FAMILY.search(text):
        result.add("large language model")
    return result


def _seed_weights(seed: Paper, keywords: list[str]) -> Counter:
    weights = Counter({term: 3 for term in _terms(seed.title)})
    weights.update({term: 4 for term in _terms(" ".join(keywords[:20]))})
    # Abstract-only terms have less influence than the title/problem/method.
    weights.update({term: 1 for term in _terms(seed.abstract or "")})
    return Counter(dict(sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:32]))


def _query(seed: Paper) -> str:
    title = _FAMILY.sub(" ", QueryPlanner._translate_to_english(seed.title))
    title = " ".join(word for word in title.split() if word not in _QUERY_GLUE)
    if re.search(r"[\u4e00-\u9fff]", title):
        query = " ".join(title.split())[:140]
    else:
        words = [word for word in re.findall(r"[\w-]+", title.casefold()) if word not in _GENERIC]
        query = " ".join(dict.fromkeys(words))[:140]
        query = " ".join(query.split()[:6])
    if _FAMILY.search(seed.title + " " + (seed.abstract or "")[:2000]):
        query = "large language model " + query
    return query.strip() or seed.title[:160]


def structure_labels(abstract: str | None) -> list[str]:
    return [label for label, pattern in _STRUCTURE.items() if re.search(pattern, (abstract or "")[:20000], re.I)]


class _JournalCrossRefSource(CrossRefSource):
    """Field query improves recall; the service still requires exact venue equality."""

    def __init__(self, venue: str, **kwargs):
        super().__init__(**kwargs)
        self.venue = venue

    async def search(self, query: str, max_results: int = 30) -> list[Paper]:
        response = await self._request_with_retry("GET", "/works", params={
            "query.container-title": self.venue,
            "query.bibliographic": query,
            "rows": min(max_results, CANDIDATES_PER_SOURCE),
            "sort": "relevance", "order": "desc",
        })
        items = response.json().get("message", {}).get("items", [])
        return [paper for item in items if (paper := self._parse_paper(item)) is not None]


def _sources(seed: Paper, mode: SimilarMode):
    options = {"timeout": SOURCE_TIMEOUT, "max_retries": 0}
    crossref = (
        _JournalCrossRefSource(venue=seed.venue, email=settings.CROSSREF_EMAIL, **options)
        if mode == "journal" else CrossRefSource(email=settings.CROSSREF_EMAIL, **options)
    )
    return {
        DataSource.OPENALEX: OpenAlexSource(email=settings.OPENALEX_EMAIL, api_key=settings.OPENALEX_API_KEY, **options),
        DataSource.CROSSREF: crossref,
    }


def _safe_source_error(error: str | None) -> str | None:
    if not error:
        return None
    if "429" in error or "断路" in error:
        return "数据源限流或暂时熔断，请稍后重试"
    if "超时" in error or "timeout" in error.casefold():
        return "数据源请求超时，本次样本不完整"
    return "数据源暂不可用，本次样本不完整"


def _unique_candidates(seed: Paper, papers: list[Paper]) -> list[Paper]:
    ids = {str(seed.id)}
    dois = {normalize_doi(seed.doi)} - {""}
    titles = {normalize_title(seed.title)} - {""}
    result = []
    # Prefer the actual record with an abstract, without synthesizing a new abstract.
    for paper in sorted(papers, key=lambda item: bool(item.abstract), reverse=True):
        doi, title = normalize_doi(paper.doi), normalize_title(paper.title)
        if str(paper.id) in ids or (doi and doi in dois) or (title and title in titles):
            continue
        if not title:
            continue
        ids.add(str(paper.id))
        if doi:
            dois.add(doi)
        titles.add(title)
        result.append(paper)
    return result


async def recommend_similar(seed: Paper, mode: SimilarMode, limit: int, *, keywords: list[str] | None = None) -> SimilarPapersResponse:
    scope = "仅依据本次外部检索返回的标题和原始摘要，不读取全文、不调用生成模型；频次为种子相关术语及模型品牌在去重且排除种子后的样本论文中出现的篇数，不代表全刊、全领域占比或全文词频。"
    response = SimilarPapersResponse(seed_paper_id=str(seed.id), mode=mode, scope=scope)
    require_llm = mode == "topic" and bool(_FAMILY.search(seed.title))
    if require_llm:
        response.scope += "种子标题明确涉及大语言模型，主题推荐也要求候选标题或摘要中有相应模型线索。"
    elif mode in {"structure", "journal"}:
        response.scope += "此模式可包含同领域采用其他方法的论文，供摘要结构或同刊研究参考。"
    if mode == "journal":
        response.statistics.term_sample_scope = "same_venue"
        response.scope += "同刊按规范空白与大小写后的完整刊名精确匹配，未遍历期刊全部论文。"
        if not normalize_venue(seed.venue):
            response.warnings.append("种子论文缺少期刊/会议名称，无法核对同刊论文。")
            return response
    seed_labels = structure_labels(seed.abstract)
    if re.search(r"[\u4e00-\u9fff]", seed.title):
        response.warnings.append("跨语言检索仅使用内置术语映射，未翻译的专业词可能影响召回。")
    if mode == "structure":
        response.scope += "摘要结构标签仅为可观察的措辞线索，不代表全文结构、写作质量或内容结论一致。"
        if len(seed_labels) < 2:
            response.warnings.append("种子摘要缺失或不足以识别至少两种结构线索，暂不推荐写作结构相似论文。")
            return response

    sources = _sources(seed, mode)
    try:
        result = await Retriever(sources=sources, timeout=SOURCE_TIMEOUT).retrieve([
            SubQuery(query=_query(seed), source=source, rationale="以种子论文研究问题与方法检索")
            for source in sources
        ], max_results=CANDIDATES_PER_SOURCE)
    finally:
        await asyncio.gather(*(source.close() for source in sources.values()), return_exceptions=True)

    response.source_statuses = [SimilarSourceStatus(
        source=status.source, success=status.success, paper_count=status.paper_count,
        elapsed_ms=round(status.elapsed_ms, 2), error=_safe_source_error(status.error),
    ) for status in result.source_statuses]
    if any(not status.success for status in result.source_statuses):
        response.warnings.append("部分数据源未完成，本次仅展示已成功取得的候选。")
    candidates = _unique_candidates(seed, result.papers[:CANDIDATES_PER_SOURCE * 2])
    venue = normalize_venue(seed.venue)
    same_venue = [paper for paper in candidates if venue and normalize_venue(paper.venue) == venue]
    sample = same_venue if mode == "journal" else candidates
    weights = _seed_weights(seed, keywords or [])
    scored = []
    frequencies = Counter()
    brand_frequencies = Counter()
    seed_brands = [brand for brand, pattern in _BRANDS.items() if pattern.search(seed.title + " " + (seed.abstract or "")[:20000])]
    for paper in sample:
        paper_text = paper.title + " " + (paper.abstract or "")[:20000]
        terms = _terms(paper_text)
        brand_frequencies.update(brand for brand, pattern in _BRANDS.items() if pattern.search(paper_text))
        matched = sorted(terms & weights.keys(), key=lambda term: (-weights[term], term))
        frequencies.update(matched)
        # Require a research/problem/method term, not just a model brand/family.
        topical = [term for term in matched if term != "large language model" and weights[term] >= 3]
        shared_labels = [label for label in seed_labels if label in structure_labels(paper.abstract)]
        distinctive = [term for term in topical if term not in _BROAD_METHODS]
        if (
            not distinctive
            or (require_llm and "large language model" not in terms)
            or (mode == "structure" and len(shared_labels) < 2)
        ):
            continue
        score = sum(weights[term] for term in matched)
        if mode == "structure":
            score += len(shared_labels) * 3
            reason = "同领域/方法词有重合；原始摘要共同出现" + "、".join(shared_labels) + "线索，仅供摘要写作参考。"
        elif mode == "journal":
            reason = "完整刊名与种子论文一致，标题/摘要包含相近研究词；属于本次检索样本。"
        else:
            reason = "标题/摘要与种子论文的研究问题或方法词重合：" + "、".join(topical[:5]) + "。"
        scored.append((score, SimilarPaperItem(
            paper=paper, reason=reason, matched_terms=matched[:8],
            structure_labels=shared_labels if mode == "structure" else [],
        )))
    scored.sort(key=lambda item: (-item[0], -(item[1].paper.year or 0), str(item[1].paper.id)))
    response.items = [item for _, item in scored[:limit]]
    # Keep model brands visible rather than collapsing their document counts into LLM.
    brand_order = seed_brands + [brand for brand in _BRANDS if brand not in seed_brands and brand_frequencies[brand]]
    term_counts = [(brand, brand_frequencies[brand]) for brand in brand_order]
    term_counts.extend(sorted(frequencies.items(), key=lambda item: (-item[1], item[0])))
    response.statistics = SimilarStatistics(
        candidate_count=len(candidates), matched_count=len(scored), returned_count=len(response.items),
        same_venue_count=len(same_venue), term_sample_count=len(sample),
        term_sample_scope="same_venue" if mode == "journal" else "retrieved_candidates",
        terms=[TermFrequency(term=term, count=count) for term, count in term_counts[:12]],
    )
    if not response.items:
        response.warnings.append("本次有限候选中没有满足推荐条件的论文；不代表该方向或期刊没有相关研究。")
    return response

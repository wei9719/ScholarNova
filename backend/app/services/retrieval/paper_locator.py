"""Deterministic paper/page/table locators; no model or external lookups."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.retrieval.bm25 import tokenize

if TYPE_CHECKING:
    from collections.abc import Sequence

_NUMBER = r"(?:\d{1,4}|[零〇一二两三四五六七八九十百]{1,6})"
_PAGE = re.compile(rf"第\s*({_NUMBER})\s*页|\b(?:page|p\.)\s*(\d{{1,4}})\b", re.I)
_TABLE = re.compile(rf"(?<![a-z])(?:table|tab\.?)\s*([0-9]{{1,3}}|[ivxlcdm]{{1,8}})(?![a-z0-9])|表\s*({_NUMBER})", re.I)
_DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-"})
_RESEARCH_TERMS = (
    (r"数据集|数据类型", "dataset datasets data"),
    (r"缺失|掩码", "missing masking random block"),
    (r"评价|评估|指标", "evaluation metrics MAE RMSE MSE MAPE"),
    (r"实验协议|实验设置|训练|划分", "experimental settings training validation test split"),
    (r"插补|填补|缺失修复", "imputation"),
    (r"预测", "forecasting prediction"),
    (r"速度", "speed velocity"),
    (r"流量", "flow volume counts"),
)


@dataclass(frozen=True)
class PaperLocator:
    page: int | None = None
    table: int | None = None

    @property
    def explicit(self) -> bool:
        return self.page is not None or self.table is not None


def _number(value: str) -> int:
    if value.isdecimal():
        return int(value)
    if re.fullmatch(r"[ivxlcdm]+", value, re.I):
        values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
        total, previous = 0, 0
        for character in value.lower()[::-1]:
            current = values[character]
            total += -current if current < previous else current
            previous = max(previous, current)
        return total
    digits = {character: number for number, character in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    total, current = 0, 0
    for character in value:
        if character in {"十", "百"}:
            total += (current or 1) * {"十": 10, "百": 100}[character]
            current = 0
        else:
            current = digits[character]
    return total + current


def parse_locator(question: str) -> PaperLocator:
    page_match, table_match = _PAGE.search(question), _TABLE.search(question)
    page = _number(next(group for group in page_match.groups() if group)) if page_match else None
    table = _number(next(group for group in table_match.groups() if group)) if table_match else None
    return PaperLocator(page=page, table=table)


def normalize_query(question: str) -> str:
    """Align compact labels with PDF text without changing the original question."""
    text = question.translate(_DASHES)
    text = re.sub(r"\b(table|tab)\.?\s*(\d+)\b", r"Table \2", text, flags=re.I)
    text = re.sub(r"\b(RM|CM)\s*(\d+)\s*%?", r"\1 \2%", text, flags=re.I)
    return text


def expand_research_query(question: str) -> str:
    """Small transparent bilingual lookup vocabulary, not generated evidence."""
    terms = [word for pattern, words in _RESEARCH_TERMS if re.search(pattern, question)
             for word in words.split()]
    return question + "\n" + " ".join(dict.fromkeys(terms)) if terms else question


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text).strip()


def lookup_terms(question: str) -> list[str]:
    """Bound SQL preselection to distinctive words, before loading PDF text."""
    tokens = list(dict.fromkeys(tokenize(normalize_query(question))))
    english = [token for token in tokens if re.search(r"[a-z]", token) and len(token) >= 3]
    chinese = [token for token in tokens if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token)]
    return (english + chinese)[:12]


def match_named_papers(question: str, titles: Sequence[tuple[str, str]]) -> list[str]:
    """Match whole titles or distinctive prefixes, never arbitrary topic overlap."""
    normalized = _normalized(question)
    matches: list[tuple[str, str, bool, list[tuple[int, int]]]] = []
    for paper_id, title in titles:
        full = _normalized(title)
        prefix = _normalized(re.split(r"[:：]", title, maxsplit=1)[0])
        aliases = {full} if len(full) >= 6 else set()
        if prefix != full and len(prefix) >= 4:
            aliases.add(prefix)
        words = full.split()
        if len(words) > 6:
            aliases.add(" ".join(words[:6]))
        for alias in sorted(aliases, key=len, reverse=True):
            # Chinese surrounding an English title is not part of its token.
            pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            spans = [match.span() for match in re.finditer(pattern, normalized)]
            # Hyphenated acronym prefixes also accept STDPLM/STD PLM forms.
            if not spans and alias == prefix and re.fullmatch(r"[A-Za-z0-9-]{4,24}", re.split(r"[:：]", title, maxsplit=1)[0].translate(_DASHES).strip()):
                compact = prefix.replace(" ", "")
                pattern = r"(?<![a-z0-9])" + r"\s*".join(map(re.escape, compact)) + r"(?![a-z0-9])"
                spans = [match.span() for match in re.finditer(pattern, normalized)]
            if spans:
                matches.append((paper_id, alias, alias == full, spans))
                break
    # A prefix occurring only inside another explicitly supplied full title
    # does not identify a second PDF. Separate explicit titles remain intact.
    exact_spans = [(pid, span) for pid, _, exact, spans in matches if exact for span in spans]
    retained = [
        (len(alias), pid) for pid, alias, exact, spans in matches
        if exact or any(not any(
            other != pid and outer[0] <= span[0] and span[1] <= outer[1]
            and outer[1] - outer[0] > span[1] - span[0]
            for other, outer in exact_spans
        ) for span in spans)
    ]
    # Keep explicitly named comparisons, but never silently truncate >4 matches.
    return list(dict.fromkeys(paper_id for _, paper_id in sorted(retained, reverse=True)))


def has_paper_title_cue(question: str) -> bool:
    """Unknown explicit titles must not silently degrade to unrelated topics."""
    return bool(re.search(
        r"《[^》]{4,300}》|[a-z0-9][a-z0-9 ._-]{2,80}\s*(?:原文|这篇论文)",
        question, re.I,
    ))


def has_ambiguous_named_prefixes(question: str, titles: Sequence[tuple[str, str]]) -> bool:
    """A comparison word alone cannot disambiguate papers sharing one name."""
    groups: dict[str, list[str]] = {}
    for _, title in titles:
        prefix = _normalized(re.split(r"[:：]", title, maxsplit=1)[0])
        groups.setdefault(prefix, []).append(_normalized(title))
    normalized = _normalized(question)
    return any(
        len(full_titles) > 1 and (
            len(set(full_titles)) != len(full_titles)
            or not all(re.search(r"(?<![a-z0-9])" + re.escape(full) + r"(?![a-z0-9])", normalized)
                       for full in full_titles)
        ) for full_titles in groups.values()
    )


def table_matches(text: str, number: int) -> bool:
    return any(_number(next(group for group in match.groups() if group)) == number
               for match in _TABLE.finditer(text))


def table_caption_matches(text: str, number: int) -> bool:
    for match in _TABLE.finditer(text):
        if _number(next(group for group in match.groups() if group)) != number:
            continue
        if not text[text.rfind("\n", 0, match.start()) + 1:match.start()].strip():
            return True
    return False

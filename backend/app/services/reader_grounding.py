"""Keep model-written mathematical interpretations out of the original-text lane."""

from __future__ import annotations

import json
import re

from app.schemas.reader import ReaderSourceGrounding

MAX_QUOTES = 6
MAX_QUOTE_LENGTH = 800
SOURCE_SCOPE = (
    "原文栏仅展示在本次实际发送的 PDF 文字节选中连续匹配的摘录，只合并排版空白。"
    "匹配不验证公式排版、解释、推导或摘录是否完整；未匹配不等于论文没有写。"
    "模型解释、常识、译释和图像公式转录均属教学补充或待核对草稿，不是作者明文。"
)


def normalize_quote(text: str) -> str:
    # Never NFKC-fold exponents/minus signs or remove word boundaries in mathematics.
    return re.sub(r"\s+", " ", text).strip()


def _without_page_citations(text: str) -> str:
    # Page provenance is shown with verified quotes, not appended to AI deductions.
    return re.sub(r"\[[PS]\s*\d+\]", "", text, flags=re.I).strip()


def ground_math_response(
    content: str, excerpt: str, page_number: int, source_notes: str, teaching_notes: str,
) -> tuple[str, str, ReaderSourceGrounding]:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        payload = json.loads(value)
    except (ValueError, TypeError):
        payload = {}
    quotes = payload.get("source_quotes", []) if isinstance(payload, dict) else []
    matched, rejected, drafts = [], 0, []
    if not isinstance(quotes, list) or len(quotes) > MAX_QUOTES:
        rejected = len(quotes) if isinstance(quotes, list) else 1
        quotes = []
    page_text = normalize_quote(excerpt)
    for item in quotes:
        if not isinstance(item, str) or not 12 <= len(item.strip()) <= MAX_QUOTE_LENGTH:
            rejected += 1
            continue
        quote = normalize_quote(item)
        if len(quote) < 12:
            rejected += 1
            continue
        if quote not in page_text:
            rejected += 1
            drafts.append(_without_page_citations(item.strip()))
        elif quote not in matched:
            # Emit the matched page text, never an interpretation beside a quote.
            start = page_text.index(quote)
            matched.append(page_text[start:start + len(quote)])
    teaching = []
    if source_notes:
        teaching.append("待核对的模型解读（原模型原文栏，未核实为作者明文）：\n"
                        + _without_page_citations(source_notes))
    if teaching_notes:
        teaching.append(_without_page_citations(teaching_notes))
    if drafts:
        teaching.append("未在本次文字节选中匹配的模型摘录（不是已核实原文）：\n"
                        + "\n\n".join(drafts))
    source = "\n\n".join(f"「{quote}」 [P{page_number}]" for quote in matched)
    detail = SOURCE_SCOPE
    if not matched:
        detail += " 本次没有可展示的匹配摘录，请查看原页；不能据此断言原文未定义相关符号。"
    if rejected:
        detail += f" {rejected} 条候选摘录未通过本次匹配或输入范围检查，未放入原文栏。"
    return source, "\n\n".join(teaching), ReaderSourceGrounding(
        matched_quotes=len(matched), rejected_quotes=rejected,
        moved_source_notes=bool(source_notes), detail=detail,
    )

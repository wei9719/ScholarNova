"""Bounded, labelled excerpts for research planning; no model or persistence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Sequence

from app.services.features.reading_notes import reading_note_parts
from app.services.retrieval.bm25 import tokenize


MAX_RESEARCH_CONTEXT_CHARS = 18_000
_GOALS = {
    "实验与复现步骤": ("实验", "复现", "缺失", "掩码", "评价", "指标", "训练", "数据集", "防泄漏", "experiment", "imput", "mask", "metric", "split", "train", "baseline"),
    "公式与推导": ("公式", "推导", "数学", "维度", "证明", "equation", "math", "deriv", "proof"),
    "代码、数据与公开联系": ("源码", "代码", "数据获取", "联系", "邮箱", "github", "code", "resource", "contact"),
    "基础知识与技能": ("基础", "先修", "理解", "学习", "prerequisite", "learn", "understand"),
    "章节写作": ("写作", "摘要", "结论", "投稿", "writing", "abstract", "conclusion"),
    "绘图与伪代码": ("绘图", "架构", "伪代码", "流程图", "figure", "diagram", "pseudocode"),
    "中英转换": ("翻译", "中英", "translation", "translate"),
    "LaTeX 与 Word 排版": ("排版", "latex", "word", "format"),
}


@dataclass(frozen=True)
class ResearchContext:
    text: str
    coverage: str
    warnings: list[str]
    source_chars: int


def _cost(text: str) -> int:
    # Count the actual JSON-quoted material, not only its unescaped characters.
    return len(json.dumps(text, ensure_ascii=False)) - 2


def _fit(text: str, budget: int) -> str:
    low, high = 0, min(len(text), max(0, budget))
    while low < high:
        middle = (low + high + 1) // 2
        if _cost(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _parts(item: Any, query: str) -> list[tuple[str, str]]:
    data = getattr(item, "card_data", None)
    parts: list[tuple[str, str]] = []
    if getattr(item, "card_type", None) == "reading_note" and isinstance(data, dict):
        sections = data.get("sections")
        if isinstance(sections, dict):
            parts = reading_note_parts({"sections": {
                key: value for key, value in sections.items() if isinstance(value, dict)
            }})
    if not parts and (getattr(item, "content", None) or "").strip():
        parts = [("知识库正文（用户整理，未独立核验，非论文原文）", item.content)]
    if getattr(item, "notes", None):
        parts.append(("个人备注，非论文事实", item.notes))
    points = getattr(item, "research_points", None) or []
    if points:
        parts.append(("条目研究点（用户整理，未独立核验）", "\n".join(str(point) for point in points[:3])))
    lowered = query.casefold()

    def priority(part: tuple[str, str]) -> int:
        label = part[0]
        score = sum(100 for section, hints in _GOALS.items()
                    if label.startswith(section) for hint in hints if hint in lowered)
        # Within a relevant section, keep the source and the reader's constraints
        # ahead of model suggestions. These labels are never collapsed together.
        score += 2 if "论文材料" in label else 1 if "个人" in label else 0
        return score

    return sorted(((label, text.strip()) for label, text in parts if text.strip()), key=priority, reverse=True)


def _excerpt(text: str, budget: int, terms: Sequence[str]) -> tuple[str, int]:
    head = _fit(text, budget)
    if len(head) == len(text) or not head:
        return head, 0
    starts = {0}
    for term in terms:
        match = re.search(re.escape(term), text, flags=re.IGNORECASE)
        if match:
            starts.add(max(0, match.start() - len(head) // 4))
    choices = [(_fit(text[start:start + budget], budget), start) for start in sorted(starts)]
    return max(choices, key=lambda value: (sum(term in value[0].casefold() for term in terms), -value[1]))


def _note(item: Any, index: int, parts: list[tuple[str, str]], budget: int, terms: Sequence[str]) -> tuple[str, str, int, int]:
    metadata = f"条目{index} ID={item.id}\n标题：{item.title}\n分类：{item.category}\n"
    header = _fit(metadata, min(300, budget // 3))
    available = budget - _cost(header)
    selected: list[tuple[str, str]] = []
    for label, text in parts:
        prefix = f"\n【{label}；笔记节选】\n"
        if sum(_cost(f"\n【{name}；笔记节选】\n") + min(64, _cost(body)) for name, body in selected) + _cost(prefix) + min(64, _cost(text)) > available:
            break
        selected.append((label, text))
    prefixes = [f"\n【{label}；笔记节选】\n" for label, _ in selected]
    body_budget = max(0, available - sum(_cost(prefix) for prefix in prefixes))
    allocations = [body_budget // len(selected)] * len(selected) if selected else []
    excerpts = [_excerpt(text, allocation, terms) for (_, text), allocation in zip(selected, allocations)]
    for _ in range(len(selected)):
        remaining = body_budget - sum(_cost(text) for text, _ in excerpts)
        active = [i for i, ((_, text), (excerpt, _)) in enumerate(zip(selected, excerpts)) if len(excerpt) < len(text)]
        if not active or remaining < len(active):
            break
        for i in active:
            allocations[i] = _cost(excerpts[i][0]) + remaining // len(active)
            excerpts[i] = _excerpt(selected[i][1], allocations[i], terms)
    block = header + "".join(prefix + excerpt for prefix, (excerpt, _) in zip(prefixes, excerpts))
    chosen = [f"{label}（字符{start + 1}–{start + len(excerpt)}/{len(text)}）"
              for (label, text), (excerpt, start) in zip(selected, excerpts)]
    omitted = [label for label, _ in parts[len(selected):]]
    truncated = sum(len(excerpt) < len(text) for (_, text), (excerpt, _) in zip(selected, excerpts))
    info = f"条目{index} ID={item.id}：选入 " + ("；".join(chosen) or "无正文")
    if omitted:
        info += "；未纳入 " + "、".join(omitted)
    if len(header) < len(metadata):
        info += "；标题/分类等元信息亦有截断"
    return block, info, truncated, len(omitted)


def build_research_context(items: Sequence[Any], query: str) -> ResearchContext:
    """Give each selected note a fair initial share, then reuse spare capacity."""
    terms = sorted(set(tokenize(query)), key=lambda value: (-len(value), value))[:32]
    parts = [_parts(item, query) for item in items]
    source_chars = sum(len(text) for note in parts for _, text in note)
    if not items:
        return ResearchContext("", "没有有效知识条目。", [], 0)
    budget = MAX_RESEARCH_CONTEXT_CHARS - 2 - 4 * (len(items) - 1)
    quotas = [budget // len(items)] * len(items)
    rendered = [_note(item, i + 1, note, quotas[i], terms) for i, (item, note) in enumerate(zip(items, parts))]
    for _ in range(len(items)):
        remaining = budget - sum(_cost(value[0]) for value in rendered)
        active = [i for i, value in enumerate(rendered) if value[2] or value[3]]
        if not active or remaining < len(active):
            break
        before = sum(_cost(value[0]) for value in rendered)
        for i in active:
            quotas[i] = _cost(rendered[i][0]) + remaining // len(active)
            rendered[i] = _note(items[i], i + 1, parts[i], quotas[i], terms)
        if sum(_cost(value[0]) for value in rendered) == before:
            break
    text = "\n\n".join(value[0] for value in rendered)
    truncated, omitted = sum(value[2] for value in rendered), sum(value[3] for value in rendered)
    coverage = (
        f"主分析按目标从{len(items)}条所选知识笔记取材；JSON资料字符串固定总预算{MAX_RESEARCH_CONTEXT_CHARS}字符，"
        f"实际{_cost(text) + 2}字符（含元信息/标签，不含用户问题及系统提示）；"
        f"非空字段原始正文共{source_chars}字符，{truncated}字段节选、{omitted}字段未纳入。"
        "不是PDF全文，不是已执行实验。\n" + "\n".join(value[1] for value in rendered)
    )
    warnings = ["仅分析所选笔记节选，不等于读取论文全文或执行实验；AI建议与个人想法不得写成已验证论文结论。"]
    if truncated or omitted:
        warnings.append(f"预算内有{truncated}个字段截断、{omitted}个字段未纳入，实际字段与字符范围见材料覆盖说明。")
    return ResearchContext(text, coverage, warnings, source_chars)

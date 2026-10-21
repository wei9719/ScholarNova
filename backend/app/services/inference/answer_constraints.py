"""Small, deterministic output checks; never a factual/semantic correctness judge."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence


_CITATION = re.compile(r"\[s\d+\]", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)")
_NUMBER = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
           "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_COUNT = r"\d+|[一二两三四五六七八九十]|one|two|three|four|five"
_SENTENCE_REQUIREMENT = re.compile(
    rf"(?P<mode>只(?:用|要)?(?:回答|答|说|写)?|仅(?:用|回答)?|恰好|不超过|最多|至多)\s*"
    rf"(?P<count>{_COUNT})\s*句(?:话)?|"
    rf"(?P<en_mode>exactly|only|at most|no more than)\s+(?P<en_count>{_COUNT})\s+sentences?",
    re.IGNORECASE,
)
_NEGATED_REQUIREMENT = re.compile(
    r"(?:不要|不用|不必|无需|不能|不要求|别)(?:再)?\s*$|"
    r"\b(?:not|never|do not(?: use)?|don't(?: use)?)\s*$",
    re.IGNORECASE,
)
_SECTIONS = re.compile(
    r"(?:区分|分开|分段|分别).{0,16}原文.{0,16}教学|"
    r"原文.{0,16}教学.{0,16}(?:分开|分段|区分)|"
    r"(?:separate|distinguish).{0,30}source.{0,30}teaching",
    re.IGNORECASE,
)
_REPEAT = re.compile(
    r"(?:不要|别|无需|不必)(?:再)?(?:重复|重述|复述)"
    r"(?:上一问|上轮(?:回答)?|上一次(?:回答)?|前一问|之前(?:的回答)?)"
    r"(?:中|里)?(?:提到|使用|介绍|说明)?(?:的)?(?P<topic>[^，,。.!?？;；\n]{1,16})",
)
_TOPICS = {
    "数据集": r"数据集|datasets?",
    "模型": r"模型(?:名称)?|model(?: name)?",
    "指标": r"(?:评价)?指标|metrics?",
    "方法": r"方法(?:名称)?|methods?",
}
_IDENTIFIER = r"[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*"
_PURE_HEADING = re.compile(
    r"(?:原文(?:信息|事实)?|教学(?:解释|说明)?|直接结论|证据与说明|仍需确认的问题|"
    r"source information|teaching explanation)(?:[（(][^）)\n]{0,30}[）)])?[:：]?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class OutputContract:
    sentence_count: int | None = None
    at_most: bool = False
    source_teaching: bool = False
    forbidden: tuple[str, ...] = ()
    unchecked: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.sentence_count or self.source_teaching or self.forbidden or self.unchecked)

    @property
    def instruction(self) -> str:
        requirements = []
        if self.sentence_count:
            requirements.append(f"{'至多' if self.at_most else '恰好'} {self.sentence_count} 句，标题与引用标记不另算一句")
        if self.source_teaching:
            requirements.append("明确用“原文信息”和“教学解释”标签区分两部分，可用行内标签避免增加句数")
        if self.forbidden:
            requirements.append("不得复述上一问对象值：" + "、".join(self.forbidden))
        return "；".join(requirements)


@dataclass(frozen=True, slots=True)
class OutputCheck:
    status: Literal["passed", "failed", "not_checked", "not_applicable"]
    issues: tuple[str, ...] = ()
    unchecked: tuple[str, ...] = ()

    @property
    def detail(self) -> str:
        if self.status == "not_applicable":
            return "本轮没有识别到支持的显式输出约束；未作格式合格保证"
        if self.status == "failed":
            return "输出要求未满足：" + "；".join(self.issues) + "。保留实际回答，未裁剪拼接；引用通过不代表格式或内容正确。"
        if self.status == "not_checked":
            return "部分输出要求无法客观核验：" + "；".join(self.unchecked)
        return "已通过本轮可核验的句数、分段标签或指定历史值检查；不代表全部语义要求、原文解释或数学推导正确"


def _identifier(value: str) -> bool:
    return len(value) >= 2 and not re.fullmatch(r"S\d+", value, re.IGNORECASE) and (
        any(character.isdigit() for character in value) or value.isupper()
    )


def parse_output_contract(question: str, history: Sequence[object] = ()) -> OutputContract:
    """Check only explicit, objective requirements; history is data, not evidence."""
    count_match = next((match for match in _SENTENCE_REQUIREMENT.finditer(question)
                        if not _NEGATED_REQUIREMENT.search(question[:match.start()])), None)
    count = None
    at_most = False
    unchecked: list[str] = []
    if count_match:
        raw = (count_match.group("count") or count_match.group("en_count")).casefold()
        count = int(raw) if raw.isdigit() else _NUMBER[raw]
        if not 0 < count <= 20:
            count = None
            unchecked.append("句数要求超出 1 至 20 句的自动核验范围，仍须人工核对")
        at_most = (count_match.group("mode") or count_match.group("en_mode")).casefold() in {
            "不超过", "最多", "至多", "at most", "no more than",
        }
    forbidden: list[str] = []
    previous = next((getattr(item, "content", "") for item in reversed(history)
                     if getattr(item, "role", "") == "assistant"), "")
    previous = _CITATION.sub("", previous)
    for match in _REPEAT.finditer(question):
        topic = next((pattern for name, pattern in _TOPICS.items() if name in match.group("topic")), None)
        values: list[str] = []
        if topic:
            # Only an identifier directly attached to the requested object is
            # objective enough to check. Do not ban every historical model name.
            for pattern in (
                rf"(?P<value>{_IDENTIFIER})\s*(?:的)?\s*(?:{topic})",
                rf"(?:{topic})\s*(?:为|是|采用|使用|[:：]|is|was)?\s*(?P<value>{_IDENTIFIER})",
            ):
                values.extend(found.group("value") for found in re.finditer(pattern, previous, re.IGNORECASE)
                              if _identifier(found.group("value")))
        values = [value for value in values if not re.search(
            rf"(?<![A-Za-z0-9_.-]){re.escape(value)}(?![A-Za-z0-9_.-])", question, re.IGNORECASE,
        )]
        if values:
            forbidden.extend(values)
        else:
            unchecked.append("未能唯一定位上一问所指对象值，禁复述要求仍须人工核对")
    sections = bool(_SECTIONS.search(question)) and not re.search(
        r"(?:无需|不用|不必|不要)(?:再)?(?:区分|分开|分段).{0,16}原文.{0,16}教学", question,
    )
    return OutputContract(count, at_most, sections,
                          tuple(dict.fromkeys(forbidden))[:12], tuple(dict.fromkeys(unchecked)))


def _sentence_count(answer: str) -> int | None:
    plain = _CITATION.sub("", answer)
    # Avoid splitting decimal/model versions, common abbreviations, or labels.
    plain = re.sub(r"\b(?:e\.g\.|i\.e\.|et al\.|Fig\.|Eq\.|Dr\.|Mr\.|approx\.)", lambda match: match.group().replace(".", "·"), plain, flags=re.IGNORECASE)
    plain = re.sub(r"\b(?:[A-Z]\.){2,}", lambda match: match.group().replace(".", "·"), plain)
    lines = []
    for line in plain.splitlines():
        stripped = line.strip()
        content = re.sub(r"^(?:[-*#>]+|\d+[.)、])\s*", "", stripped).strip("*_` ")
        if not content or _PURE_HEADING.fullmatch(content):
            continue
        # A line wrap inside prose is not a sentence boundary. Unpunctuated
        # list items are ambiguous, so do not pretend their count is verified.
        if re.match(r"^(?:[-*]+|\d+[.)、])\s+", stripped) and not re.search(r"[。！？.!?]$", content):
            return None
        lines.append(content)
    plain = " ".join(lines)
    if plain and not re.search(r"[。！？.!?]$", plain):
        return None
    segments = []
    for segment in _SENTENCE_SPLIT.split(plain):
        segment = re.sub(r"^(?:[-*#>]+|\d+[.)、])\s*", "", segment.strip()).strip("*_` ")
        if segment and not _PURE_HEADING.fullmatch(segment) and re.search(r"[\w\u4e00-\u9fff]", segment):
            segments.append(segment)
    return len(segments)


def check_output_contract(answer: str, contract: OutputContract) -> OutputCheck:
    if not contract.active:
        return OutputCheck("not_applicable")
    issues = []
    unchecked = list(contract.unchecked)
    if contract.sentence_count:
        actual = _sentence_count(answer)
        if actual is None:
            unchecked.append("缺少明确句末标点，无法客观核验句数，仍须人工核对")
        elif actual == 0 or actual > contract.sentence_count or not contract.at_most and actual != contract.sentence_count:
            issues.append(f"要求{'至多' if contract.at_most else '恰好'} {contract.sentence_count} 句，实际识别 {actual} 句")
    if contract.source_teaching and not (
        re.search(r"(?:原文(?:信息|事实)?|source information)(?:[（(][^）)\n]{0,30}[）)])?[*_` ]*[:：\n]", answer, re.IGNORECASE)
        and re.search(r"(?:教学(?:解释|说明)?|teaching explanation)(?:[（(][^）)\n]{0,30}[）)])?[*_` ]*[:：\n]", answer, re.IGNORECASE)
    ):
        issues.append("缺少原文信息与教学解释的明确分段标签")
    if any(re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(value)}(?![A-Za-z0-9_.-])", answer, re.IGNORECASE)
           for value in contract.forbidden):
        issues.append("仍复述了用户要求省略的上一问对象值")
    return OutputCheck("failed" if issues else "not_checked" if unchecked else "passed",
                       tuple(issues), tuple(unchecked))

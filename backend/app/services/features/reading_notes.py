"""Deterministic reading-note text shared by persistence and retrieval."""

SECTION_TITLES = {
    "overview": "论文想法与概述",
    "experiments": "实验与复现步骤",
    "resources": "代码、数据与公开联系",
    "math": "公式与推导",
    "prerequisites": "基础知识与技能",
    "writing": "章节写作",
    "figures": "绘图与伪代码",
    "translation": "中英转换",
    "formatting": "LaTeX 与 Word 排版",
}
NOTE_LABELS = {
    "paper_notes": "论文材料摘录/用户整理，未独立核验",
    "ai_suggestions": "AI建议，非论文事实",
    "user_notes": "个人想法，非论文事实",
}


def reading_note_parts(data: dict) -> list[tuple[str, str]]:
    """Keep provenance beside every field, without rewriting the user's text."""
    parts = []
    sections = data.get("sections") or {}
    for name, title in SECTION_TITLES.items():
        section = sections.get(name) or {}
        for field, label in NOTE_LABELS.items():
            text = section.get(field, "")
            if isinstance(text, str) and text.strip():
                parts.append((f"{title} · {label}", text))
    return parts


def render_reading_note(data: dict) -> str:
    return "\n\n".join(f"## {label}\n{text}" for label, text in reading_note_parts(data))

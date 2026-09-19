"""
科研框架图提示词引擎（Nature-figure style, domain-adapted）

把 Nature/Cell 期刊配图的提示词方法论（Paper Analysis → Figure Planning →
Prompt Generation → Quality Check）适配到计算机 / 工程 / AI 科研领域。

关键设计（双层约束）：
1. 规划层（LLM）：把完整的 Nature 规范（布局 / 配色 / 禁止元素 / 检查清单）
   作为约束交给 LLM，让它在设计模块时遵守。
2. 渲染层（生图模型）：最终交给商汤等生图模型的提示词**只含可渲染的
   纯描述**（标题 + 模块 + 布局 + 视觉风格 + 一句话配色），
   绝不包含 "COLOR RULES" / "FORBIDDEN" / "MUST FOLLOW" 这类元指令——
   生图模型会把它们当画面文字渲染成乱码。

其他原则：
- 短英文标签优先，降低生图模型文字乱码率
- 不注入任何编造的数值结果、不伪造数据
- 与 LLMGateway 解耦：本模块只负责"组装字符串"，调用方决定由谁生成规划
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# =============================================================================
# 布局模式（适配计算机/工程领域）
# =============================================================================

LAYOUT_PATTERNS = {
    "pipeline": {
        "name": "Horizontal Pipeline Flow",
        "best_for": "端到端方法 / 系统架构 / 数据流",
        "render": (
            "Layout: left-to-right pipeline of the supplied labeled modules, "
            "connected in their listed order by straight arrows. Each module "
            "contains its supplied internal components."
        ),
        "plan_hint": "left-to-right pipeline flow (inputs → core method → outputs)",
    },
    "hierarchy": {
        "name": "Layered / Multi-tier Structure",
        "best_for": "算法栈 / 框架分层 / 模型内部结构",
        "render": (
            "Layout: top-to-bottom stack of the supplied modules in their "
            "listed order. Layers are separated by horizontal boundaries "
            "with short labels; supplied internal components sit inside their layer."
        ),
        "plan_hint": "top-to-bottom layered stack",
    },
    "radial": {
        "name": "Radial Hub-and-Spoke",
        "best_for": "核心方法与多数据/多任务交互",
        "render": (
            "Layout: radial design with the first supplied module at the "
            "center and the remaining modules arranged around it. Each module "
            "carries its supplied label and internal components."
        ),
        "plan_hint": "radial hub-and-spoke with central method",
    },
    "comparison": {
        "name": "Side-by-side Comparison",
        "best_for": "消融实验 / 方法对比 / 基线对照",
        "render": (
            "Layout: side-by-side panels of equal size for the supplied "
            "modules. Each panel contains its supplied label and internal "
            "components, with consistent alignment and spacing."
        ),
        "plan_hint": "side-by-side comparison panels",
    },
    "roadmap": {
        "name": "Research Stage Roadmap",
        "best_for": "科研阶段路线图（选题→调研→方案→实验→成稿）",
        "render": (
            "Layout: horizontal research roadmap with 5 stage columns arranged "
            "left to right, connected by arrows: stage 1 topic formulation, "
            "stage 2 literature review, stage 3 method design, stage 4 "
            "experiments and validation, stage 5 paper writing. Each stage "
            "column contains 2-3 short task labels and one key deliverable. "
            "Use a distinct accent color for the current stage. Arrows show "
            "stage progression only."
        ),
        "plan_hint": "horizontal 5-stage research roadmap",
    },
}

# =============================================================================
# 配色规范（期刊投稿风格）
# =============================================================================

COLOR_NAVY = "#1F3A5F"
COLOR_TEAL = "#2A9D8F"
COLOR_GOLD = "#C9A227"
COLOR_BG = "#FAFAFA"

# 给生图模型的"一句话配色"（渲染层用，不列 HEX 清单）
RENDER_COLOR_HINT = (
    "Color palette: restrained academic colors — navy for primary modules, "
    "teal for method emphasis, a small amount of muted gold for key highlights, "
    "on a clean off-white background."
)

# =============================================================================
# 视觉风格基线（渲染层）
# =============================================================================

RENDER_VISUAL_STYLE = (
    "Visual style: publication-quality scientific figure, clean modern academic "
    "style, flat design with subtle shadows and soft gradients, rounded "
    "rectangles and clear geometric shapes, professional typography, concise "
    "short English labels only, generous white space between modules."
)

# =============================================================================
# 禁止元素（规划层约束，绝不进入渲染层提示词）
# =============================================================================

PLANNING_FORBIDDEN = """HARD RULES for module design:
- No invented numeric results, fake metrics, or unverified percentages
- No placeholder text like "MODULE 1", "XXXX", "LEFT SECTION"
- No tier labels as visible text ("TIER 1", "LEVEL 2")
- Every module label must be short English (1-3 words max)
- No logos, watermarks, or decorative filler
- Use the provided knowledge items as the ground truth; do not fabricate"""

# =============================================================================
# 规划 → 渲染提示词
# =============================================================================

def select_layout(
    route_title: str,
    analysis_text: str = "",
    knowledge_text: str = "",
) -> str:
    """
    根据研究路线标题、文字分析与知识库内容选择布局模式（启发式，可被 LLM 规划覆盖）。

    优先级：明确的方法对比 > 框架/分层 > 融合/多模态 > 默认流水线。
    """
    text = f"{route_title} {analysis_text} {knowledge_text}".lower()
    # 科研阶段路线图关键词优先
    if any(k in text for k in ("路线图", "科研阶段", "roadmap", "研究路线", "阶段规划")):
        return "roadmap"
    if any(k in text for k in ("对比", "消融", "基线", "compare", "ablation", "baseline")):
        return "comparison"
    if any(k in text for k in ("框架", "分层", "栈", "架构", "stack", "layer", "architect")):
        return "hierarchy"
    if any(k in text for k in ("融合", "多模态", "fusion", "multi-modal", "multimodal")):
        return "radial"
    return "pipeline"


def build_render_prompt(
    route_title: str,
    modules: Optional[List[Dict[str, Any]]] = None,
    layout: str = "pipeline",
) -> str:
    """
    生成**只含可渲染内容**的英文提示词（交给生图模型）。

    关键：这里不拼接 FORBIDDEN / COLOR RULES / 知识库原文——
    那些约束只作用于规划层，避免生图模型把指令当画面文字。
    比例也不写进渲染层（由 generate_image 的 aspect_ratio 参数控制），
    避免 "ASPECT RATIO:" 这类元指令被渲染成乱码。

    Args:
        route_title: 研究路线标题
        modules: 结构化模块列表 [{name, desc}]（由 LLM 规划或启发式生成）
        layout: 布局模式 key
    """
    layout_render = LAYOUT_PATTERNS.get(layout, LAYOUT_PATTERNS["pipeline"])["render"]

    parts: List[str] = []
    parts.append(
        "Create a clean, publication-quality research framework diagram for "
        "the topic: \"" + route_title + "\"."
    )

    if modules:
        # 只取 name + 内部子模块 + 公式，避免长 desc 被渲染成画面文字
        label_list = []
        for m in modules:
            if not (isinstance(m, dict) and m.get("name")):
                continue
            name = str(m["name"])
            subs = m.get("sub_modules")
            subs_list = []
            if isinstance(subs, list):
                subs_list = [
                    str(s) for s in subs if isinstance(s, str) and s.strip()
                ]
            formula = m.get("formula") if isinstance(m, dict) else None
            if subs_list:
                name = name + " (internal components: " + ", ".join(subs_list) + ")"
            if formula and isinstance(formula, str) and formula.strip():
                name = name + " (notation: " + formula + ")"
            label_list.append(name)
        if label_list:
            parts.append(
                "The diagram should contain these labeled modules, in order: "
                + ", ".join(label_list) + "."
            )

    parts.append(layout_render + ".")
    parts.append(RENDER_VISUAL_STYLE)
    parts.append(RENDER_COLOR_HINT)

    return "\n".join(parts)


# =============================================================================
# LLM 规划提示词（规划层：带完整 Nature 约束）
# =============================================================================

PLANNING_SYSTEM_PROMPT = (
    "You are a scientific figure planner following Nature/Cell journal "
    "publication standards. Given a research route and its knowledge base "
    "content, output a STRUCTURED JSON plan for a publication-quality "
    "framework diagram. Reply with JSON only, no markdown fences."
)

PLANNING_USER_TEMPLATE = """Research route: {route_title}

Verified knowledge items (ground truth):
{knowledge_text}

Route analysis (content reference only):
{text_analysis}

Choose the best layout for this content:
{layout_options}

Output JSON with this exact schema:
{{
  "layout": "pipeline" | "hierarchy" | "radial" | "comparison",
  "modules": [
    {{
      "name": "short English label (1-3 words)",
      "desc": "what this module covers, one short phrase",
      "sub_modules": ["up to 4 short internal component labels, only when supported by the knowledge items; otherwise use an empty list"],
      "formula": "exact formula copied from verified knowledge items only; empty string if none"
    }}
  ],
  "flow": "a short phrase describing the visual flow"
}}

{forbidden_rules}

Additional rules:
- 1 to 6 modules; use only as many as the knowledge items support
- Modules MUST reflect the actual research content from the knowledge items; do not invent
- No numeric results in labels
- Module and sub-module labels: one line, 1-3 words, at most 48 characters.
- desc: one line, at most 160 characters. formula: one line, at most 60 characters.
- Internal structure is optional. Include only components explicitly supported
  by the knowledge items; leave sub_modules empty when that detail is absent.
- Do not add layers, losses, attention blocks, feedback loops or other details
  merely to make the figure look more complex.
- Never invent, infer, or complete a formula. Copy it verbatim from the verified knowledge items or leave formula empty.
- Route analysis is AI-generated planning, not evidence for formulas or established paper facts."""


def build_planning_user_prompt(
    route_title: str,
    knowledge_text: str = "",
    text_analysis: str = "",
) -> str:
    """组装规划层用户提示词（带布局选项 + 禁止元素约束）。"""
    layout_options = "\n".join(
        f"- {key}: {pat['best_for']} ({pat['plan_hint']})"
        for key, pat in LAYOUT_PATTERNS.items()
    )
    return PLANNING_USER_TEMPLATE.format(
        route_title=route_title,
        knowledge_text=knowledge_text or "No linked knowledge details",
        text_analysis=text_analysis or "No analysis available",
        layout_options=layout_options,
        forbidden_rules=PLANNING_FORBIDDEN,
    )


# =============================================================================
# 检查清单（供日志 / 审计）
# =============================================================================

QUALITY_CHECKLIST = [
    "module labels are short English (1-3 words)",
    "no invented numeric results",
    "color coding consistent (navy/teal/gold)",
    "no forbidden placeholder labels",
    "flow direction matches layout",
    "knowledge items are the ground truth",
]

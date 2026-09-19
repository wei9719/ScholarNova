# 科研框架图提示词引擎（Diagram Prompt Engine）

ScholarNova 的研究路线图/架构图出图链路，采用 **Nature 期刊配图规范** 约束，
按 **双层约束** 设计：规范约束规划层（LLM），纯描述进渲染层（生图模型）。

## 设计动机

早期硬编码模板（`knowledge.py` 内联字符串）出的图信息密度低、占位感强。
改为「GLM 生成结构化提示词 → 商汤出图」后质量显著提升，但发现一个新问题：
**把 COLOR RULES / FORBIDDEN 等元指令直接塞给生图模型，会被渲染成画面乱码**
（实测出现 "GROUND Verified Knowledg"、"LAYOLE"、"BED increase arrowss"）。

因此拆成两层：

```
知识库条目 + 文字分析
        │
        ▼
[规划层] LLM（GLM）遵守 Nature 规范，输出结构化 JSON 规划
        │   { layout, modules:[{name,desc}], flow }
        ▼
[渲染层] build_render_prompt 组装纯描述提示词（无元指令）
        │
        ▼
     商汤 sensenova-u1-fast 出图
```

- **规划层**：`planner.py` 调 LLM 生成结构化规划，并校验模块形状、数量和短标签；失败时回退到启发式布局 + 规则建议骨架，不保证出图成功或忠实复现原文。
- **渲染层**：`prompt_engine.py` 的 `build_render_prompt` 只含可渲染内容（标题 + 模块标签 + 布局 + 视觉风格 + 一句话配色），**绝不包含** "COLOR RULES"/"FORBIDDEN"/"MUST FOLLOW" 等元指令，也不把知识库原文塞进画面（避免被当正文渲染）。

## 文件说明

| 文件 | 职责 |
|---|---|
| `app/services/diagram/prompt_engine.py` | 纯字符串组装：布局模式、配色、视觉风格、渲染提示词、规划层提示词模板 |
| `app/services/diagram/planner.py` | LLM 规划 + 降级兜底，对外暴露 `build_prompt_for_route` |
| `app/services/diagram/route_planner.py` | 科研阶段路线规划：LLM 生成 5 阶段路线（选题→调研→方法→实验→论文），输出结构化阶段 + roadmap 渲染提示词；内置**时效性/幻觉防线**（每阶段 evidence_status 标注 + 整体 evidence_summary） |
| `app/services/diagram/paper_analyzer.py` | 论文分析图：把知识库/论文内容转成「Question→Method→Contribution→Evidence」结构图（LLM 规划 + 兜底骨架） |
| `app/services/diagram/route_pipeline.py` | 三步分析流水线（文字→架构图→路线图）异步生成器，供 REST 端点与 SSE 流式端点复用 |
| `app/api/v1/knowledge.py` | 研究路线端点：`POST /routes/{id}/ai-generate`（REST）+ `POST /routes/{id}/ai-generate-stream`（SSE 流式进度） |
| `docs/diagram-skill/nature-figure-prompts/` | 上游参考资产（Aryous/nature-figure-prompts, MIT），论文分析/布局/配色/词汇规范 |
| `docs/diagram-skill/research-planning-architect/` | 上游参考资产（clear0215/research-planning-architect），分层实验矩阵/新颖性诊断/决策门思想 |

## SSE 流式进度

`POST /api/v1/knowledge/routes/{route_id}/ai-generate-stream` 返回 `text/event-stream`，
逐步推送 文字分析 → 架构图 → 科研阶段路线图 的进度事件（含 evidence 防线报告）：

```
event: stage   data: {"event":"stage","stage":"analysis","progress":30,...}
event: done    data: {"event":"done","progress":100,"data":{...RouteResponse}}
event: error   data: {"event":"error","message":"..."}
```

## 时效性 / 幻觉防线

规划器给每个科研阶段标注 `evidence_status`（grounded / partial / unverified）：

- **grounded**：阶段任务有知识库条目直接支撑（如"模型设计"被知识项 1-2 支持）
- **partial**：部分支撑，需补充文献验证
- **unverified**：纯规划动作（文献检索、论文撰写等），知识库未覆盖，明确提示需自行验证

整体 `evidence_summary` 汇总各阶段覆盖情况；LLM 未标注时由 `_annotate_evidence_status`
规则兜底（检测知识库是否含证据性内容：实验/结果/数据/验证等信号词）。
防线信息只进**文字版路线**与事件推送，**不进渲染提示词**（避免污染画面）。

## 布局模式

| key | 名称 | 适用 |
|---|---|---|
| `pipeline` | 水平流水线 | 端到端方法 / 系统架构 / 数据流 |
| `hierarchy` | 分层堆叠 | 算法栈 / 框架分层 |
| `radial` | 放射中心 | 核心方法与多数据/多任务交互 |
| `comparison` | 并排对比 | 消融实验 / 基线对照 |
| `roadmap` | 科研阶段路线图 | 选题→调研→方法→实验→论文的 5 阶段横向路线 |

布局由 LLM 规划选择，LLM 失败时按标题/分析文本关键词启发式选择。

## 配色（期刊投稿风格）

- 主模块：藏青 `#1F3A5F`
- 方法强调：青绿 `#2A9D8F`
- 关键高亮：克制金 `#C9A227`
- 背景：米白 `#FAFAFA`

## 质量检查

2026-09-19 源码改进：不再为了视觉复杂度要求模型补出注意力、奖励模块、公式或反馈箭头。内部组件可以为空；过长或形状错误的规划整份回退，不静默截断为看似合格的内容。来源中不存在的公式仍不进入规划结果。

这只是格式与提示词约束，不是语义真实性验证。规则回退、另一次 `architecture_judge` 结构提炼，以及缺少显式连接边与来源映射仍有限制。SVG 与生图使用统一经确认结构是下一阶段目标。

离线回归：新增 `test_diagram_planner.py` 48 项、原有 `test_diagram_pipeline.py` 14 项均通过；没有据此宣称真实生图效果达到某个模型水平。

- 模块标签为短英文（2-3 词），降低生图文字乱码率
- 不注入编造的数值/指标
- 渲染提示词不含元指令（自动化测试断言）
- 知识库条目为内容 ground truth

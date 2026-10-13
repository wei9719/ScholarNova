import type { KnowledgeItem, StudyDraft, StudySection } from '@/api/types'

export const studySections: Array<{ key: StudySection; zh: string; en: string; hint: string; hintEn: string }> = [
  { key: 'overview', zh: '论文思路', en: 'Understanding', hint: '研究问题、已有方法、本文改进、证据与局限；把你赞同和存疑的地方写在个人想法中。', hintEn: 'Question, prior work, contribution, evidence and limitations. Keep your interpretation separate.' },
  { key: 'experiments', zh: '实验复现', en: 'Reproduction', hint: '依次记录环境版本、硬件、数据划分、预处理、基线、参数、随机种子、运行命令、指标和实际日志。未跑过的步骤写成计划。', hintEn: 'Track environment, hardware, data splits, preprocessing, baselines, parameters, seeds, commands, metrics and actual logs. Plans are not results.' },
  { key: 'resources', zh: '源码、数据与联系', en: 'Code, data & contact', hint: '从已读材料提取网址与公开邮箱，并保留出处。材料里出现不等于官方或可访问；执行源码前检查许可、依赖和下载内容。', hintEn: 'Extract URLs and public email addresses from available material. Check provenance, availability and licenses before use.' },
  { key: 'math', zh: '公式与推导', en: 'Math & derivations', hint: '保留公式编号、原文位置、符号定义、维度、假设和逐步变形。可用 $...$ 或 $$...$$ 保存 LaTeX 源码；本编辑器暂不编译公式。', hintEn: 'Keep equation IDs, source locations, symbols, dimensions, assumptions and derivation steps. LaTeX source is preserved, not compiled here.' },
  { key: 'prerequisites', zh: '前置知识', en: 'Prerequisites', hint: '按“概念是什么 → 本文哪里用到 → 学习资料 → 最小练习 → 是否掌握”整理，常用内容可提炼为基础知识卡。', hintEn: 'Define the concept, its role in this paper, learning resources, a small exercise and your understanding. Reuse it as a foundation card.' },
  { key: 'writing', zh: '论文写作与伪代码', en: 'Writing & pseudocode', hint: '梳理摘要、引言、相关工作、方法、实验、讨论、结论的论证任务。伪代码先写输入、输出、步骤和停止条件；未完成实验不写成已有结果。', hintEn: 'Outline abstract, introduction, related work, method, experiments, discussion and conclusion. Specify pseudocode inputs, outputs, steps and stopping conditions.' },
  { key: 'figures', zh: '数据图与结构图', en: 'Figures & diagrams', hint: '数据图必须使用实际数据并记录单位、样本和误差；模型图/架构图先列模块、输入输出与连线含义，再复用研究路线的制图入口。', hintEn: 'Data figures require actual data, units, samples and uncertainty. Define diagram modules, inputs, outputs and edges before using route-diagram tools.' },
  { key: 'translation', zh: '中英术语与表达', en: 'Bilingual writing', hint: '建立术语对照表，保留公式、变量名、指标、引用和语气强度。逐段比较原文与译文，避免把“可能”翻成“证明”。', hintEn: 'Keep a glossary; preserve equations, variables, metrics, citations and certainty. Compare the original and translated passages.' },
  { key: 'formatting', zh: 'LaTeX 与 Word 排版', en: 'LaTeX & Word', hint: '先取目标期刊官方模板。LaTeX 关注编译器、参考文献和图表路径；Word 使用标题样式、题注、交叉引用与参考文献工具。本页提供建议，不代替排版软件。', hintEn: 'Start from the journal template. Track compiler, references and figure paths in LaTeX; use styles, captions and cross-references in Word. This is guidance, not a document compiler.' },
]

export interface StudyEntry { paper_notes: string; ai_suggestions: string; user_notes: string }
export interface ReadingNote extends Record<string, unknown> {
  schema_version: 1
  status: 'to_read' | 'reading' | 'reproducing' | 'reviewed'
  source_query: string
  sections: Partial<Record<StudySection, StudyEntry>>
}
export const emptyEntry = (): StudyEntry => ({ paper_notes: '', ai_suggestions: '', user_notes: '' })
export function readingNote(data?: Record<string, unknown> | null): ReadingNote {
  const sections: ReadingNote['sections'] = {}
  const raw = data?.sections as Record<string, Partial<StudyEntry>> | undefined
  for (const { key } of studySections) {
    const item = raw?.[key]
    if (item && typeof item === 'object') sections[key] = {
      paper_notes: typeof item.paper_notes === 'string' ? item.paper_notes : '',
      ai_suggestions: typeof item.ai_suggestions === 'string' ? item.ai_suggestions : '',
      user_notes: typeof item.user_notes === 'string' ? item.user_notes : '',
    }
  }
  return { schema_version: 1, status: ['to_read', 'reading', 'reproducing', 'reviewed'].includes(String(data?.status)) ? data!.status as ReadingNote['status'] : 'to_read', source_query: typeof data?.source_query === 'string' ? data.source_query : '', sections }
}

export function draftWithSources(draft: StudyDraft): string {
  const locations = draft.sources.map(source => `[${source.label}] ${source.section}${source.page ? `（章节起始页 ${source.page}，非精确定位）` : ''}\n${source.excerpt}\n片段 ${source.id} / ${source.content_hash}`)
  return [draft.content, `材料范围：${draft.material_scope === 'abstract' ? '仅摘要' : '本地全文节选'}；${draft.section === 'resources' ? '本地材料提取，未调用模型' : draft.model_completed ? 'AI 建议，待核对' : '模型草稿未完成'}`, ...draft.warnings, locations.length ? `来源快照\n${locations.join('\n\n')}` : ''].filter(Boolean).join('\n\n')
}

export function renderReadingNote(note: ReadingNote): string {
  return studySections.flatMap(section => {
    const item = note.sections[section.key]
    if (!item) return []
    return [`## ${section.zh}`, item.paper_notes && `### 论文材料摘录与归纳（未独立核验）\n${item.paper_notes}`, item.ai_suggestions && `### AI 建议（非论文事实）\n${item.ai_suggestions}`, item.user_notes && `### 个人想法（非论文事实）\n${item.user_notes}`].filter(Boolean)
  }).join('\n\n')
}

export function exportKnowledge(item: KnowledgeItem): string {
  return [`# ${item.title}`, `分类：${item.category}`, `类型：${item.card_type || '普通知识条目'}`, `更新时间：${item.updated_at}`, item.source_paper_title ? `来源论文：${item.source_paper_title}` : '', item.source_paper_doi ? `DOI：${item.source_paper_doi}` : '', item.content, item.notes ? `## 个人备注（非论文事实）\n${item.notes}` : ''].filter(Boolean).join('\n\n')
}

export function downloadKnowledge(item: KnowledgeItem) {
  const url = URL.createObjectURL(new Blob([exportKnowledge(item)], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url; link.download = `${item.title.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 90) || 'research-note'}.md`
  link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

import { useState } from 'react'
import {
  X, Edit2, Trash2, ExternalLink, Tag, FileText,
  BookOpen, Clock, StickyNote, Loader2, FileDown,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { useLocaleStore } from '@/stores/localeStore'
import { knowledgeApi } from '@/api/client'
import type { KnowledgeItem } from '@/api/types'
import { downloadKnowledge, readingNote, renderReadingNote, studySections, type StudyEntry } from '@/utils/studyNote'
import './KnowledgeDetail.css'

interface KnowledgeDetailProps {
  item: KnowledgeItem
  onClose: () => void
  onEdit: (item: KnowledgeItem) => void
  onDelete: (id: string) => void
  onDerive?: (prefill: Partial<KnowledgeItem>) => void
  onReadPaper?: (item: KnowledgeItem) => void
  onDiscuss?: (item: KnowledgeItem) => void
}

export default function KnowledgeDetail({
  item, onClose, onEdit, onDelete, onDerive, onReadPaper, onDiscuss,
}: KnowledgeDetailProps) {
  const { t, locale } = useLocaleStore()
  const isChinese = locale === 'zh'
  const [deleting, setDeleting] = useState(false)
  const notebook = item.card_type === 'reading_note' ? readingNote(item.card_data) : null
  const statuses = { to_read: isChinese ? '待阅读' : 'To read', reading: isChinese ? '阅读中' : 'Reading', reproducing: isChinese ? '复现中' : 'Reproducing', reviewed: isChinese ? '已复盘' : 'Reviewed' }
  const sectionFields: Array<{ key: keyof StudyEntry; label: string }> = [
    { key: 'paper_notes', label: isChinese ? '论文材料摘录/用户整理，未独立核验' : 'Paper excerpts / user summary — not independently verified' },
    { key: 'ai_suggestions', label: isChinese ? 'AI建议，非论文事实' : 'AI suggestions — not paper facts' },
    { key: 'user_notes', label: isChinese ? '个人想法，非论文事实' : 'Personal ideas — not paper facts' },
  ]
  const derivedContent = (type: 'foundation' | 'skill') => {
    if (!notebook) return ''
    const keys = type === 'foundation' ? ['prerequisites', 'math'] : ['experiments', 'figures', 'writing', 'formatting']
    return renderReadingNote({ ...notebook, sections: Object.fromEntries(
      Object.entries(notebook.sections).filter(([key, entry]) => keys.includes(key)
        && entry && Object.values(entry).some((text) => text.trim())),
    ) })
  }
  const derive = (type: 'foundation' | 'skill') => {
    const content = derivedContent(type)
    if (!content || !onDerive) return
    const label = type === 'foundation' ? (isChinese ? '基础知识' : 'Foundation') : (isChinese ? '技能卡' : 'Skill')
    onDerive({
      title: `${label} · ${item.title}`, category: item.category, card_type: type,
      content, source_paper_id: item.source_paper_id, source_paper_title: item.source_paper_title,
      source_paper_doi: item.source_paper_doi, tags: [...item.tags], research_points: [],
      notes: isChinese
        ? `提炼自阅读笔记：${item.title}\n原笔记 ID：${item.id}\n待人工整理与核验；提炼不代表已掌握或已完成复现。`
        : `Derived from reading note: ${item.title}\nOriginal note ID: ${item.id}\nReview this draft; extraction does not establish mastery or successful reproduction.`,
    })
  }

  const handleDelete = async () => {
    if (!window.confirm(t('knowledge.deleteConfirm'))) return
    setDeleting(true)
    try {
      await knowledgeApi.delete(item.id)
      toast.success(t('knowledge.deleteSuccess'))
      onDelete(item.id)
      onClose()
    } catch {
      toast.error(t('knowledge.deleteError'))
    } finally {
      setDeleting(false)
    }
  }

  const renderAnalysisContent = (text: string) => {
    const sections = text.split(/\n(?=\d+[\.\)、]|#{1,3}\s|\*\*[^*]+\*\*)/g).filter(Boolean)
    return sections.map((section, i) => {
      const headingMatch = section.match(/^(?:\d+[\.\)、]|#{1,3}\s|\*\*)(.+?)(?:\*\*|$)/m)
      const heading = headingMatch ? headingMatch[1].replace(/\*\*/g, '').trim() : null
      const body = heading ? section.slice(section.indexOf(heading) + heading.length).trim() : section.trim()
      return (
        <div key={i} className="mb-3">
          {heading && <h5 className="text-xs font-bold text-primary-600 dark:text-primary-400 mb-1">{heading}</h5>}
          <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-line">{body}</p>
        </div>
      )
    })
  }

  return (
    <div className="knowledge-detail animate-fade-in">
      {/* Header */}
      <div className="knowledge-detail-header">
        <div className="flex-1 min-w-0">
          <h2 className="knowledge-detail-title">{item.title}</h2>
          <div className="flex items-center gap-2 mt-1">
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400">
              <FileText className="w-3 h-3" />
              {item.category || (isChinese ? '未分类' : 'Uncategorized')}
            </span>
          </div>
        </div>
        <button onClick={onClose} aria-label={isChinese ? '关闭详情' : 'Close details'} className="knowledge-detail-close">
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Meta */}
      <div className="knowledge-detail-meta">
        {notebook && <div className="meta-row">{isChinese ? '阅读进度：' : 'Reading progress: '}{statuses[notebook.status]}</div>}
        <div className="meta-row">
          <Clock className="w-4 h-4" />
          <span>{isChinese ? '创建: ' : 'Created: '}{new Date(item.created_at).toLocaleDateString()}</span>
        </div>
        {item.updated_at !== item.created_at && (
          <div className="meta-row">
            <Clock className="w-4 h-4" />
            <span>{isChinese ? '更新: ' : 'Updated: '}{new Date(item.updated_at).toLocaleDateString()}</span>
          </div>
        )}
      </div>

      {(onReadPaper && item.source_paper_id || onDiscuss && item.category?.trim()) && <div className="knowledge-detail-section">
        <h4 className="knowledge-detail-section-title">{isChinese ? '继续研究' : 'Continue research'}</h4>
        <div className="flex flex-wrap gap-2">
          {onReadPaper && item.source_paper_id && <button onClick={() => onReadPaper(item)} className="knowledge-action-btn knowledge-action-route">
            <BookOpen className="w-4 h-4" />{isChinese ? '继续阅读原文' : 'Continue reading the paper'}
          </button>}
          {onDiscuss && item.category?.trim() && <button onClick={() => onDiscuss(item)} className="knowledge-action-btn knowledge-action-route">
            {isChinese ? '在此分类中提问' : 'Ask within this category'}
          </button>}
        </div>
        <p className="mt-2 text-xs text-gray-500">{isChinese ? '沿用本条笔记的论文和研究分类；提问会新建独立对话，不自动发送问题。' : 'Keeps this note’s paper and category. Asking opens a separate conversation without sending a question.'}</p>
      </div>}

      {/* Source Paper */}
      {(item.source_paper_title || item.source_paper_doi) && (
        <div className="knowledge-detail-section">
          <h4 className="knowledge-detail-section-title">
            <BookOpen className="w-4 h-4" />
            {t('knowledge.sourcePaper')}
          </h4>
          <div className="knowledge-source-paper">
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{item.source_paper_title}</p>
            {item.source_paper_doi && (
              <a
                href={`https://doi.org/${item.source_paper_doi}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary-600 dark:text-primary-400 hover:underline mt-1"
              >
                <ExternalLink className="w-3 h-3" />
                {item.source_paper_doi}
              </a>
            )}
          </div>
        </div>
      )}

      {/* Content */}
      <div className="knowledge-detail-content custom-scrollbar">
        {notebook ? (
          <>
            <div className="knowledge-detail-section text-xs text-gray-500 dark:text-gray-400">
              <p>{isChinese ? '进度仅记录个人学习状态，不代表实验或论文结论已验证。公式和代码按原文展示，本页不编译或执行。' : 'Progress tracks personal study, not verified results. Equations and code are shown as text, not compiled or executed.'}</p>
              {!item.source_paper_id && <p className="mt-2">{isChinese ? '未关联本地论文记录，可继续整理手写笔记。' : 'No local paper is linked; you can keep handwritten notes.'}</p>}
              {notebook.source_query && <p className="mt-2 study-note-text">{isChinese ? '来源检索问题：' : 'Source search question: '}{notebook.source_query}</p>}
            </div>
            {studySections.map((section) => {
              const entry = notebook.sections[section.key]
              return <section key={section.key} aria-label={isChinese ? section.zh : section.en} className="knowledge-detail-section study-note-section">
                <h3 className="knowledge-detail-section-title">{isChinese ? section.zh : section.en}</h3>
                {entry && sectionFields.some(({ key }) => entry[key].trim()) ? sectionFields.map(({ key, label }) => entry[key].trim() && (
                  <div key={key} className={`study-note-field study-note-field-${key}`}>
                    <h4>{label}</h4>
                    <p className="study-note-text">{entry[key]}</p>
                  </div>
                )) : <p className="text-xs text-gray-400">{isChinese ? '尚未记录' : 'No notes yet'}</p>}
              </section>
            })}
            {onDerive && <div className="knowledge-detail-section">
              <p className="text-xs text-gray-500 mb-2">{isChinese ? '将相关分区提炼为可编辑草稿，确认后再保存。空白分区请先补充。' : 'Extract relevant sections into an editable draft, then review and save. Fill empty sections first.'}</p>
              <div className="flex flex-wrap gap-2">
                <button className="knowledge-action-btn knowledge-action-route" disabled={!derivedContent('foundation')} onClick={() => derive('foundation')}>{isChinese ? '提炼基础知识' : 'Extract foundation'}</button>
                <button className="knowledge-action-btn knowledge-action-route" disabled={!derivedContent('skill')} onClick={() => derive('skill')}>{isChinese ? '提炼技能卡' : 'Extract skill'}</button>
              </div>
            </div>}
          </>
        ) : <div className="knowledge-detail-section">
          <h4 className="knowledge-detail-section-title">
            <FileText className="w-4 h-4" />
            {t('knowledge.content')}
          </h4>
          <div className="knowledge-content-body">
            {renderAnalysisContent(item.content)}
          </div>
        </div>}

        {/* Research Points */}
        {item.research_points.length > 0 && (
          <div className="knowledge-detail-section">
            <h4 className="knowledge-detail-section-title">
              <StickyNote className="w-4 h-4" />
              {t('knowledge.researchPoints')}
            </h4>
            <ul className="space-y-1">
              {item.research_points.map((point, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
                  <span className="text-primary-500 mt-0.5">&#x2022;</span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Tags */}
        {item.tags.length > 0 && (
          <div className="knowledge-detail-section">
            <h4 className="knowledge-detail-section-title">
              <Tag className="w-4 h-4" />
              {t('knowledge.tags')}
            </h4>
            <div className="flex flex-wrap gap-1.5">
              {item.tags.map((tag, i) => (
                <span key={i} className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300">
                  #{tag}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Notes */}
        {item.notes && (
          <div className="knowledge-detail-section">
            <h4 className="knowledge-detail-section-title">
              <StickyNote className="w-4 h-4" />
              {t('knowledge.notes')}
            </h4>
            <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-line bg-yellow-50 dark:bg-yellow-900/10 p-3 rounded-md border border-yellow-200 dark:border-yellow-800">
              {item.notes}
            </p>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="knowledge-detail-actions">
        <button onClick={() => {
          try { downloadKnowledge(item) }
          catch { toast.error(isChinese ? '导出失败，请重试' : 'Export failed. Please retry.') }
        }} className="knowledge-action-btn knowledge-action-route">
          <FileDown className="w-4 h-4" />{isChinese ? '导出 Markdown' : 'Export Markdown'}
        </button>
        <button
          onClick={() => onEdit(item)}
          className="knowledge-action-btn knowledge-action-edit"
        >
          <Edit2 className="w-4 h-4" />
          {t('knowledge.edit')}
        </button>
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="knowledge-action-btn knowledge-action-delete"
        >
          {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
          {t('knowledge.delete')}
        </button>
      </div>
    </div>
  )
}

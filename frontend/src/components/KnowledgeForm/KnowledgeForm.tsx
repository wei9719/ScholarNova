import { useState, useEffect, useRef } from 'react'
import {
  X, Loader2, Plus, Tag, FileText, BookOpen,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { useLocaleStore } from '@/stores/localeStore'
import { knowledgeApi } from '@/api/client'
import type { KnowledgeItem, KnowledgeCreateRequest } from '@/api/types'
import { readingNote, renderReadingNote, type ReadingNote } from '@/utils/studyNote'
import StudyNoteEditor from './StudyNoteEditor'
import { safeErrorMessage } from '@/utils/safeError'
import './KnowledgeForm.css'

interface KnowledgeFormProps {
  open: boolean
  editingItem: KnowledgeItem | null
  prefill: Partial<KnowledgeItem> | null
  categories: { name: string; count: number }[]
  onClose: () => void
  onSuccess: () => void
}

export default function KnowledgeForm({
  open, editingItem, prefill, categories, onClose, onSuccess,
}: KnowledgeFormProps) {
  const { t, locale } = useLocaleStore()
  const isChinese = locale === 'zh'

  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('')
  const [newCategory, setNewCategory] = useState('')
  const [content, setContent] = useState('')
  const [sourcePaperTitle, setSourcePaperTitle] = useState('')
  const [sourcePaperDoi, setSourcePaperDoi] = useState('')
  const [researchPoints, setResearchPoints] = useState<string[]>([])
  const [researchPointInput, setResearchPointInput] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [showNewCategory, setShowNewCategory] = useState(false)
  const [cardType, setCardType] = useState('')
  const [notebook, setNotebook] = useState<ReadingNote>(() => readingNote())
  const [autoPolish, setAutoPolish] = useState(false)
  const session = useRef<string | null>(null)
  const initial = useRef('')
  const snapshot = () => JSON.stringify({ title, category, newCategory, content, sourcePaperTitle, sourcePaperDoi, researchPoints, tags, notes, cardType, notebook })

  // 从分析内容自动提取研究点和标签
  const extractFromContent = (text: string) => {
    if (!text) return { points: [] as string[], tags: [] as string[] }

    const points: string[] = []
    const tagSet = new Set<string>()

    // 提取研究点：匹配 "1. xxx" 或 "（1）xxx" 或 "- xxx" 格式
    const pointPatterns = [
      /(?:^|\n)\s*\d+[\.\)、]\s*(.+)/gm,
      /(?:^|\n)\s*[（(]\d+[)）]\s*(.+)/gm,
      /(?:^|\n)\s*[-•]\s*(.+)/gm,
      /(?:创新点|贡献|方法|发现|结论)[：:]\s*(.+)/g,
    ]
    for (const pattern of pointPatterns) {
      let match
      while ((match = pattern.exec(text)) !== null) {
        const point = match[1].trim().replace(/\*\*/g, '').slice(0, 80)
        if (point.length > 5 && !points.includes(point)) {
          points.push(point)
        }
      }
    }

    // 提取标签：技术术语
    const techTerms = [
      '联邦学习', '差分隐私', 'Transformer', 'GNN', 'CNN', 'RNN', 'LSTM',
      '注意力机制', '图神经网络', '强化学习', '迁移学习', '对比学习',
      '自监督', '预训练', '微调', '知识蒸馏', '模型压缩',
      '交通流', '预测', '时空', '图卷积', '自注意力',
      'federated learning', 'differential privacy', 'attention mechanism',
      'graph neural network', 'reinforcement learning', 'transfer learning',
      'traffic flow', 'prediction', 'spatio-temporal', 'transformer',
    ]
    const textLower = text.toLowerCase()
    for (const term of techTerms) {
      if (textLower.includes(term.toLowerCase())) {
        tagSet.add(term)
      }
    }

    // 从标题提取标签
    return { points: points.slice(0, 8), tags: Array.from(tagSet).slice(0, 10) }
  }

  useEffect(() => {
    if (!open) { session.current = null; return }
    const key = editingItem?.id || prefill?.source_paper_id || prefill?.title || 'new'
    if (session.current === key) return
    session.current = key
    const item = editingItem || prefill
    const extracted = !editingItem && !prefill?.research_points?.length && prefill?.content ? extractFromContent(prefill.content) : null
    const values = {
      title: item?.title || '', category: item?.category || '', newCategory: '', content: item?.content || '',
      sourcePaperTitle: item?.source_paper_title || '', sourcePaperDoi: item?.source_paper_doi || '',
      researchPoints: extracted?.points || item?.research_points || [], tags: extracted?.tags || item?.tags || [],
      notes: item?.notes || '', cardType: item?.card_type || '', notebook: readingNote(item?.card_data),
    }
    setCardType(values.cardType)
    setNotebook(values.notebook)
    setAutoPolish(false)
    setTitle(values.title); setCategory(values.category); setContent(values.content)
    setSourcePaperTitle(values.sourcePaperTitle); setSourcePaperDoi(values.sourcePaperDoi)
    setResearchPoints(values.researchPoints); setTags(values.tags); setNotes(values.notes)
    setShowNewCategory(false); setNewCategory(''); setResearchPointInput(''); setTagInput('')
    initial.current = JSON.stringify(values)
  }, [editingItem, prefill, open])
  const handleClose = () => {
    if (saving) return
    if (initial.current && snapshot() !== initial.current && !window.confirm(isChinese ? '笔记尚未保存，确定放弃本次修改？' : 'Discard unsaved changes?')) return
    onClose()
  }

  const confirmNewCategory = () => {
    const name = newCategory.trim()
    if (!name) {
      toast.error(isChinese ? '请输入分类名称' : 'Please enter a category name')
      return
    }
    setCategory(name)
    setShowNewCategory(false)
    setNewCategory('')
  }

  const handleResearchPointKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const val = researchPointInput.trim()
      if (val && !researchPoints.includes(val)) {
        setResearchPoints([...researchPoints, val])
        setResearchPointInput('')
      }
    }
  }

  const handleTagKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const val = tagInput.trim()
      if (val && !tags.includes(val)) {
        setTags([...tags, val])
        setTagInput('')
      }
    }
  }

  const removeResearchPoint = (idx: number) => {
    setResearchPoints(researchPoints.filter((_, i) => i !== idx))
  }

  const removeTag = (idx: number) => {
    setTags(tags.filter((_, i) => i !== idx))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) {
      toast.error(isChinese ? '请输入标题' : 'Please enter a title')
      return
    }

    const finalCategory = (showNewCategory ? newCategory : category).trim()
    if (!finalCategory) {
      toast.error(isChinese ? '请选择或输入分类' : 'Please select or enter a category')
      return
    }

    setSaving(true)
    try {
      const data: KnowledgeCreateRequest = {
        title: title.trim(),
        category: finalCategory,
        content: content.trim(),
        source_paper_title: sourcePaperTitle.trim() || null,
        source_paper_doi: sourcePaperDoi.trim() || null,
        source_paper_id: editingItem?.source_paper_id || prefill?.source_paper_id || null,
        research_points: researchPoints,
        tags,
        notes: notes.trim() || null,
        auto_polish: cardType !== 'reading_note' && autoPolish && !editingItem,
        card_type: cardType || null,
        ...(cardType === 'reading_note' ? { card_data: notebook } : editingItem?.card_type === cardType ? { card_data: editingItem.card_data } : {}),
      }

      if (editingItem) {
        await knowledgeApi.update(editingItem.id, { ...data, expected_updated_at: editingItem.updated_at })
      } else {
        await knowledgeApi.create(data)
      }
      toast.success(t('knowledge.saveSuccess'))
      onSuccess()
      onClose()
    } catch (error) {
      toast.error(safeErrorMessage(error, t('knowledge.saveError')))
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null

  return (
    <div className="knowledge-form-overlay" onClick={handleClose}>
      <div role="dialog" aria-label={isChinese ? '编辑知识与笔记' : 'Edit knowledge and notes'} className={`knowledge-form animate-fade-in ${cardType === 'reading_note' ? 'study-form' : ''}`} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="knowledge-form-header">
          <h2 className="knowledge-form-title">
            {editingItem ? t('knowledge.editTitle') : t('knowledge.createTitle')}
          </h2>
          <button onClick={handleClose} aria-label={isChinese ? '关闭编辑器' : 'Close editor'} disabled={saving} className="knowledge-form-close">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="knowledge-form-body custom-scrollbar">
          <fieldset disabled={saving}>
          <div className="form-metadata">
          <div className="knowledge-form-field">
            <label className="knowledge-form-label" htmlFor="knowledge-kind">{isChinese ? '资料类型' : 'Entry type'}</label>
            <select id="knowledge-kind" value={cardType} className="knowledge-form-input" onChange={e => {
              const next = e.target.value
              if (cardType === 'reading_note' && next !== 'reading_note') setContent(renderReadingNote(notebook))
              if (next === 'reading_note' && cardType !== 'reading_note' && content && content !== renderReadingNote(notebook)) {
                const previous = notebook.sections.overview || { paper_notes: '', ai_suggestions: '', user_notes: '' }
                const userNotes = [previous.user_notes, content].filter(Boolean).join('\n\n--- 普通正文转入的个人记录 ---\n\n')
                if (userNotes.length > 20000) { toast.error(isChinese ? '普通正文过长，请先保存或导出后再转为阅读笔记，避免丢失内容。' : 'Content is too long to convert safely. Save or export it first.'); return }
                setNotebook({ ...notebook, sections: { ...notebook.sections, overview: { ...previous, user_notes: userNotes } } })
              }
              setCardType(next)
            }}>
              <option value="">{isChinese ? '普通知识条目' : 'Knowledge entry'}</option>
              <option value="reading_note">{isChinese ? '阅读笔记' : 'Reading note'}</option>
              <option value="foundation">{isChinese ? '基础知识卡' : 'Foundation card'}</option>
              <option value="skill">{isChinese ? '技能卡' : 'Skill card'}</option>
              {cardType && !['reading_note', 'foundation', 'skill'].includes(cardType) && <option value={cardType}>{cardType}</option>}
            </select>
            <p className="knowledge-form-hint">{isChinese ? '分类表示研究方向；类型表示资料用途。它们共用同一个知识库。' : 'Categories organize research topics; types describe how entries are used. All remain in one library.'}</p>
          </div>
          {/* Title */}
          <div className="knowledge-form-field form-title-field">
            <label className="knowledge-form-label">
              <FileText className="w-4 h-4" />
              {t('knowledge.titleField')} *
            </label>
            <input
              type="text"
              aria-label={isChinese ? '笔记标题' : 'Entry title'}
              maxLength={500}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t('knowledge.titlePlaceholder')}
              className="knowledge-form-input"
            />
          </div>

          {/* Category */}
          <div className="knowledge-form-field form-category-field">
            <label className="knowledge-form-label">
              <FileText className="w-4 h-4" />
              {t('knowledge.category')} *
            </label>
            {!showNewCategory ? (
              <div className="flex gap-2">
                <select
                  aria-label={isChinese ? '研究分类' : 'Research category'}
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className="knowledge-form-input flex-1"
                >
                  <option value="">{t('knowledge.categorySelect')}</option>
                  {category && !categories.some(cat => cat.name === category) && <option value={category}>{category}</option>}
                  {categories.map((cat) => (
                    <option key={cat.name} value={cat.name}>{cat.name} ({cat.count})</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setShowNewCategory(true)}
                  className="knowledge-form-add-btn"
                  title={t('knowledge.categoryNew')}
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <div className="flex gap-2">
                <input
                  type="text"
                  aria-label={isChinese ? '新分类名称' : 'New category name'}
                  maxLength={100}
                  value={newCategory}
                  onChange={(e) => setNewCategory(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); confirmNewCategory() }
                  }}
                  placeholder={t('knowledge.categoryNewPlaceholder')}
                  className="knowledge-form-input flex-1"
                />
                <button
                  type="button"
                  onClick={confirmNewCategory}
                  className="knowledge-form-submit-btn"
                >
                  {t('common.confirm')}
                </button>
                <button
                  type="button"
                  onClick={() => { setShowNewCategory(false); setNewCategory('') }}
                  className="knowledge-form-cancel-btn"
                >
                  {t('common.cancel')}
                </button>
              </div>
            )}
          </div>

          {/* Source Paper */}
          <details className="knowledge-form-field form-source-field" open={cardType !== 'reading_note'}>
            <summary className="knowledge-form-label cursor-pointer">
              <BookOpen className="w-4 h-4" />
              {t('knowledge.sourcePaper')} {t('knowledge.sourcePaperOptional')}
              {sourcePaperTitle && cardType === 'reading_note' && <span className="truncate font-normal"> · {sourcePaperTitle}</span>}
            </summary>
            <input
              type="text"
              value={sourcePaperTitle}
              onChange={(e) => setSourcePaperTitle(e.target.value)}
              placeholder={t('knowledge.sourcePaperTitle')}
              className="knowledge-form-input"
            />
            <input
              type="text"
              value={sourcePaperDoi}
              onChange={(e) => setSourcePaperDoi(e.target.value)}
              placeholder={t('knowledge.sourcePaperDOI')}
              className="knowledge-form-input mt-2"
            />
          </details>
          </div>

          {/* Content */}
          {cardType === 'reading_note' ? <StudyNoteEditor value={notebook} onChange={setNotebook} paperId={editingItem?.source_paper_id || prefill?.source_paper_id} chinese={isChinese} disabled={saving} /> : <div className="knowledge-form-field">
            <label className="knowledge-form-label">
              <FileText className="w-4 h-4" />
              {t('knowledge.content')}
            </label>
            <textarea
              value={content}
              aria-label={isChinese ? '正文' : 'Content'}
              onChange={(e) => setContent(e.target.value)}
              placeholder={t('knowledge.contentPlaceholder')}
              rows={6}
              className="knowledge-form-textarea"
            />
            {['foundation', 'skill'].includes(cardType) && <p className="knowledge-form-hint">{cardType === 'foundation' ? (isChinese ? '建议记录定义、前置条件、推导、例子、学习资料和自测结果。' : 'Include definitions, prerequisites, derivations, examples, resources and self-checks.') : (isChinese ? '建议记录适用场景、输入输出、依赖、步骤、命令、示例和验收标准。' : 'Include use cases, inputs, outputs, dependencies, steps, commands, examples and checks.')}</p>}
            {!editingItem && <label className="knowledge-form-hint"><input type="checkbox" checked={autoPolish} onChange={e => setAutoPolish(e.target.checked)} /> {isChinese ? '使用 AI 压缩正文（可选，会替换为短摘要；长笔记请勿勾选）' : 'AI condensation (optional; replaces content with a short summary)'}</label>}
          </div>}

          {/* Research Points */}
          <div className="knowledge-form-field">
            <label className="knowledge-form-label">
              <FileText className="w-4 h-4" />
              {t('knowledge.researchPoints')}
            </label>
            {researchPoints.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {researchPoints.map((point, i) => (
                  <span key={i} className="knowledge-tag">
                    {point}
                    <button type="button" onClick={() => removeResearchPoint(i)} className="knowledge-tag-remove">
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <input
                type="text"
                value={researchPointInput}
                onChange={(e) => setResearchPointInput(e.target.value)}
                onKeyDown={handleResearchPointKeyDown}
                placeholder={t('knowledge.researchPointsPlaceholder')}
                className="knowledge-form-input flex-1"
              />
            </div>
            <p className="knowledge-form-hint">{t('knowledge.researchPointsHint')}</p>
          </div>

          {/* Tags */}
          <div className="knowledge-form-field">
            <label className="knowledge-form-label">
              <Tag className="w-4 h-4" />
              {t('knowledge.tags')}
            </label>
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {tags.map((tag, i) => (
                  <span key={i} className="knowledge-tag">
                    #{tag}
                    <button type="button" onClick={() => removeTag(i)} className="knowledge-tag-remove">
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <input
                type="text"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={handleTagKeyDown}
                placeholder={t('knowledge.tagsPlaceholder')}
                className="knowledge-form-input flex-1"
              />
            </div>
            <p className="knowledge-form-hint">{t('knowledge.tagsHint')}</p>
          </div>

          {/* Notes */}
          <div className="knowledge-form-field">
            <label className="knowledge-form-label">
              <FileText className="w-4 h-4" />
              {t('knowledge.notes')}
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={t('knowledge.notesPlaceholder')}
              rows={3}
              className="knowledge-form-textarea"
            />
          </div>
          </fieldset>
        </form>

        {/* Footer */}
        <div className="knowledge-form-footer">
          <button onClick={handleClose} disabled={saving} className="knowledge-form-cancel-btn">
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="knowledge-form-submit-btn"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            {t('knowledge.save')}
          </button>
        </div>
      </div>
    </div>
  )
}

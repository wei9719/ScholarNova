import { useEffect, useRef, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { studyApi } from '@/api/client'
import type { StudyDraft, StudySection } from '@/api/types'
import { safeErrorMessage } from '@/utils/safeError'
import { draftWithSources, emptyEntry, studySections, type ReadingNote } from '@/utils/studyNote'

interface Props { value: ReadingNote; onChange: (value: ReadingNote) => void; paperId?: string | null; chinese: boolean; disabled?: boolean }
export default function StudyNoteEditor({ value, onChange, paperId, chinese, disabled }: Props) {
  const [section, setSection] = useState<StudySection>('overview')
  const [question, setQuestion] = useState('')
  const [draft, setDraft] = useState<StudyDraft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const request = useRef<AbortController | null>(null)
  useEffect(() => {
    request.current?.abort(); request.current = null
    setDraft(null); setBusy(false); setError(''); setQuestion('')
    return () => { request.current?.abort(); request.current = null }
  }, [paperId, section])
  useEffect(() => {
    if (!busy) return
    const started = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [busy])
  const config = studySections.find(item => item.key === section)!
  const entry = value.sections[section] || emptyEntry()
  const change = (field: keyof typeof entry, text: string) => onChange({ ...value, sections: { ...value.sections, [section]: { ...entry, [field]: text } } })
  const generate = async () => {
    if (!paperId || busy) return
    const controller = new AbortController(); request.current = controller
    setBusy(true); setDraft(null); setError(''); setElapsed(0)
    try {
      const { data } = await studyApi.draft(paperId, section, question, controller.signal)
      if (!controller.signal.aborted && request.current === controller) setDraft(data)
    } catch (reason) {
      if (!controller.signal.aborted) setError(safeErrorMessage(reason, chinese ? '本节草稿未完成，已写内容保留。可重试或继续手写。' : 'Draft unavailable. Your notes are unchanged; retry or continue writing.'))
    } finally {
      if (request.current === controller) { setBusy(false); request.current = null }
    }
  }
  return <section aria-label={chinese ? '阅读笔记编辑器' : 'Reading notebook'} className="study-editor">
    <div className="study-toolbar">
      <label>{chinese ? '阅读进度' : 'Reading status'}
        <select aria-label={chinese ? '阅读进度' : 'Reading status'} disabled={disabled} value={value.status} onChange={e => onChange({ ...value, status: e.target.value as ReadingNote['status'] })} className="knowledge-form-input">
          <option value="to_read">{chinese ? '待阅读' : 'To read'}</option><option value="reading">{chinese ? '阅读中' : 'Reading'}</option><option value="reproducing">{chinese ? '复现中' : 'Reproducing'}</option><option value="reviewed">{chinese ? '已整理（个人标记）' : 'Reviewed (personal status)'}</option>
        </select>
      </label>
      {value.source_query && <p>{chinese ? '来自检索：' : 'Search: '}{value.source_query}</p>}
    </div>
    <div className="study-workspace">
      <nav aria-label={chinese ? '笔记章节' : 'Notebook sections'} className="study-outline">
        {studySections.map(item => <button type="button" key={item.key} disabled={disabled} aria-pressed={section === item.key} onClick={() => setSection(item.key)}>{chinese ? item.zh : item.en}{Object.values(value.sections[item.key] || {}).some(Boolean) && <span aria-label={chinese ? '已有记录' : 'Has notes'}> •</span>}</button>)}
      </nav>
      <div className="study-fields">
        <h3>{chinese ? config.zh : config.en}</h3>
        <p className="study-hint">{chinese ? config.hint : config.hintEn}</p>
        <label>{chinese ? '论文材料摘录与归纳（请保留出处）' : 'Paper material and summaries (keep source locations)'}
          <textarea aria-label={chinese ? '论文材料摘录' : 'Paper notes'} value={entry.paper_notes} disabled={disabled} maxLength={20000} onChange={e => change('paper_notes', e.target.value)} rows={5} className="knowledge-form-textarea" />
        </label>
        <label>{chinese ? 'AI 建议（不作为论文事实）' : 'AI suggestions (not paper facts)'}
          <textarea aria-label={chinese ? 'AI 建议' : 'AI suggestions'} value={entry.ai_suggestions} disabled={disabled} maxLength={20000} onChange={e => change('ai_suggestions', e.target.value)} rows={5} className="knowledge-form-textarea" />
        </label>
        <label>{chinese ? '我的想法与实际操作记录' : 'My ideas and actual work'}
          <textarea aria-label={chinese ? '我的想法' : 'My ideas'} value={entry.user_notes} disabled={disabled} maxLength={20000} onChange={e => change('user_notes', e.target.value)} rows={4} className="knowledge-form-textarea" />
        </label>
        <details className="study-assist">
          <summary>{chinese ? '依据论文材料辅助整理本节' : 'Draft this section from paper material'}</summary>
          <p className="study-hint">{section === 'resources'
            ? (chinese ? '仅从本地已读取材料中提取链接和公开邮箱，不调用模型，也不会自动打开链接。' : 'Extract links and public emails locally. No model call or automatic link access.')
            : (chinese ? '将本地已读取的论文节选发送给设置中的“分析”模型，每次只生成本节。草稿不会覆盖笔记，需要你确认后加入。' : 'Send available local paper excerpts to your Analysis model for this section only. Review the draft before appending it.')}</p>
          {!paperId && <p>{chinese ? '请从搜索结果的论文详情创建笔记以关联材料；当前仍可手工记录。' : 'Create a note from a search result to link paper material. Manual editing remains available.'}</p>}
          <label>{chinese ? '本节重点（可选）' : 'Focus (optional)'}<textarea aria-label={chinese ? '本节重点' : 'Section focus'} className="knowledge-form-textarea" maxLength={2000} rows={2} value={question} onChange={e => setQuestion(e.target.value)} disabled={busy || disabled} /></label>
          <div className="study-actions">
            <button type="button" className="knowledge-form-submit-btn" disabled={!paperId || busy || disabled} onClick={generate}>{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}{busy ? `${elapsed}s` : section === 'resources' ? (chinese ? '提取材料中的资源' : 'Extract resources') : (chinese ? '生成本节草稿' : 'Draft this section')}</button>
            {busy && <button type="button" className="knowledge-form-cancel-btn" onClick={() => { request.current?.abort(); request.current = null; setBusy(false) }}>{chinese ? '停止等待' : 'Stop waiting'}</button>}
          </div>
          {error && <p role="alert">{error}</p>}
          {draft && <div className="study-draft" aria-label={chinese ? '待确认草稿' : 'Draft for review'}>
            <p>{chinese ? '材料范围：' : 'Coverage: '}{draft.material_scope === 'abstract' ? (chinese ? '仅摘要' : 'Abstract only') : (chinese ? '全文节选' : 'Full-text excerpts')} · {section === 'resources' ? (chinese ? '本地提取，无模型调用' : 'Local extraction, no model call') : `${draft.provider || '—'}/${draft.model || '—'} · ${draft.total_tokens} Token · ${draft.model_completed ? (chinese ? '草稿已返回，请核对' : 'Draft returned; review required') : (chinese ? '草稿未完成，请查看下方原因' : 'Draft incomplete; see details below')}`}</p>
            {draft.warnings.map((warning, index) => <p key={index} className="study-hint">{warning}</p>)}
            <pre>{draft.content}</pre>
            <details><summary>{chinese ? `来源快照（${draft.sources.length}）` : `Source snapshots (${draft.sources.length})`}</summary>{draft.sources.map(source => <blockquote key={source.label}>[{source.label}] {source.section}{source.page ? ` / ${chinese ? '章节起始页' : 'Section starts at page'} ${source.page}` : ''}<p>{source.excerpt}</p></blockquote>)}</details>
            <button type="button" className="knowledge-form-submit-btn" disabled={disabled || (section !== 'resources' && !draft.model_completed)} onClick={() => {
              const target = section === 'resources' ? 'paper_notes' : 'ai_suggestions'
              const next = [entry[target], draftWithSources(draft)].filter(Boolean).join('\n\n')
              if (next.length > 20000) { setError(chinese ? '合并后超过本栏 20000 字限制，请先导出或精简已有内容。' : 'Combined notes exceed 20,000 characters. Export or shorten this field first.'); return }
              change(target, next); setDraft(null)
            }}>{section === 'resources' ? (chinese ? '确认追加到论文材料栏' : 'Append to paper material') : (chinese ? '确认追加到 AI 建议栏' : 'Append to AI suggestions')}</button>
          </div>}
        </details>
      </div>
    </div>
  </section>
}

import { useEffect, useId, useRef, useState } from 'react'
import { BookOpen, ExternalLink, Loader2, RefreshCw } from 'lucide-react'
import { journalsApi, type JournalDiscoveryResponse, type JournalPapersResponse, type JournalRecommendation } from '@/api/journalsClient'
import type { KnowledgeCreateRequest, Paper } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import { safeErrorMessage } from '@/utils/safeError'
import './JournalDiscovery.css'

function link(value: string | null) {
  if (!value) return null
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? value : null
  } catch { return null }
}

export function journalWritingDraft(journal: JournalRecommendation, query: string, category: string, papers: Paper[] = []): KnowledgeCreateRequest {
  const samples = papers.filter(paper => journal.candidate_paper_ids.includes(paper.id)).slice(0, 12)
  return {
    title: `${journal.name}｜投稿与写作参考`, category: category.trim() || '期刊写作参考',
    card_type: 'skill', auto_polish: false, tags: ['期刊写作参考', journal.name],
    content: [
      `研究关键词：${query}`, `期刊：${journal.name}`, `ISSN：${journal.issns.join(' / ')}`,
      `核验来源：https://openalex.org/${journal.source_id}`, `期刊主页：${journal.homepage_url || '待核对'}`,
      '一、官方作者指南（请从期刊主页核实文章类型、摘要字数、篇幅、图表及参考文献格式，注明访问日期）',
      '待补充。尚未读取作者指南，不能将以下观察当成期刊强制规范。',
      '二、样本文章观察（按同刊、相同文章类型比较，逐篇记录 DOI、年份和原文位置）',
      samples.length ? samples.map(paper => `${paper.title}｜${paper.year || '年份待核对'}｜DOI：${paper.doi || '待补充'}｜来源：${paper.source}`).join('\n') : '当前还没有选定样本，请先查看该刊相关论文。',
      '摘要：背景问题、方法、结果、结论如何组织？',
      '标题与大小标题：研究对象、方法、范围怎样限定？',
      '图表与正文：哪句话引出图表？哪些文字解释结果而不是重复数字？',
      '三、我的可复用提纲与检查清单（不用原文替词冒充个人写作）',
      '待整理。所有观察需区分官方要求、样本规律和个人选择。',
    ].join('\n\n'),
    notes: '由期刊发现页创建的个人技能卡草稿；确认并保存后才进入知识库。',
  }
}

export default function JournalDiscovery({ query, papers, category = '', dateFrom, dateTo, onSelectPaper, onCreateWritingDraft }: {
  query: string
  papers: Paper[]
  category?: string
  dateFrom?: string
  dateTo?: string
  onSelectPaper?: (paper: Paper) => void
  onCreateWritingDraft?: (draft: KnowledgeCreateRequest) => void
}) {
  const isZh = useLocaleStore().locale === 'zh'
  const [result, setResult] = useState<JournalDiscoveryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [started, setStarted] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const listId = useId()
  const numberFormat = new Intl.NumberFormat(isZh ? 'zh-CN' : 'en-US', { maximumFractionDigits: 2 })
  const [selected, setSelected] = useState<JournalRecommendation | null>(null)
  const [related, setRelated] = useState<JournalPapersResponse | null>(null)
  const [paperLoading, setPaperLoading] = useState(false)
  const [paperError, setPaperError] = useState('')
  const relatedController = useRef<AbortController | null>(null)
  const candidatesKey = JSON.stringify(papers.slice(0, 100).map(paper => [paper.id, paper.title, paper.venue]))

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setResult(null); setError(''); setLoading(started)
    setSelected(null); setRelated(null); setPaperError(''); setPaperLoading(false)
    relatedController.current?.abort()
    if (!started || !query.trim()) { setLoading(false); return () => controller.abort() }
    journalsApi.discover(query, papers, controller.signal).then(({ data }) => {
      if (active) { setResult(data); setExpanded(true) }
    }).catch(cause => {
      if (active && !controller.signal.aborted) setError(safeErrorMessage(cause, isZh ? '期刊查询未完成，可以重试。' : 'Journal discovery failed. Retry below.'))
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false; controller.abort(); relatedController.current?.abort() }
    // The metadata signature avoids repeated retrieval on unrelated parent renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, candidatesKey, dateFrom, dateTo, revision, started])

  async function openPapers(journal: JournalRecommendation) {
    relatedController.current?.abort()
    const controller = new AbortController()
    relatedController.current = controller
    setSelected(journal); setRelated(null); setPaperError('')
    if (!journal.source_id || journal.verification !== 'verified_journal') { setPaperLoading(false); return }
    setPaperLoading(true)
    try {
      const { data } = await journalsApi.papers(journal.source_id, query, { date_from: dateFrom, date_to: dateTo }, controller.signal)
      if (!controller.signal.aborted) setRelated(data)
    } catch (cause) {
      if (!controller.signal.aborted) setPaperError(safeErrorMessage(cause, isZh ? '同刊查询未完成，请重试。' : 'Same-journal search failed. Retry.'))
    } finally { if (!controller.signal.aborted) setPaperLoading(false) }
  }

  const currentPapers = selected ? papers.filter(paper => selected.candidate_paper_ids.includes(paper.id)) : []
  return <section className="journal-discovery" aria-label={isZh ? '相关期刊' : 'Related journals'}>
    <header className="journal-discovery__header">
      <h3><BookOpen size={18} />{isZh ? '相关期刊与投稿线索' : 'Journals and submission leads'}</h3>
      <div className="journal-discovery__actions">
        {Boolean(result?.journals.length) && <button type="button" aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(value => !value)}>{expanded ? (isZh ? '收起期刊列表' : 'Collapse journal list') : (isZh ? `展开期刊列表（${result!.journals.length}）` : `Show journal list (${result!.journals.length})`)}</button>}
        <button type="button" disabled={loading || !query.trim()} onClick={() => { setStarted(true); setRevision(value => value + 1) }}><RefreshCw size={14} />{started ? (isZh ? '重新查询' : 'Refresh') : (isZh ? '查询相关期刊' : 'Find related journals')}</button>
      </div>
    </header>
    <p className="journal-discovery__muted">{isZh ? '先看研究方向是否匹配，再核对期刊规范与指标。这里不按影响因子替你决定投稿。' : 'Match research scope first, then verify author guidelines and metrics.'}</p>
    {loading && <p role="status"><Loader2 className="animate-spin" size={16} />{isZh ? '正在核对期刊类型、ISSN 与当前候选来源…' : 'Checking journal type, ISSN and candidate sources…'}</p>}
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p className="journal-discovery__muted">{result.scope}</p>
      <p className="journal-discovery__muted">{isZh ? `当前候选 ${result.candidate_count} 篇；补充查询样本 ${result.discovery_candidate_count} 篇。` : `${result.candidate_count} current candidates; ${result.discovery_candidate_count} discovery samples.`}{result.cached && (isZh ? ' 使用十分钟内缓存。' : ' Cached within ten minutes.')}</p>
      {result.warnings.map((warning, index) => <p key={index} className="journal-discovery__warning">{warning}</p>)}
      {!result.journals.length && <p>{isZh ? '本次尚未发现可核验的期刊。可缩短关键词，或先检索论文再查询。' : 'No journal identified. Refine the keywords or search papers first.'}</p>}
      <div id={listId} className="journal-discovery__list" hidden={!expanded}>{result.journals.map((journal, index) => <article key={journal.source_id || `${journal.name}-${index}`}>
        <div className="journal-discovery__title"><h4>{journal.name}</h4><span className={journal.verification === 'pending' ? 'journal-discovery__pending' : ''}>{journal.verification === 'verified_journal' ? (isZh ? '期刊已核对' : 'Journal verified') : (isZh ? '发表源待核验' : 'Source unverified')}</span></div>
        {journal.issns.length > 0 && <p className="journal-discovery__muted">ISSN {journal.issns.join(' / ')}</p>}
        <p>{journal.reason}</p>
        <div className="journal-discovery__metrics">{journal.metrics.map((metric, i) => <div key={i}>
          <strong>{metric.name}：{typeof metric.value === 'number' ? numberFormat.format(metric.value) : metric.value}</strong>
          <small>{metric.year ? `${metric.year} 年` : (isZh ? '指标年度未提供' : 'Metric year unavailable')} · {metric.source}</small>
          <small>{metric.basis}</small>
          {link(metric.source_url) && <a href={link(metric.source_url)!} target="_blank" rel="noopener noreferrer">{isZh ? '核对指标来源' : 'Metric source'}</a>}
        </div>)}</div>
        {journal.verification === 'verified_journal' && !journal.metrics.some(metric => metric.name.startsWith('JIF')) && <p className="journal-discovery__muted">{isZh ? '正式 JIF 尚未提供，可在设置中导入有使用权且带年份的数据。开放指标不等于影响因子。' : 'Official JIF unavailable. Import licensed data with a reporting year in Settings; open metrics are not JIF.'}</p>}
        <div className="journal-discovery__actions">
          <button type="button" onClick={() => openPapers(journal)}>{journal.verification === 'verified_journal' ? (isZh ? '查看该刊相关论文' : 'Related papers in this journal') : (isZh ? '查看当前命中文献' : 'Current matched papers')}</button>
          {link(journal.homepage_url) && <a href={link(journal.homepage_url)!} target="_blank" rel="noopener noreferrer"><ExternalLink size={13} />{isZh ? '期刊主页 / 找作者指南' : 'Journal homepage / guidelines'}</a>}
          {link(journal.author_guidelines_url) && <a href={link(journal.author_guidelines_url)!} target="_blank" rel="noopener noreferrer">{isZh ? '官方作者指南' : 'Official author guidelines'}</a>}
          {journal.verification === 'verified_journal' && onCreateWritingDraft && <button type="button" onClick={() => onCreateWritingDraft(journalWritingDraft(journal, query, category, papers))}>{isZh ? '建立写作参考草稿' : 'Create writing-reference draft'}</button>}
        </div>
      </article>)}</div>
    </>}
    {selected && <div className="journal-discovery__papers">
      <h4>{selected.name} — {isZh ? '与当前关键词相关的论文' : 'Papers related to the current query'}</h4>
      {paperLoading && <p role="status">{isZh ? '正在查询该刊论文…' : 'Searching journal papers…'}</p>}
      {paperError && <p role="alert">{paperError}</p>}
      {related?.warnings.map((warning, i) => <p key={i} className="journal-discovery__warning">{warning}</p>)}
      {related && <p className="journal-discovery__muted">{related.scope}</p>}
      {!paperLoading && !related?.papers.length && <p>{isZh ? '补充查询尚无可展示论文，以下保留当前结果中的命中文献。' : 'No additional papers; current matched candidates remain below.'}</p>}
      {[...(related?.papers || []), ...currentPapers.filter(paper => !related?.papers.some(item => item.id === paper.id))].map(paper => <div key={paper.id} className="journal-discovery__paper">
        {onSelectPaper ? <button type="button" onClick={() => onSelectPaper(paper)}>{paper.title}</button> : <span>{paper.title}</span>}
        <small>{[paper.year, paper.venue, paper.source].filter(Boolean).join(' · ')}</small>
      </div>)}
    </div>}
  </section>
}

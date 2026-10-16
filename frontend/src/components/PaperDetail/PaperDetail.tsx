import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useInRouterContext } from 'react-router-dom'
import {
  X, ExternalLink, Copy, Check, Loader2, Sparkles, BookOpen,
  Calendar, Hash, Users, Lightbulb, AlertTriangle, FlaskConical,
  Languages, BookMarked, FileUp, FileCheck2, Library,
} from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import { useLocaleStore } from '@/stores/localeStore'
import { knowledgeApi, papersApi, zoteroApi, networkApi } from '@/api/client'
import { openAcademicAccess } from '@/api/accessClient'
import type { Paper, PaperDetail as PaperDetailType, AnalysisResult, FulltextStatus, KnowledgeItem } from '@/api/types'
import KnowledgeForm from '@/components/KnowledgeForm/KnowledgeForm'
import SimilarPapers from './SimilarPapers'
import { getAbstractText } from '@/utils/abstract'
import { emptyEntry, readingNote } from '@/utils/studyNote'
import { safeErrorMessage } from '@/utils/safeError'
import './PaperDetail.css'

interface PaperDetailProps {
  paper: PaperDetailType
  analysis: AnalysisResult | null
  analysisLoading: boolean
  evidenceSpans: import('@/api/types').EvidenceSpan[]
  evidenceLoading: boolean
  runId?: string | null
  onClose: () => void
  onAnalyze: (query?: string) => void
  onFulltextUploaded: (analyze?: boolean) => void
  onSelectPaper?: (paper: Paper) => void
  researchCategory?: string
  sourceQuery?: string
}

type AnalysisKey = 'full' | 'research_points' | 'limitations' | 'methodology'

const analysisConfig: Record<AnalysisKey, { icon: any; zhLabel: string; enLabel: string; query: string }> = {
  full: { icon: Sparkles, zhLabel: '全面分析', enLabel: 'Full Analysis', query: '请对这篇论文进行全面深度分析，包括：1)核心研究问题 2)创新点和主要贡献 3)研究方法和技术路线 4)实验设计和结果 5)论文优点和局限性 6)与当前研究方向的关联。请用中文回答。' },
  research_points: { icon: Lightbulb, zhLabel: '研究点提炼', enLabel: 'Research Points', query: '请提炼这篇论文的核心研究点，包括：1)研究问题是什么 2)提出了什么新方法/新理论 3)核心创新点（与已有方法的区别） 4)主要贡献（对领域的推动）。请用中文详细回答。' },
  limitations: { icon: AlertTriangle, zhLabel: '研究缺陷', enLabel: 'Limitations', query: '请分析这篇论文的研究缺陷和局限性，包括：1)方法的假设和限制条件 2)实验设计的不足 3)数据集的局限 4)未讨论的潜在问题 5)未来可改进的方向。请用中文详细回答。' },
  methodology: { icon: FlaskConical, zhLabel: '方法论拆解', enLabel: 'Methodology', query: '请拆解这篇论文的研究方法，包括：1)整体技术路线 2)核心算法/模型架构 3)实验设计（数据集、基线、评估指标） 4)关键实现细节 5)方法的可复现性评估。请用中文详细回答。' },
}

export default function PaperDetailPanel({
  paper, analysis, analysisLoading,
  onClose, onAnalyze, onFulltextUploaded, onSelectPaper, researchCategory = '', sourceQuery = '',
}: PaperDetailProps) {
  const { locale } = useLocaleStore()
  const inRouter = useInRouterContext()
  const [copiedField, setCopiedField] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'abstract' | 'analysis' | 'similar'>('abstract')
  const [translatedAbstract, setTranslatedAbstract] = useState<string | null>(null)
  const [translating, setTranslating] = useState(false)
  const [activeAnalysis, setActiveAnalysis] = useState<AnalysisKey | null>(null)
  const [knowledgeFormOpen, setKnowledgeFormOpen] = useState(false)
  const [knowledgeOpening, setKnowledgeOpening] = useState(false)
  const [knowledgeEditing, setKnowledgeEditing] = useState<KnowledgeItem | null>(null)
  const [knowledgePrefill, setKnowledgePrefill] = useState<Partial<KnowledgeItem> | null>(null)
  const noteScope = `${paper.id}\u0000${researchCategory.trim() || '未分类'}`
  const activeScopeRef = useRef(noteScope)
  const noteRequestRef = useRef(0)
  activeScopeRef.current = noteScope
  useEffect(() => {
    activeScopeRef.current = noteScope
    setKnowledgeOpening(false); setKnowledgeFormOpen(false)
    return () => { activeScopeRef.current = ''; noteRequestRef.current++ }
  }, [noteScope])
  const [knowledgeCategories, setKnowledgeCategories] = useState<{ name: string; count: number }[]>([])
  const [fulltextStatus, setFulltextStatus] = useState<FulltextStatus | null>(null)
  const [uploadingFulltext, setUploadingFulltext] = useState(false)
  const [zoteroSyncing, setZoteroSyncing] = useState(false)
  const [zoteroPickerOpen, setZoteroPickerOpen] = useState(false)
  const [zoteroCollections, setZoteroCollections] = useState<import('@/api/types').ZoteroCollection[]>([])
  const [zoteroCollectionsLoading, setZoteroCollectionsLoading] = useState(false)
  const [zoteroSelectedCollection, setZoteroSelectedCollection] = useState('') // '' = 根库（我的文库）
  const [analysisElapsed, setAnalysisElapsed] = useState(0)
  const analysisStartedAtRef = useRef<number | null>(null)
  const fulltextInputRef = useRef<HTMLInputElement>(null)
  const fulltextStatusRequestRef = useRef(0)
  const fulltextUploadRequestRef = useRef(0)
  const visiblePaperIdRef = useRef(paper.id)
  visiblePaperIdRef.current = paper.id
  useEffect(() => {
    visiblePaperIdRef.current = paper.id
    fulltextUploadRequestRef.current++
    setUploadingFulltext(false)
    return () => { visiblePaperIdRef.current = '' }
  }, [paper.id])

  const isChinese = locale === 'zh'
  const abstractText = getAbstractText(paper.abstract)
  const refreshFulltextStatus = useCallback((markUnavailableOnFailure = false) => {
    const request = ++fulltextStatusRequestRef.current
    void papersApi.fulltextStatus(paper.id)
      .then(({ data }) => { if (request === fulltextStatusRequestRef.current) setFulltextStatus(data) })
      .catch(() => { if (markUnavailableOnFailure && request === fulltextStatusRequestRef.current) setFulltextStatus({ available: false, source: null, file_size: 0 }) })
  }, [paper.id])
  useEffect(() => {
    let active = true
    const unsubscribe = window.scholarAccess?.onImported(event => {
      if (!active || event.paper_id !== paper.id) return
      refreshFulltextStatus()
      onFulltextUploaded(false)
      toast.success(isChinese ? '授权全文已导入，可进入原文伴读' : 'Authorized PDF imported. Open guided reading.')
    })
    return () => { active = false; unsubscribe?.() }
  }, [paper.id, refreshFulltextStatus, onFulltextUploaded, isChinese])

  const openInstitutionalAccess = async () => {
    try {
      const { data } = await networkApi.libraryLink(paper.doi || paper.title)
      await navigator.clipboard.writeText(data.query).catch(() => {})
      const mode = await openAcademicAccess({ url: data.url, channel: 'campus', paper_id: paper.id })
      toast.success(mode === 'desktop' ? '已绑定当前论文；请完成机构登录，再识别 PDF 并确认导入' : '请登录图书馆，下载当前论文后点击“导入全文 PDF”')
    } catch (error) { toast.error(safeErrorMessage(error, '机构页面打开失败')) }
  }

  // 切换论文时重置所有一次性状态，避免残留上一篇的翻译/分析
  useEffect(() => {
    setTranslatedAbstract(null)
    setActiveAnalysis(null)
    setActiveTab('abstract')
  }, [paper.id])

  // 分析进行中显示已用时长
  useEffect(() => {
    if (analysisLoading) {
      analysisStartedAtRef.current = Date.now()
      setAnalysisElapsed(0)
      const timer = window.setInterval(() => {
        setAnalysisElapsed(Math.floor((Date.now() - (analysisStartedAtRef.current || Date.now())) / 1000))
      }, 500)
      return () => window.clearInterval(timer)
    }
  }, [analysisLoading])

  useEffect(() => {
    setFulltextStatus(null)
    refreshFulltextStatus(true)
    return () => { fulltextStatusRequestRef.current++ }
  }, [refreshFulltextStatus])

  const handleTranslate = async () => {
    if (translatedAbstract) { setTranslatedAbstract(null); return }
    setTranslating(true)
    try {
      const res = await fetch('/api/v1/papers/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: paper.abstract || paper.title, target_lang: 'zh' }),
      })
      if (res.ok) { const data = await res.json(); setTranslatedAbstract(data.translated) }
      else toast.error(isChinese ? '翻译失败' : 'Translation failed')
    } catch { toast.error(isChinese ? '翻译失败' : 'Translation failed') }
    finally { setTranslating(false) }
  }

  const handleAnalysisClick = (key: AnalysisKey) => {
    setActiveAnalysis(key)
    setActiveTab('analysis')
    onAnalyze(analysisConfig[key].query)
  }

  const handleFulltextFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
      toast.error(isChinese ? '请选择 PDF 文件' : 'Please select a PDF file')
      return
    }
    const uploadPaperId = paper.id
    const request = ++fulltextUploadRequestRef.current
    setUploadingFulltext(true)
    try {
      const { data } = await papersApi.uploadFulltext(paper.id, file)
      if (visiblePaperIdRef.current !== uploadPaperId || request !== fulltextUploadRequestRef.current) {
        onFulltextUploaded()
        return
      }
      fulltextStatusRequestRef.current++
      setFulltextStatus(data)
      setActiveAnalysis('full')
      setActiveTab('analysis')
      if (data.feature_error) {
        toast(data.feature_error, { icon: '⚠️', duration: 6000 })
      } else {
        toast.success(isChinese
          ? `全文已导入（${data.page_count || 0} 页），正在重新分析`
          : `Full text imported (${data.page_count || 0} pages). Re-analyzing.`)
      }
      onFulltextUploaded()
    } catch (error: any) {
      if (visiblePaperIdRef.current === uploadPaperId && request === fulltextUploadRequestRef.current) toast.error(error.response?.data?.detail || (isChinese ? 'PDF 导入失败' : 'PDF import failed'))
    } finally {
      if (visiblePaperIdRef.current === uploadPaperId && request === fulltextUploadRequestRef.current) setUploadingFulltext(false)
    }
  }

  const handleSyncToZotero = async () => {
    setZoteroSyncing(true)
    try {
      let connected = false
      try {
        const { data } = await zoteroApi.status()
        connected = data.connected
      } catch {
        connected = false
      }
      if (!connected) {
        toast.error(isChinese
          ? '未检测到本地 Zotero。请先启动 Zotero，并在 设置 → 高级 → 允许其他应用程序与 Zotero 通信 中启用本地 API。'
          : 'Zotero not detected. Start Zotero and enable the local API under Settings → Advanced.')
        return
      }
      setZoteroCollectionsLoading(true)
      try {
        const { data } = await zoteroApi.collections()
        setZoteroCollections(data.items || [])
      } catch {
        setZoteroCollections([])
        toast.error(isChinese ? '获取 Zotero 文件夹列表失败' : 'Failed to load Zotero collections')
        return
      } finally {
        setZoteroCollectionsLoading(false)
      }
      setZoteroSelectedCollection('')
      setZoteroPickerOpen(true)
    } finally {
      setZoteroSyncing(false)
    }
  }

  const handleConfirmZoteroPush = async () => {
    setZoteroSyncing(true)
    try {
      const { data } = await zoteroApi.push({
        title: paper.title,
        creators: paper.authors.map((name) => {
          const parts = name.trim().split(/\s+/)
          return {
            firstName: parts.length > 1 ? parts.slice(0, -1).join(' ') : '',
            lastName: parts[parts.length - 1] || '',
          }
        }),
        year: paper.year ? String(paper.year) : undefined,
        venue: paper.venue || undefined,
        doi: paper.doi || undefined,
        url: paper.url || undefined,
        abstract: paper.abstract || undefined,
        collection_key: zoteroSelectedCollection || undefined,
      })
      if (data.warnings?.length) {
        toast(data.warnings.join(' '), { icon: '⚠️', duration: 7000 })
      } else {
        toast.success(isChinese
          ? (zoteroSelectedCollection
              ? `论文元数据已同步到 Zotero「${zoteroCollections.find((c) => c.key === zoteroSelectedCollection)?.name || ''}」`
              : '论文元数据已同步到 Zotero 我的文库')
          : 'Paper metadata saved to Zotero')
      }
      setZoteroPickerOpen(false)
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      const msg = typeof detail === 'object' ? detail?.message : detail
      toast.error(msg || (isChinese ? '同步到 Zotero 失败' : 'Failed to push to Zotero'))
    } finally {
      setZoteroSyncing(false)
    }
  }

  const handleSaveToKnowledge = async () => {
    if (knowledgeOpening) return
    setKnowledgeOpening(true)
    const paperId = paper.id
    const noteCategory = researchCategory.trim() || '未分类'
    const requestId = ++noteRequestRef.current
    const isCurrent = () => activeScopeRef.current === noteScope && noteRequestRef.current === requestId
    try {
      const [response, existing] = await Promise.all([
        knowledgeApi.getCategories(),
        knowledgeApi.list(noteCategory, { card_type: 'reading_note', source_paper_id: paperId, page_size: 1 }),
      ])
      if (!isCurrent()) return
      setKnowledgeCategories(response.data)
      setKnowledgeEditing(existing.data.items[0] || null)
      const notebook = readingNote()
      notebook.status = 'reading'
      notebook.source_query = sourceQuery.slice(0, 2000)
      const analysisText = analysis ? [
        `已有分析（${analysis.document_coverage === 'fulltext' ? '全文材料，覆盖范围以原分析提示为准' : '摘要材料'}）`,
        analysis.summary, analysis.methodology,
        ...analysis.key_findings, ...analysis.strengths, ...analysis.weaknesses, analysis.relevance_to_query,
      ].filter(Boolean).join('\n\n') : ''
      const bounded = (text: string) => text.length <= 20000 ? text : `${text.slice(0, 19880)}\n\n[此栏达到长度上限；剩余内容请回到论文原分析查看。]`
      notebook.sections.overview = {
        ...emptyEntry(), paper_notes: bounded(abstractText || ''), ai_suggestions: bounded(analysisText),
      }
      setKnowledgePrefill({
        title: paper.title, category: noteCategory, content: '', card_type: 'reading_note', card_data: notebook,
        source_paper_id: paper.id, source_paper_title: paper.title, source_paper_doi: paper.doi,
        research_points: [], tags: paper.keywords?.slice(0, 5) || [], notes: null,
      })
      setKnowledgeFormOpen(true)
    } catch (error) {
      if (isCurrent()) toast.error(safeErrorMessage(error, isChinese ? '读取笔记失败，请重试' : 'Could not open notes. Please retry.'))
    } finally {
      if (isCurrent()) setKnowledgeOpening(false)
    }
  }

  const copyToClipboard = async (text: string, field: string) => {
    try { await navigator.clipboard.writeText(text); setCopiedField(field); toast.success(isChinese ? '已复制' : 'Copied'); setTimeout(() => setCopiedField(null), 2000) }
    catch { toast.error(isChinese ? '复制失败' : 'Failed to copy') }
  }

  const formats = {
    apa: `${paper.authors.join(', ')}${paper.year ? ` (${paper.year}).` : '.'} ${paper.title}.${paper.venue ? ` *${paper.venue}*.` : ''}${paper.doi ? ` https://doi.org/${paper.doi}` : ''}`,
    bibtex: `@article{${paper.id},\n  title={${paper.title}},\n  author={${paper.authors.join(' and ')}},\n${paper.year ? `  year={${paper.year}},\n` : ''}${paper.venue ? `  journal={${paper.venue}},\n` : ''}${paper.doi ? `  doi={${paper.doi}},\n` : ''}}`,
  }

  // 解析分析结果为结构化段落
  const renderAnalysis = (text: string) => {
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
    <div className="paper-detail animate-fade-in">
      {/* Header */}
      <div className="paper-detail-header">
        <div className="flex-1 min-w-0">
          <h2 className="paper-detail-title">{paper.title}</h2>
        </div>
        <button onClick={onClose} className="paper-detail-close"><X className="w-5 h-5" /></button>
      </div>

      {/* Meta */}
      <div className="paper-detail-meta">
        {paper.authors.length > 0 && <div className="meta-row"><Users className="w-4 h-4" /><span>{paper.authors.join(', ')}</span></div>}
        {paper.year && <div className="meta-row"><Calendar className="w-4 h-4" /><span>{paper.year}</span></div>}
        {paper.venue && <div className="meta-row"><BookOpen className="w-4 h-4" /><span>{paper.venue}</span></div>}
        {paper.doi && <div className="meta-row"><Hash className="w-4 h-4" /><a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noopener noreferrer" className="text-primary-600 dark:text-primary-400 hover:underline">{paper.doi}</a></div>}
      </div>

      {/* Links */}
      <div className="paper-detail-links">
        {inRouter
          ? <Link to={`/assistant?paper=${encodeURIComponent(paper.id)}&category=${encodeURIComponent(researchCategory)}`} className="paper-link"><BookOpen className="w-3.5 h-3.5" />{isChinese ? '原文伴读' : 'Guided reading'}</Link>
          : <a href={`/assistant?paper=${encodeURIComponent(paper.id)}`} className="paper-link"><BookOpen className="w-3.5 h-3.5" />{isChinese ? '原文伴读' : 'Guided reading'}</a>}
        <button type="button" className="paper-link" onClick={openInstitutionalAccess}><Library className="w-3.5 h-3.5" />{isChinese ? '机构授权获取' : 'Institutional full text'}</button>
        {paper.url && <button type="button" className="paper-link" onClick={() => {
          void openAcademicAccess({ url: paper.url!, channel: 'public', paper_id: paper.id }).catch(error => toast.error(safeErrorMessage(error, '页面打开失败')))
        }}><ExternalLink className="w-3.5 h-3.5" />{isChinese ? '网页获取 PDF' : 'Get PDF from page'}</button>}
        {paper.url && <a href={paper.url} target="_blank" rel="noopener noreferrer" className="paper-link"><ExternalLink className="w-3.5 h-3.5" />{isChinese ? '查看原文' : 'View Paper'}</a>}
        {paper.pdf_url && <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer" className="paper-link paper-link-oa"><ExternalLink className="w-3.5 h-3.5" />PDF</a>}
        <input
          ref={fulltextInputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={handleFulltextFile}
        />
        <button
          type="button"
          className="paper-link paper-link-upload"
          disabled={uploadingFulltext}
          onClick={() => fulltextInputRef.current?.click()}
          title={isChinese ? '导入你有权使用的 PDF，供 Agent 读取全文和图表' : 'Import an authorized PDF for full-text and figure analysis'}
        >
          {uploadingFulltext
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : fulltextStatus?.available
              ? <FileCheck2 className="w-3.5 h-3.5" />
              : <FileUp className="w-3.5 h-3.5" />}
          {fulltextStatus?.available
            ? (isChinese ? '替换全文' : 'Replace PDF')
            : (isChinese ? '导入全文 PDF' : 'Import PDF')}
        </button>
        <button
          type="button"
          className="paper-link"
          disabled={zoteroSyncing}
          onClick={handleSyncToZotero}
          title={isChinese ? '将论文元数据同步到本地 Zotero；PDF 需手动添加' : 'Save paper metadata to Zotero; add PDFs manually'}
        >
          {zoteroSyncing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <BookMarked className="w-3.5 h-3.5" />}
          {isChinese ? '同步到 Zotero' : 'Push to Zotero'}
        </button>
      </div>

      {/* Zotero 文件夹选择器 */}
      <div className="px-4 py-2 border-b border-gray-200 dark:border-gray-700">
        <button type="button" className="paper-link" disabled={knowledgeOpening} onClick={handleSaveToKnowledge}>
          {knowledgeOpening ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <BookOpen className="w-3.5 h-3.5" />}
          {isChinese ? '阅读笔记 / 继续编辑' : 'Reading note / continue editing'}
        </button>
        <p className="text-xs text-gray-500 mt-1">{isChinese ? `分类：${researchCategory.trim() || '未分类'} · 摘录、复现、公式与写作笔记` : `Category: ${researchCategory.trim() || '未分类 (Uncategorized)'} · reading, reproduction, math and writing`}</p>
      </div>
      {zoteroPickerOpen && (
        <div className="mt-3 p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40">
          <div className="flex items-center gap-2 mb-2">
            <BookMarked className="w-4 h-4 text-primary-500" />
            <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
              {isChinese ? '同步到 Zotero 文件夹' : 'Push to Zotero collection'}
            </span>
          </div>
          {zoteroCollectionsLoading ? (
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              {isChinese ? '正在读取文件夹列表...' : 'Loading collections...'}
            </div>
          ) : (
            <select
              value={zoteroSelectedCollection}
              onChange={(e) => setZoteroSelectedCollection(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-400"
            >
              <option value="">{isChinese ? '我的文库（根目录）' : 'My Library (root)'}</option>
              {zoteroCollections.map((col) => (
                <option key={col.key} value={col.key}>{col.name}</option>
              ))}
            </select>
          )}
          {!zoteroCollectionsLoading && zoteroCollections.length === 0 && (
            <p className="text-[11px] text-gray-400 mt-1">
              {isChinese ? '未发现自定义文件夹，将写入我的文库根目录。' : 'No custom collections found; will write to My Library root.'}
            </p>
          )}
          <div className="flex items-center justify-end gap-2 mt-3">
            <button
              type="button"
              onClick={() => setZoteroPickerOpen(false)}
              className="px-3 py-1.5 rounded-md text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              {isChinese ? '取消' : 'Cancel'}
            </button>
            <button
              type="button"
              disabled={zoteroSyncing || zoteroCollectionsLoading}
              onClick={handleConfirmZoteroPush}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-primary-600 text-white hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {zoteroSyncing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <BookMarked className="w-3.5 h-3.5" />}
              {zoteroSyncing ? (isChinese ? '同步中...' : 'Syncing...') : (isChinese ? '确认同步' : 'Confirm')}
            </button>
          </div>
        </div>
      )}

      {(fulltextStatus?.available || analysis) && (
        <div className={clsx(
          'fulltext-status',
          (analysis?.document_coverage === 'fulltext' && analysis.model_completed !== false)
            || (fulltextStatus?.available && !analysis)
            ? 'fulltext-status-ready'
            : 'fulltext-status-warning',
        )}>
          {analysis?.document_coverage === 'fulltext' && analysis.model_completed !== false ? (
            <>
              <FileCheck2 className="w-4 h-4 flex-shrink-0" />
              <div className="min-w-0">
                <span>
                  {isChinese ? '已分析 PDF 文字' : 'PDF text analyzed'}
                  {analysis.visual_pages_read > 0
                    ? (isChinese ? `，并读取 ${analysis.visual_pages_read} 个图表页面` : ` with ${analysis.visual_pages_read} visual pages`)
                    : (isChinese ? '；本次未成功读取图表页面' : '; no visual page was successfully read in this analysis')}
                  {analysis.total_tokens > 0
                    ? (isChinese ? `；模型 Token ${analysis.total_tokens}` : `; model tokens ${analysis.total_tokens}`)
                    : ''}
                </span>
                {analysis.document_error && <p className="mt-1 opacity-80">{analysis.document_error}</p>}
              </div>
            </>
          ) : fulltextStatus?.available && analysis?.model_completed === false ? (
            <div className="min-w-0">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>{isChinese
                  ? 'PDF 材料已准备，但模型服务未完成本次分析；当前显示基础回退结果，请重试。'
                  : 'The PDF was parsed, but the model did not complete this analysis. Retry to use the prepared full text.'}</span>
              </div>
              {analysis.document_error && <p className="mt-1 opacity-80">{analysis.document_error}</p>}
            </div>
          ) : fulltextStatus?.available && !analysis ? (
            <>
              <FileCheck2 className="w-4 h-4 flex-shrink-0" />
              <span>{fulltextStatus.feature_error || (isChinese
                ? 'PDF 已保存，实际正文与图表读取情况以分析结果为准。'
                : 'PDF saved. The analysis result will report text and figure coverage.')}</span>
            </>
          ) : (
            <div className="min-w-0">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>{isChinese
                  ? '自动获取全文未成功，本次仅依据摘要。请通过学校图书馆 / 校园网数据库下载你有权访问的 PDF，回到本页点击“导入全文 PDF”，AI 将读取全文与图表辅助分析。'
                  : 'Automatic full-text retrieval failed. This analysis used the abstract only; import an authorized PDF for full analysis.'}</span>
              </div>
              {analysis?.document_error && (
                <details className="fulltext-error-detail">
                  <summary>{isChinese ? '查看获取详情' : 'Retrieval details'}</summary>
                  <p>{analysis.document_error}</p>
                </details>
              )}
            </div>
          )}
        </div>
      )}

      {/* Analysis buttons */}
      <div className="p-3 border-b border-gray-200 dark:border-gray-700">
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">🤖 {isChinese ? 'AI 深度分析' : 'AI Deep Analysis'}</p>
        <div className="flex flex-wrap gap-1.5">
          {(Object.entries(analysisConfig) as [AnalysisKey, typeof analysisConfig[AnalysisKey]][]).map(([key, { icon: Icon, zhLabel, enLabel }]) => (
            <button key={key} onClick={() => handleAnalysisClick(key)} disabled={analysisLoading}
              className={clsx('inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-all',
                activeAnalysis === key ? 'bg-primary-600 text-white' : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700',
                analysisLoading && 'opacity-50 cursor-not-allowed')}>
              {analysisLoading && activeAnalysis === key
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <Icon className="w-3 h-3" />}
              {isChinese ? zhLabel : enLabel}
            </button>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="paper-detail-tabs">
        {(['abstract', 'analysis', 'similar'] as const).map((tab) => (
          <button key={tab} onClick={() => setActiveTab(tab)} className={clsx('paper-tab', activeTab === tab && 'paper-tab-active')}>
            {tab === 'abstract' && <BookOpen className="w-3.5 h-3.5" />}
            {tab === 'analysis' && <Sparkles className="w-3.5 h-3.5" />}
            {tab === 'similar' && <Library className="w-3.5 h-3.5" />}
            {tab === 'abstract' ? (isChinese ? '摘要' : 'Abstract') : tab === 'analysis' ? (isChinese ? '分析' : 'Analysis') : (isChinese ? '相似推荐' : 'Similar papers')}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="paper-detail-content custom-scrollbar">
        {activeTab === 'similar' && <SimilarPapers key={paper.id} paperId={paper.id} onSelect={onSelectPaper} />}
        {activeTab === 'abstract' && (
          <div className="animate-fade-in">
            {abstractText && (
              <button onClick={handleTranslate} disabled={translating}
                className="inline-flex items-center gap-1 px-2.5 py-1 mb-3 rounded-md text-xs font-medium bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 hover:bg-primary-100 dark:hover:bg-primary-900/50 transition-colors">
                {translating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Languages className="w-3 h-3" />}
                {translatedAbstract ? (isChinese ? '显示原文' : 'Show Original') : (isChinese ? '翻译为中文' : 'Translate to Chinese')}
              </button>
            )}
            {translatedAbstract
              ? <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-line">{translatedAbstract}</p>
              : abstractText
                ? <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-line">{abstractText}</p>
                : <div className="space-y-2 text-sm text-gray-500 dark:text-gray-400">
                    <p>{isChinese ? '当前数据源未提供摘要，这不代表论文没有摘要。' : 'The current source did not provide an abstract; the paper may still have one.'}</p>
                    <p>{isChinese ? '可在搜索结果选择“有摘要”；如需阅读本篇，请通过图书馆或出版方获取你有权使用的 PDF，再点击上方“导入全文 PDF”。' : 'Filter results to papers with abstracts, or obtain an authorized PDF from your library or publisher and use Import PDF above.'}</p>
                  </div>
            }
            <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
              <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">{isChinese ? '引用格式' : 'Citation'}</h4>
              <div className="space-y-2">
                {Object.entries(formats).map(([fmt, text]) => (
                  <div key={fmt} className="citation-block">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">{fmt}</span>
                      <button onClick={() => copyToClipboard(text, fmt)} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
                        {copiedField === fmt ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                    <pre className="text-xs text-gray-600 dark:text-gray-400 whitespace-pre-wrap break-all font-mono">{text}</pre>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'analysis' && (
          <div className="animate-fade-in">
            {analysisLoading && (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-primary-500" />
                <span className="ml-2 text-sm text-gray-500 tabular-nums">
                  {isChinese
                    ? `正在分析：${activeAnalysis ? analysisConfig[activeAnalysis].zhLabel : ''}... 已用时 ${analysisElapsed}s`
                    : `Analyzing... ${analysisElapsed}s elapsed`}
                </span>
              </div>
            )}
            {!analysis && !analysisLoading && (
              <div className="text-center py-6">
                <Sparkles className="w-8 h-8 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
                <p className="text-sm text-gray-500 dark:text-gray-400">{isChinese ? '点击上方分析按钮开始' : 'Click a button above to start'}</p>
              </div>
            )}
            {analysis && !analysisLoading && (
              <div>
                {/* 当前分析类型标题 */}
                {activeAnalysis && (
                  <div className="flex items-center gap-2 mb-3 pb-2 border-b border-gray-200 dark:border-gray-700">
                    {(() => { const cfg = analysisConfig[activeAnalysis]; const Icon = cfg.icon; return <><Icon className="w-4 h-4 text-primary-500" /><h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">{isChinese ? cfg.zhLabel : cfg.enLabel}</h3></> })()}
                  </div>
                )}
                {/* 分析内容 - 结构化展示 */}
                <div className="space-y-1">
                  {renderAnalysis(analysis.summary)}
                </div>
                {/* 方法论 */}
                {analysis.methodology && (
                  <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                    <h4 className="text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">{isChinese ? '方法论' : 'Methodology'}</h4>
                    <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{analysis.methodology}</p>
                  </div>
                )}
                {/* 关键发现 */}
                {analysis.key_findings.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                    <h4 className="text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">{isChinese ? '关键发现' : 'Key Findings'}</h4>
                    <ul className="space-y-1">
                      {analysis.key_findings.map((f, i) => (
                        <li key={i} className="flex items-start gap-1.5 text-sm text-gray-700 dark:text-gray-300">
                          <span className="text-primary-500 mt-0.5">•</span>{f}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {/* 优缺点 */}
                <div className="grid grid-cols-2 gap-3 mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                  {analysis.strengths.length > 0 && (
                    <div>
                      <h4 className="text-xs font-bold text-green-600 dark:text-green-400 uppercase mb-1">{isChinese ? '✅ 优点' : '✅ Strengths'}</h4>
                      <ul className="space-y-1">
                        {analysis.strengths.map((s, i) => <li key={i} className="text-xs text-gray-700 dark:text-gray-300 leading-relaxed">• {s}</li>)}
                      </ul>
                    </div>
                  )}
                  {analysis.weaknesses.length > 0 && (
                    <div>
                      <h4 className="text-xs font-bold text-red-600 dark:text-red-400 uppercase mb-1">{isChinese ? '⚠️ 局限性' : '⚠️ Weaknesses'}</h4>
                      <ul className="space-y-1">
                        {analysis.weaknesses.map((w, i) => <li key={i} className="text-xs text-gray-700 dark:text-gray-300 leading-relaxed">• {w}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
                {/* 与查询关联 */}
                {analysis.relevance_to_query && (
                  <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                    <h4 className="text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">{isChinese ? '🔗 与查询的关联' : '🔗 Relevance'}</h4>
                    <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{analysis.relevance_to_query}</p>
                  </div>
                )}
                {/* Save to Knowledge Button */}
                <div className="mt-4 pt-3 border-t border-gray-200 dark:border-gray-700">
                  <button
                    onClick={handleSaveToKnowledge} disabled={knowledgeOpening}
                    className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 hover:bg-primary-100 dark:hover:bg-primary-900/50 border border-primary-200 dark:border-primary-800 transition-colors"
                  >
                    <BookMarked className="w-4 h-4" />
                    {isChinese ? '整理为阅读笔记 / 继续编辑' : 'Create / continue reading note'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

      </div>

      {/* Knowledge Form for saving analysis */}
      <KnowledgeForm
        open={knowledgeFormOpen}
        editingItem={knowledgeEditing}
        prefill={knowledgePrefill}
        categories={knowledgeCategories}
        onClose={() => setKnowledgeFormOpen(false)}
        onSuccess={() => {
          setKnowledgeFormOpen(false)
          toast.success(isChinese ? '已保存到知识库' : 'Saved to knowledge base')
        }}
      />
    </div>
  )
}

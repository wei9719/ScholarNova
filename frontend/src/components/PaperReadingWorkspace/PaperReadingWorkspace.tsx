import { useEffect, useRef, useState } from 'react'
import {
  ArrowLeft,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileUp,
  Loader2,
  Save,
  Sparkles,
} from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { knowledgeApi, papersApi } from '@/api/client'
import {
  readerApi,
  readerAssetUrl,
  type ReadingDocument,
  type ReadingMode,
  type ReadingPage,
  type ReadingResponse,
} from '@/api/readerClient'
import type { KnowledgeItem, PaperDetail, StudySection } from '@/api/types'
import KnowledgeForm from '@/components/KnowledgeForm/KnowledgeForm'
import SimilarPapers from '@/components/PaperDetail/SimilarPapers'
import WritingSamplesPanel from './WritingSamplesPanel'
import { useLocaleStore } from '@/stores/localeStore'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { emptyEntry, readingNote } from '@/utils/studyNote'
import { safeErrorMessage } from '@/utils/safeError'
import './PaperReadingWorkspace.css'

interface Props {
  paperId: string
  initialCategory?: string
  initialPage?: number
  initialHash?: string
}
interface ReadingTurn {
  id: number
  mode: ReadingMode
  page: number
  hash: string
  selection: string
  question: string
  result: ReadingResponse
}
const modes: Array<{
  key: ReadingMode
  zh: string
  en: string
  section: StudySection
}> = [
  { key: 'translate', zh: '翻译原文', en: 'Translate', section: 'translation' },
  { key: 'explain', zh: '解释段落', en: 'Explain', section: 'overview' },
  { key: 'math', zh: '解释公式', en: 'Equations', section: 'math' },
  { key: 'figure', zh: '读图表', en: 'Figures', section: 'figures' },
  {
    key: 'prerequisite',
    zh: '补基础与练习',
    en: 'Learn & practise',
    section: 'prerequisites',
  },
  {
    key: 'writing',
    zh: '整理写作样例',
    en: 'Writing sample',
    section: 'writing',
  },
  {
    key: 'extension',
    zh: '阅读拓展',
    en: 'Explore further',
    section: 'prerequisites',
  },
]
const hasParts = (result: ReadingResponse) =>
  Boolean(result.source_notes || result.teaching_notes || result.exercise)
const calculationScope =
  '只对下列输入按给定指标作本地数值验算；不证明数据来自论文、指标选择、公式转录或推导正确。'
const sourceNotesTitle = (result: ReadingResponse, zh = true) =>
  result.source_grounding
    ? zh
      ? '原文摘录（本页文字匹配）'
      : 'Source quotations (page-text match)'
    : zh
      ? '原文相关说明（AI 归纳，需核对）'
      : 'Source discussion (AI summary; verify)'
const teachingNotesTitle = (result: ReadingResponse, zh = true) =>
  result.source_grounding
    ? zh
      ? '教学补充与未核实解读（非作者明文）'
      : 'Teaching and unverified interpretations (not explicit author statements)'
    : zh
      ? '通俗解释与学习建议'
      : 'Explanation & study guidance'
const sourceGroundingText = (
  grounding: NonNullable<ReadingResponse['source_grounding']>,
  zh = true,
) =>
  [
    `${zh ? '匹配摘录' : 'Matched quotations'}: ${grounding.matched_quotes} · ${zh ? '拒绝摘录' : 'Rejected quotations'}: ${grounding.rejected_quotes}`,
    grounding.detail,
    grounding.moved_source_notes &&
      (zh
        ? '未核实的原文解读已放入教学补充。'
        : 'Unverified source interpretations were moved to teaching guidance.'),
    zh
      ? '仅核对摘录是否在本次提供的页面文字中连续匹配，不核验解读归属、公式或推导正确性。'
      : 'Checks only contiguous quotation matches in the supplied page text, not interpretation attribution, formulas or derivations.',
  ]
    .filter(Boolean)
    .join('\n')
const calculationText = (
  check: NonNullable<ReadingResponse['calculation_checks']>[number],
  zh = true,
) => {
  const labels = zh
    ? {
        computed: '已计算，未给声称结果',
        matched: '数值一致（仅此算例）',
        mismatch: '数值不一致',
        unsupported: '无法验算',
      }
    : {
        computed: 'Computed; no claimed value',
        matched: 'Numeric match (this example only)',
        mismatch: 'Numeric mismatch',
        unsupported: 'Cannot check',
      }
  return [
    `${check.metric.toUpperCase()} · ${labels[check.status]}`,
    `${zh ? '真实值' : 'Actual'}: ${JSON.stringify(check.y_true)} · ${zh ? '预测值' : 'Predicted'}: ${JSON.stringify(check.y_pred)}`,
    check.mask &&
      `mask: ${JSON.stringify(check.mask)} (${zh ? '1 表示参与验算' : '1 means included'})`,
    `${zh ? '模型声称' : 'Model claimed'}: ${check.claimed_result ?? '—'} · ${zh ? '本地计算' : 'Local result'}: ${check.computed_result ?? '—'}`,
    check.detail,
  ]
    .filter(Boolean)
    .join('\n')
}
const outputText = (turn: ReadingTurn) =>
  [
    !hasParts(turn.result) && turn.result.answer,
    turn.result.source_notes &&
      `${sourceNotesTitle(turn.result)}\n${turn.result.source_notes}`,
    turn.result.teaching_notes &&
      `${turn.result.source_grounding ? teachingNotesTitle(turn.result) : '学习解释（AI 建议）'}\n${turn.result.teaching_notes}`,
    turn.result.exercise && `练习建议\n${turn.result.exercise}`,
    turn.result.source_grounding &&
      `原文摘录匹配检查\n${sourceGroundingText(turn.result.source_grounding)}`,
    Boolean(turn.result.calculation_checks?.length) &&
      `基础算例数值验算（非数学证明）\n${calculationScope}\n${turn.result.calculation_checks!.map((check) => calculationText(check)).join('\n\n')}`,
  ]
    .filter(Boolean)
    .join('\n\n')
const locationText = (page: number, hash: string) =>
  `PDF 第 ${page} 页；文件版本 ${hash}`
const noteTurnKey = (paperId: string, category: string, turn: ReadingTurn) =>
  JSON.stringify([paperId, category.trim(), turn.id, turn.page, turn.hash])

export default function PaperReadingWorkspace({
  paperId,
  initialCategory = '',
  initialPage = 1,
  initialHash = '',
}: Props) {
  const navigate = useNavigate()
  const { locale } = useLocaleStore()
  const zh = locale === 'zh'
  const [document, setDocument] = useState<ReadingDocument | null>(null)
  const [paper, setPaper] = useState<PaperDetail | null>(null)
  const [page, setPage] = useState<ReadingPage | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [pageInput, setPageInput] = useState('1')
  const [view, setView] = useState<'text' | 'image'>('text')
  const [loading, setLoading] = useState(true)
  const [pageLoading, setPageLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const [mode, setMode] = useState<ReadingMode>('explain')
  const [selection, setSelection] = useState('')
  const [question, setQuestion] = useState('')
  const [personal, setPersonal] = useState('')
  const [category, setCategory] = useState(initialCategory || '未分类')
  const [categories, setCategories] = useState<
    { name: string; count: number }[]
  >([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [turns, setTurns] = useState<ReadingTurn[]>([])
  // Successful saves only: these markers deliberately live in this workspace,
  // not in a new persistent schema or a global handwritten-text deduplicator.
  const [savedTurns, setSavedTurns] = useState<Map<string, Set<string>>>(
    () => new Map(),
  )
  const [lastSavedNote, setLastSavedNote] = useState<Pick<
    KnowledgeItem,
    'id' | 'category'
  > | null>(null)
  const [showRecommendations, setShowRecommendations] = useState(false)
  const [showWritingSamples, setShowWritingSamples] = useState(false)
  const [writingSamplesActivated, setWritingSamplesActivated] = useState(false)
  const [opening, setOpening] = useState(false)
  const [form, setForm] = useState<{
    editing: KnowledgeItem | null
    prefill: Partial<KnowledgeItem> | null
    readingTurn?: ReadingTurn
    personalDraft?: string
  } | null>(null)
  const textRef = useRef<HTMLDivElement>(null)
  const responsesRef = useRef<HTMLDivElement>(null)
  const uploadRef = useRef<HTMLInputElement>(null)
  const modelRequest = useRef<AbortController | null>(null)
  const noteRequest = useRef(0)
  const turnSequence = useRef(0)
  const alive = useRef(true)
  const pageIdentity = useRef('')
  pageIdentity.current = `${paperId}:${document?.content_hash || ''}:${pageNumber}`

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      modelRequest.current?.abort()
      noteRequest.current++
    }
  }, [])

  useEffect(() => {
    setSavedTurns(new Map())
    setLastSavedNote(null)
  }, [paperId])

  useEffect(() => {
    const request = new AbortController()
    setLoading(true)
    setError('')
    setPage(null)
    setDocument(null)
    modelRequest.current?.abort()
    modelRequest.current = null
    setBusy(false)
    setSelection('')
    setQuestion('')
    setTurns([])
    setPageNumber(1)
    setPageInput('1')
    void readerApi
      .document(paperId, request.signal)
      .then(({ data }) => {
        if (!request.signal.aborted) {
          setDocument({
            ...data,
            warnings: [
              ...data.warnings,
              ...(initialHash && data.content_hash !== initialHash
                ? [
                    zh
                      ? '来源对应的 PDF 版本已变化。当前显示最新文件，原页内容请按保存的来源快照重新核对。'
                      : 'The cited PDF version has changed. This is the current file; verify the passage against your saved source snapshot.',
                  ]
                : []),
            ],
          })
          const targetPage = Math.min(
            Math.max(1, Number.isInteger(initialPage) ? initialPage : 1),
            Math.max(1, data.page_count),
          )
          setPageNumber(targetPage)
          setPageInput(String(targetPage))
        }
      })
      .catch((reason) => {
        if (!request.signal.aborted)
          setError(
            safeErrorMessage(
              reason,
              zh
                ? '论文材料读取失败，请重试。'
                : 'Could not load the paper. Retry below.',
            ),
          )
      })
      .finally(() => {
        if (!request.signal.aborted) setLoading(false)
      })
    void papersApi
      .get(paperId)
      .then(({ data }) => {
        if (!request.signal.aborted) setPaper(data)
      })
      .catch(() => undefined)
    void knowledgeApi
      .getCategories()
      .then(({ data }) => {
        if (!request.signal.aborted) setCategories(data)
      })
      .catch(() => undefined)
    return () => {
      request.abort()
      modelRequest.current?.abort()
      noteRequest.current++
    }
  }, [paperId, revision, zh, initialPage, initialHash])

  useEffect(() => {
    const request = new AbortController()
    setPage(null)
    setSelection('')
    setQuestion('')
    setPageInput(String(pageNumber))
    setError('')
    modelRequest.current?.abort()
    modelRequest.current = null
    setBusy(false)
    if (!document?.available || !document.content_hash) {
      setPageLoading(false)
      return () => request.abort()
    }
    setPageLoading(true)
    void readerApi
      .page(paperId, pageNumber, document.content_hash, request.signal)
      .then(({ data }) => {
        if (
          !request.signal.aborted &&
          data.paper_id === paperId &&
          data.content_hash === document.content_hash &&
          data.page_number === pageNumber
        )
          setPage(data)
      })
      .catch((reason) => {
        if (!request.signal.aborted)
          setError(
            safeErrorMessage(
              reason,
              zh
                ? '本页读取失败，请重新载入论文。'
                : 'Could not read this page. Reload the paper.',
            ),
          )
      })
      .finally(() => {
        if (!request.signal.aborted) setPageLoading(false)
      })
    return () => request.abort()
  }, [paperId, document, pageNumber, zh])

  useEffect(() => {
    if (!busy) return
    const started = Date.now()
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [busy])

  useEffect(() => {
    const list = responsesRef.current
    if (turns.length && list?.scrollTo)
      list.scrollTo({ top: list.scrollHeight, behavior: 'auto' })
  }, [turns])

  const pickSelection = () => {
    if (busy) return
    const selected = window.getSelection()
    if (!selected?.rangeCount || !textRef.current) return
    const range = selected.getRangeAt(0)
    if (
      !textRef.current.contains(range.startContainer) ||
      !textRef.current.contains(range.endContainer)
    )
      return
    const value = selected.toString().trim()
    if (value.length > 5000) {
      setError(
        zh
          ? '请选择不超过 5000 字的一段原文，分段阅读更准确。'
          : 'Select up to 5,000 characters at a time.',
      )
      return
    }
    if (value) setSelection(value)
  }
  const stop = () => {
    modelRequest.current?.abort()
    modelRequest.current = null
    setBusy(false)
  }
  const ask = async () => {
    if (!page || busy || !document?.available) return
    const request = new AbortController()
    const identity = pageIdentity.current
    const input = {
      mode,
      page_number: page.page_number,
      selection: selection.trim(),
      question: question.trim(),
      content_hash: page.content_hash,
    }
    modelRequest.current = request
    setBusy(true)
    setElapsed(0)
    setError('')
    try {
      const { data } = await readerApi.assist(paperId, input, request.signal)
      if (
        !request.signal.aborted &&
        modelRequest.current === request &&
        pageIdentity.current === identity
      ) {
        const turnId = ++turnSequence.current
        setTurns((previous) =>
          [
            ...previous,
            {
              id: turnId,
              mode: input.mode,
              page: input.page_number,
              hash: input.content_hash,
              selection: input.selection,
              question: input.question,
              result: data,
            },
          ].slice(-12),
        )
      }
    } catch (reason) {
      if (!request.signal.aborted && pageIdentity.current === identity)
        setError(
          safeErrorMessage(
            reason,
            zh
              ? '本次伴读未完成，可以重试或先记录自己的理解。'
              : 'Reading assistance did not finish. Retry or continue with your own notes.',
          ),
        )
    } finally {
      if (modelRequest.current === request) {
        modelRequest.current = null
        setBusy(false)
      }
    }
  }
  const upload = async (file?: File) => {
    if (!file || uploading) return
    if (
      !file.name.toLowerCase().endsWith('.pdf') ||
      file.size > 50 * 1024 * 1024
    ) {
      setError(zh ? '请选择不超过 50 MB 的 PDF。' : 'Choose a PDF up to 50 MB.')
      return
    }
    if (
      (turns.length || selection || personal.trim()) &&
      !window.confirm(
        zh
          ? '替换 PDF 会清空当前未保存的伴读回答和选段。个人记录会保留，请先保存需要的内容。继续吗？'
          : 'Replacing the PDF clears unsaved responses and selections. Personal notes remain. Continue?',
      )
    )
      return
    stop()
    setUploading(true)
    setError('')
    try {
      await papersApi.uploadFulltext(paperId, file)
      if (alive.current) setRevision((value) => value + 1)
    } catch (reason) {
      if (alive.current)
        setError(
          safeErrorMessage(
            reason,
            zh
              ? 'PDF 导入失败，原有记录未被修改。'
              : 'PDF import failed. Existing records are unchanged.',
          ),
        )
    } finally {
      if (alive.current) setUploading(false)
    }
  }
  const openNote = async (
    turn?: ReadingTurn,
    cardType: 'reading_note' | 'foundation' | 'skill' = 'reading_note',
  ) => {
    if (opening || form) return
    const targetCategory = category.trim()
    if (!targetCategory) {
      setError(zh ? '请先填写研究分类。' : 'Enter a research category first.')
      return
    }
    const original = turn ? turn.selection : selection.trim()
    const pageNo = turn?.page || pageNumber
    const hash = turn?.hash || document?.content_hash || ''
    const source = original ? `${locationText(pageNo, hash)}\n${original}` : ''
    const ai = turn
      ? `${outputText(turn)}\n\n${locationText(pageNo, hash)}\n${turn.result.sources.map((item) => `[${item.label}] ${item.paper_id || paperId}；${locationText(item.page_number, item.content_hash)}\n${item.excerpt}`).join('\n\n')}\n\n材料范围：${turn.result.coverage}；模型 ${turn.result.provider || '—'}/${turn.result.model || '—'}；AI 建议，不代表论文事实。`
      : ''
    const metadata = {
      source_paper_id: paperId,
      source_paper_title: document?.title || paper?.title || paperId,
      source_paper_doi: paper?.doi || null,
      category: targetCategory,
    }
    if (cardType !== 'reading_note') {
      const titleQuestion = turn ? turn.question : question.trim()
      setForm({
        editing: null,
        personalDraft: personal.trim(),
        prefill: {
          ...metadata,
          title: `${cardType === 'foundation' ? (zh ? '基础知识' : 'Foundation') : zh ? '写作与阅读技能' : 'Writing & reading skill'}：${titleQuestion.slice(0, 80) || metadata.source_paper_title}`,
          card_type: cardType,
          content: [
            source && `论文原文摘录\n${source}`,
            ai && `AI 学习建议（请核对）\n${ai}`,
          ]
            .filter(Boolean)
            .join('\n\n'),
          notes: personal.trim(),
          tags: [
            cardType === 'foundation'
              ? '前置知识'
              : turn?.mode === 'writing'
                ? '写作样例'
                : '阅读技能',
            ...(paper?.venue ? [`期刊/发表源：${paper.venue}`] : []),
          ],
        },
      })
      return
    }
    const request = ++noteRequest.current
    setOpening(true)
    setError('')
    try {
      const { data } = await knowledgeApi.list(targetCategory, {
        source_paper_id: paperId,
        card_type: 'reading_note',
        page_size: 1,
      })
      if (!alive.current || request !== noteRequest.current) return
      const existing = data.items[0] || null
      const notebook = readingNote(existing?.card_data)
      const section =
        modes.find((item) => item.key === turn?.mode)?.section || 'overview'
      const entry = notebook.sections[section] || emptyEntry()
      const turnKey = turn
        ? noteTurnKey(paperId, targetCategory, turn)
        : undefined
      const alreadySaved = Boolean(
        existing && turnKey && savedTurns.get(turnKey)?.has(existing.id),
      )
      const merged = {
        paper_notes: [entry.paper_notes, !alreadySaved && source]
          .filter(Boolean)
          .join('\n\n'),
        ai_suggestions: [entry.ai_suggestions, !alreadySaved && ai]
          .filter(Boolean)
          .join('\n\n'),
        user_notes: [entry.user_notes, personal.trim()]
          .filter(Boolean)
          .join('\n\n'),
      }
      if (Object.values(merged).some((value) => value.length > 20000)) {
        setError(
          zh
            ? '合并后超过笔记单栏长度，请先导出或精简已有笔记。'
            : 'The combined note is too long. Export or shorten existing notes first.',
        )
        return
      }
      const prefill = {
        ...metadata,
        title: `${zh ? '阅读笔记' : 'Reading note'}：${metadata.source_paper_title}`,
        card_type: 'reading_note',
        card_data: {
          ...notebook,
          status: 'reading' as const,
          sections: { ...notebook.sections, [section]: merged },
        },
      }
      setForm({
        readingTurn: turn,
        personalDraft: personal.trim(),
        editing: existing
          ? { ...existing, card_data: prefill.card_data }
          : null,
        prefill: existing ? null : prefill,
      })
    } catch (reason) {
      if (alive.current && request === noteRequest.current)
        setError(
          safeErrorMessage(
            reason,
            zh
              ? '现有笔记读取失败，未新建重复条目。请重试。'
              : 'Could not load existing notes. No duplicate was created. Retry.',
          ),
        )
    } finally {
      if (alive.current && request === noteRequest.current) setOpening(false)
    }
  }
  const navigatePage = (value: number) => {
    if (
      Number.isInteger(value) &&
      value >= 1 &&
      value <= (document?.page_count || 1)
    )
      setPageNumber(value)
    else setPageInput(String(pageNumber))
  }
  const confirmLeave = () =>
    !(
      turns.some(
        (turn) => !savedTurns.get(noteTurnKey(paperId, category, turn))?.size,
      ) || personal.trim()
    ) ||
    window.confirm(
      zh
        ? '当前伴读回答与个人记录可能尚未保存。确认离开吗？'
        : 'Reading responses and personal notes may be unsaved. Leave this paper?',
    )
  const reload = () => {
    if (confirmLeave()) setRevision((value) => value + 1)
  }
  const pdfUrl = readerAssetUrl(document?.pdf_url, paperId)
  const imageUrl = readerAssetUrl(page?.image_url, paperId)
  const title =
    document?.title || paper?.title || (zh ? '正在读取论文…' : 'Loading paper…')
  const canAsk = Boolean(page && !pageLoading && !busy && !uploading)

  return (
    <main
      className="paper-reader"
      aria-label={zh ? '论文原文伴读' : 'Paper reading workspace'}
    >
      <header className="reader-heading">
        <div>
          <Link
            to="/assistant"
            className="reader-back"
            onClick={(event) => {
              if (!confirmLeave()) event.preventDefault()
            }}
          >
            <ArrowLeft size={15} />
            {zh ? '返回科研问答' : 'Back to research chat'}
          </Link>
          <h1>{title}</h1>
          <p>
            {zh
              ? '伴读仅使用当前论文的指定页面与选段。先读原文，再核对解释，最后保存自己的理解。'
              : 'Work with a specific page and passage. Read the original, check the explanation, then record your own understanding.'}
          </p>
        </div>
        <div className="reader-actions">
          <button
            className="reader-button"
            onClick={() => uploadRef.current?.click()}
            disabled={uploading || loading}
          >
            {uploading ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <FileUp size={15} />
            )}
            {zh
              ? document?.available
                ? '替换 PDF'
                : '导入全文 PDF'
              : document?.available
                ? 'Replace PDF'
                : 'Import PDF'}
          </button>
          <button
            className="reader-button"
            onClick={reload}
            disabled={busy || uploading}
          >
            {zh ? '重新载入' : 'Reload'}
          </button>
        </div>
        <input
          ref={uploadRef}
          type="file"
          accept=".pdf,application/pdf"
          hidden
          aria-label={zh ? '选择伴读 PDF' : 'Choose reading PDF'}
          onChange={(event) => {
            void upload(event.target.files?.[0])
            event.target.value = ''
          }}
        />
      </header>
      {error && (
        <div role="alert" className="reader-error">
          {error}
        </div>
      )}
      {document?.warnings?.map((warning, index) => (
        <p key={index} className="reader-status">
          {warning}
        </p>
      ))}
      <div className="reader-workspace">
        <section
          className="reader-original"
          aria-label={zh ? '论文原文' : 'Original paper'}
        >
          <div className="reader-toolbar">
            <button
              aria-label={zh ? '上一页' : 'Previous page'}
              className="reader-button"
              disabled={!document?.available || pageNumber <= 1}
              onClick={() => navigatePage(pageNumber - 1)}
            >
              <ChevronLeft size={15} />
            </button>
            <label>
              {zh ? 'PDF 页' : 'PDF page'}{' '}
              <input
                aria-label={zh ? 'PDF 页码' : 'PDF page number'}
                className="reader-page-number"
                type="number"
                min={1}
                max={document?.page_count || 1}
                value={pageInput}
                disabled={!document?.available}
                onChange={(event) => setPageInput(event.target.value)}
                onBlur={() => navigatePage(Number(pageInput))}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') navigatePage(Number(pageInput))
                }}
              />{' '}
              / {document?.page_count || '—'}
            </label>
            <button
              aria-label={zh ? '下一页' : 'Next page'}
              className="reader-button"
              disabled={
                !document?.available || pageNumber >= document.page_count
              }
              onClick={() => navigatePage(pageNumber + 1)}
            >
              <ChevronRight size={15} />
            </button>
            <div className="reader-actions">
              <button
                className="reader-button"
                aria-pressed={view === 'text'}
                onClick={() => setView('text')}
              >
                {zh ? '可选文字' : 'Selectable text'}
              </button>
              <button
                className="reader-button"
                aria-pressed={view === 'image'}
                onClick={() => setView('image')}
              >
                {zh ? '原页图像' : 'Page image'}
              </button>
              {pdfUrl && (
                <a
                  className="reader-button"
                  href={`${pdfUrl}#page=${pageNumber}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink size={14} />
                  PDF
                </a>
              )}
            </div>
          </div>
          {loading ? (
            <div className="reader-empty" role="status">
              <Loader2 className="animate-spin" />
              {zh ? '正在读取论文材料…' : 'Loading paper material…'}
            </div>
          ) : !document?.available ? (
            <div className="reader-empty">
              <BookOpen size={28} />
              <h2>
                {zh ? '先准备可阅读的全文' : 'Prepare the full text first'}
              </h2>
              <p>
                {zh
                  ? '当前没有可读取的本地 PDF。可以导入你有权使用的 PDF，或返回搜索页获取开放全文。没有全文时仍可手工整理阅读笔记。'
                  : 'No local PDF is available. Import a PDF you can use or obtain open full text from Search. Manual notes remain available.'}
              </p>
              <Link to="/search" className="reader-back">
                {zh ? '前往文献检索' : 'Go to literature search'}
              </Link>
            </div>
          ) : (
            <div className="reader-page-content custom-scrollbar">
              {pageLoading ? (
                <p role="status" className="reader-status">
                  {zh ? '正在读取本页…' : 'Loading page…'}
                </p>
              ) : (
                page && (
                  <>
                    <p className="reader-status">
                      {zh ? '材料范围：' : 'Coverage: '}
                      {page.coverage}
                    </p>
                    {page.text_truncated && (
                      <p className="reader-status">
                        {zh
                          ? '本页提取文字过长，当前仅显示部分内容；其余内容请打开 PDF 或切换原页图像查看。'
                          : 'Extracted text is truncated. Open the PDF or page image for the remainder.'}
                      </p>
                    )}
                    {page.warnings?.map((warning, index) => (
                      <p key={index} className="reader-status">
                        {warning}
                      </p>
                    ))}
                    {view === 'text' ? (
                      <>
                        <p className="reader-hint">
                          {zh
                            ? '用鼠标或键盘选中下面的原文，片段会进入右侧。本页文字按 PDF 提取顺序显示，多栏或公式请结合原页核对。'
                            : 'Select a passage below. Extracted text follows PDF order; check the original page for columns and formulas.'}
                        </p>
                        {page.text ? (
                          <div
                            ref={textRef}
                            tabIndex={0}
                            className="reader-page-text"
                            aria-label={
                              zh ? '本页可选原文' : 'Selectable page text'
                            }
                            onMouseUp={pickSelection}
                            onKeyUp={pickSelection}
                          >
                            {page.text}
                          </div>
                        ) : (
                          <p className="reader-empty">
                            {zh
                              ? '本页没有可提取文字，可能是扫描页。切换原页图像后可按页请求读图或公式解释。'
                              : 'No text could be extracted. View the page image and request visual assistance for this page.'}
                          </p>
                        )}
                      </>
                    ) : imageUrl ? (
                      <img
                        key={imageUrl}
                        className="reader-page-image"
                        src={imageUrl}
                        alt={
                          zh
                            ? `论文 PDF 第 ${pageNumber} 页`
                            : `PDF page ${pageNumber}`
                        }
                        onError={(event) => {
                          event.currentTarget.hidden = true
                          setError(
                            zh
                              ? '原页图像读取失败，请重试或打开本地 PDF。'
                              : 'Page image unavailable. Retry or open the PDF.',
                          )
                        }}
                      />
                    ) : (
                      <p className="reader-empty">
                        {zh
                          ? '本页暂不能显示图像。可打开 PDF 对照阅读。'
                          : 'No page image is available. Open the PDF to read alongside.'}
                      </p>
                    )}
                  </>
                )
              )}
            </div>
          )}
        </section>
        <section
          className="reader-assist"
          aria-label={zh ? '论文伴读助手' : 'Paper reading assistant'}
        >
          <div className="reader-controls">
            <div className="reader-mode-grid">
              {modes.map((item) => (
                <button
                  key={item.key}
                  className="reader-button"
                  disabled={busy}
                  aria-pressed={mode === item.key}
                  onClick={() => {
                    setMode(item.key)
                    if (item.key === 'figure' || item.key === 'math')
                      setView('image')
                  }}
                >
                  {zh ? item.zh : item.en}
                </button>
              ))}
            </div>
            <label className="reader-label" htmlFor="reader-selection">
              {zh
                ? `选中原文（PDF 第 ${pageNumber} 页）`
                : `Selected passage (PDF page ${pageNumber})`}
            </label>
            <textarea
              id="reader-selection"
              className="reader-textarea"
              rows={3}
              maxLength={5000}
              value={selection}
              disabled={busy || !page}
              onChange={(event) => setSelection(event.target.value)}
              placeholder={
                zh
                  ? '左侧选中后自动填入，也可粘贴本页原文。服务器会核对片段确实来自这一页。'
                  : 'Select on the left, or paste text from this page. The server verifies its location.'
              }
            />
            <label className="reader-label" htmlFor="reader-question">
              {zh
                ? '我想弄懂什么（可选）'
                : 'What would you like to understand? (optional)'}
            </label>
            <textarea
              id="reader-question"
              className="reader-textarea"
              maxLength={2000}
              rows={2}
              value={question}
              disabled={busy}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={
                zh
                  ? mode === 'writing'
                    ? '例如：这段摘要怎样衔接研究问题、方法和结果？只提炼写作功能，不照搬句子。'
                    : '例如：这里的马尔科夫假设是什么？给一个能手算的例子。'
                  : 'For example: Explain this assumption with a small worked example.'
              }
            />
            <p className="reader-hint">
              {zh
                ? mode === 'figure' || mode === 'math'
                  ? `将指定 PDF 第 ${pageNumber} 页图像发送给已配置的视觉模型。未配置视觉模型会提示设置，不会假称看过图片。`
                  : mode === 'writing'
                    ? '先整理当前论文的指定页样例。它是样本观察，不是期刊官方规范，也不代表所有同刊文章。'
                    : mode === 'extension'
                      ? '提供后续阅读的方向、前置知识和可检索关键词；新论文与链接仍需通过真实检索核实。'
                      : mode === 'translate'
                        ? '选段优先；未选文字时翻译本页可提取文字（最多 8000 字符）。译文与教学说明分开显示。'
                        : '选段优先；未选文字时使用当前页材料，不把整篇论文一次塞给模型。'
                : mode === 'figure' || mode === 'math'
                  ? 'Sends this page image to your configured vision model. Missing vision configuration is reported explicitly.'
                  : 'Uses the selected passage or this page, not the entire paper at once.'}
            </p>
            <div className="reader-actions">
              <button
                className="reader-button primary"
                disabled={!canAsk}
                onClick={() => void ask()}
              >
                {busy ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : (
                  <Sparkles size={15} />
                )}
                {busy
                  ? `${zh ? '处理中' : 'Working'} ${elapsed}s`
                  : zh
                    ? '开始本次伴读'
                    : 'Ask about this page'}
              </button>
              {busy && (
                <button className="reader-button" onClick={stop}>
                  {zh ? '停止等待' : 'Stop waiting'}
                </button>
              )}
              {mode === 'extension' && (
                <button
                  className="reader-button"
                  onClick={() => setShowRecommendations((value) => !value)}
                >
                  {zh
                    ? showRecommendations
                      ? '收起真实论文推荐'
                      : '检索真实论文推荐'
                    : showRecommendations
                      ? 'Hide paper recommendations'
                      : 'Find real related papers'}
                </button>
              )}
            </div>
            {busy && (
              <p className="reader-status">
                {zh
                  ? '停止等待不会保证取消服务商已经处理的请求或费用。'
                  : 'Stopping does not guarantee cancellation of a request or provider charges.'}
              </p>
            )}
            {mode === 'extension' && showRecommendations && (
              <div className="mt-4">
                <SimilarPapers
                  paperId={paperId}
                  onSelect={(item) => {
                    if (confirmLeave())
                      navigate(
                        `/assistant?paper=${encodeURIComponent(item.id)}&category=${encodeURIComponent(category)}`,
                      )
                  }}
                />
              </div>
            )}
            <div className="mt-3" hidden={mode !== 'writing'}>
              <button
                className="reader-button"
                onClick={() => {
                  setWritingSamplesActivated(true)
                  setShowWritingSamples((value) => !value)
                }}
              >
                {zh
                  ? showWritingSamples
                    ? '收起多篇样例对照'
                    : '多篇写作样例对照（2–5 篇）'
                  : showWritingSamples
                    ? 'Hide sample comparison'
                    : 'Compare 2–5 writing samples'}
              </button>
              <div hidden={!showWritingSamples}>
                {writingSamplesActivated && (
                  <WritingSamplesPanel
                    currentPaperId={paperId}
                    category={category}
                    chinese={zh}
                    onReview={(prefill) => setForm({ editing: null, prefill })}
                    onReadSource={(id, sourcePage, hash) => {
                      if (confirmLeave())
                        navigate(
                          `/assistant?paper=${encodeURIComponent(id)}&page=${sourcePage}&version=${encodeURIComponent(hash)}&category=${encodeURIComponent(category)}`,
                        )
                    }}
                  />
                )}
              </div>
            </div>
          </div>
          <div
            ref={responsesRef}
            className="reader-response-list custom-scrollbar"
            aria-live="polite"
          >
            {!turns.length && (
              <p className="reader-hint">
                {zh
                  ? '每次回答会保留页码、材料版本和模型信息。本篇临时保留最近 12 次回答；需要长期保留，请确认后保存到现有知识库。'
                  : 'Responses keep their page, document version and model. The latest 12 are temporary here; confirm and save useful work to your knowledge base.'}
              </p>
            )}
            {turns.map((turn) => (
              <article className="reader-response" key={turn.id}>
                <div className="reader-response-header">
                  <span>
                    {
                      modes.find((item) => item.key === turn.mode)?.[
                        zh ? 'zh' : 'en'
                      ]
                    }{' '}
                    · {zh ? 'PDF 第' : 'PDF page'} {turn.page}
                    {zh ? ' 页' : ''}
                  </span>
                  <button
                    className="reader-back"
                    disabled={turn.hash !== document?.content_hash}
                    onClick={() => {
                      setPageNumber(turn.page)
                      setView('text')
                    }}
                  >
                    {zh ? '回原文页' : 'Go to source page'}
                  </button>
                </div>
                {turn.question && (
                  <p className="reader-status">
                    {zh ? '本次问题：' : 'Question: '}
                    {turn.question}
                  </p>
                )}
                {turn.selection && (
                  <details>
                    <summary className="reader-status">
                      {zh ? '本次选段' : 'Selected passage'}
                    </summary>
                    <blockquote className="reader-selection">
                      {turn.selection}
                    </blockquote>
                  </details>
                )}
                {!hasParts(turn.result) && (
                  <div className="reader-response-text">
                    {turn.result.answer}
                  </div>
                )}
                {turn.result.source_notes && (
                  <>
                    <h3>{sourceNotesTitle(turn.result, zh)}</h3>
                    <div className="reader-response-text">
                      {turn.result.source_notes}
                    </div>
                  </>
                )}
                {turn.result.teaching_notes && (
                  <>
                    <h3>{teachingNotesTitle(turn.result, zh)}</h3>
                    <div className="reader-response-text">
                      {turn.result.teaching_notes}
                    </div>
                  </>
                )}
                {turn.result.exercise && (
                  <>
                    <h3>{zh ? '自己动手试一试' : 'Try it yourself'}</h3>
                    <div className="reader-response-text">
                      {turn.result.exercise}
                    </div>
                  </>
                )}
                {turn.result.source_grounding && (
                  <section aria-label={zh ? '原文摘录匹配检查' : 'Source quotation matching'}>
                    <h3>{zh ? '原文摘录匹配检查' : 'Source quotation matching'}</h3>
                    <div className="reader-response-text">
                      {sourceGroundingText(turn.result.source_grounding, zh)}
                    </div>
                  </section>
                )}
                {Boolean(turn.result.calculation_checks?.length) && (
                  <section
                    aria-label={
                      zh ? '基础算例数值验算' : 'Numeric example checks'
                    }
                  >
                    <h3>
                      {zh
                        ? '基础算例数值验算（非数学证明）'
                        : 'Numeric checks (not mathematical proof)'}
                    </h3>
                    <p className="reader-status">
                      {zh
                        ? calculationScope
                        : 'Checks only these inputs for the specified metric, not paper provenance, metric selection, formula transcription or derivations.'}
                    </p>
                    {turn.result.calculation_checks?.map((check, index) => (
                      <div
                        key={index}
                        role={
                          check.status === 'mismatch' ||
                          check.status === 'unsupported'
                            ? 'alert'
                            : undefined
                        }
                        className={
                          check.status === 'mismatch' ||
                          check.status === 'unsupported'
                            ? 'reader-error reader-response-text'
                            : 'reader-response-text'
                        }
                      >
                        {calculationText(check, zh)}
                      </div>
                    ))}
                  </section>
                )}
                <p className="reader-status">
                  {zh ? '材料范围' : 'Coverage'}: {turn.result.coverage} ·{' '}
                  {turn.result.provider || '—'}/{turn.result.model || '—'} ·{' '}
                  {turn.result.total_tokens ?? '—'} Token ·{' '}
                  {turn.result.model_completed
                    ? zh
                      ? '已返回，待核对'
                      : 'Returned; review needed'
                    : zh
                      ? '模型未完成'
                      : 'Model incomplete'}
                  {turn.result.fallback_used &&
                    (zh ? ' · 使用回退' : ' · Fallback used')}
                </p>
                {!turn.result.model_completed && !turn.result.total_tokens && (
                  <p className="reader-status">
                    {zh
                      ? '未完成或未返回用量不代表免费，费用请以服务商记录为准。'
                      : 'An incomplete response or missing usage does not imply zero cost; check your provider records.'}
                  </p>
                )}
                {Boolean(turn.result.model_attempts?.length) && (
                  <details>
                    <summary className="reader-status">
                      {zh ? '模型调用记录' : 'Model request activity'}
                    </summary>
                    {turn.result.model_attempts?.map((attempt, index) => (
                      <p key={index} className="reader-status">
                        {String(attempt.provider || '—')}/
                        {String(attempt.model || '—')} ·{' '}
                        {String(attempt.status || '—')}
                        {attempt.error_type
                          ? ` · ${String(attempt.error_type)}`
                          : ''}{' '}
                        · {zh ? '请求' : 'Requests'}{' '}
                        {String(
                          attempt.request_attempts ?? attempt.requests ?? '—',
                        )}{' '}
                        / {zh ? '响应' : 'Responses'}{' '}
                        {String(attempt.responses_received ?? '—')}
                      </p>
                    ))}
                  </details>
                )}
                {turn.result.warnings?.map((warning, index) => (
                  <p key={index} className="reader-status">
                    {warning}
                  </p>
                ))}
                <details>
                  <summary className="reader-status">
                    {zh ? '来源与材料版本' : 'Sources & document version'}
                  </summary>
                  <p className="reader-status">{turn.hash}</p>
                  {turn.result.sources.map((source, index) => (
                    <button
                      key={`${source.label}-${index}`}
                      className="reader-button reader-source"
                      disabled={source.content_hash !== document?.content_hash}
                      onClick={() => {
                        setPageNumber(source.page_number)
                        setView('text')
                      }}
                    >
                      [{source.label}] PDF {source.page_number}
                      <br />
                      {source.excerpt}
                    </button>
                  ))}
                </details>
                <div className="reader-actions">
                  <button
                    className="reader-button"
                    disabled={opening || !turn.result.model_completed}
                    onClick={() => void openNote(turn)}
                  >
                    <Save size={14} />
                    {savedTurns.has(noteTurnKey(paperId, category, turn))
                      ? zh
                        ? '已整理，继续编辑'
                        : 'Added · continue editing'
                      : zh
                        ? '核对并加入阅读笔记'
                        : 'Review in reading note'}
                  </button>
                  <button
                    className="reader-button"
                    disabled={opening || !turn.result.model_completed}
                    onClick={() =>
                      void openNote(
                        turn,
                        turn.mode === 'prerequisite' ? 'foundation' : 'skill',
                      )
                    }
                  >
                    {zh
                      ? turn.mode === 'prerequisite'
                        ? '整理为基础知识卡'
                        : '整理为技能 / 写作卡'
                      : 'Create reusable card'}
                  </button>
                </div>
              </article>
            ))}
          </div>
          <div className="reader-personal">
            <label className="reader-label" htmlFor="reader-category">
              {zh ? '保存到研究分类' : 'Research category for saving'}
            </label>
            <input
              id="reader-category"
              list="reader-categories"
              className="reader-select"
              maxLength={100}
              value={category}
              disabled={opening}
              onChange={(event) => setCategory(event.target.value)}
            />
            <datalist id="reader-categories">
              {categories.map((item) => (
                <option key={item.name} value={item.name} />
              ))}
            </datalist>
            <label className="reader-label" htmlFor="reader-personal">
              {zh
                ? '我的理解 / 纸笔推导记录（不会自动发给 AI）'
                : 'My understanding / handwritten work (not sent to AI)'}
            </label>
            <textarea
              id="reader-personal"
              className="reader-textarea"
              maxLength={15000}
              rows={3}
              value={personal}
              onChange={(event) => setPersonal(event.target.value)}
              placeholder={
                zh
                  ? '例如：我已手算两次状态转移；仍不理解公式 4 的维度，下一次回看第 6 页。'
                  : 'Record your own explanation, derivation, or remaining questions.'
              }
            />
            <div className="reader-actions">
              <button
                className="reader-button"
                disabled={opening || loading}
                onClick={() => void openNote()}
              >
                {opening ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <BookOpen size={14} />
                )}
                {zh ? '打开 / 保存阅读笔记' : 'Open / save reading note'}
              </button>
              {lastSavedNote && (
                <>
                  <span className="reader-status" role="status">
                    {zh ? '已保存到' : 'Saved to'} {lastSavedNote.category}
                  </span>
                  <button
                    className="reader-button"
                    onClick={() => {
                      if (!confirmLeave()) return
                      const store = useKnowledgeStore.getState()
                      store.setSelectedCategory(lastSavedNote.category)
                      store.setSearchQuery('')
                      navigate('/knowledge')
                    }}
                  >
                    {zh ? '查看已保存笔记' : 'View saved note'}
                  </button>
                </>
              )}
            </div>
            <p className="reader-status">
              {zh
                ? '个人记录与 AI 建议分栏保存。新输入和未整理的回答需在编辑器中确认保存；已整理标记仅在当前工作区保留。'
                : 'Keep personal notes separate from AI suggestions. Save new work in the editor; added-answer markers last only in this workspace.'}
            </p>
          </div>
        </section>
      </div>
      <KnowledgeForm
        open={Boolean(form)}
        editingItem={form?.editing || null}
        prefill={form?.prefill || null}
        categories={categories}
        onClose={() => setForm(null)}
        onSuccess={(saved?: KnowledgeItem) => {
          if (saved) {
            setLastSavedNote({ id: saved.id, category: saved.category })
            if (
              form?.readingTurn &&
              saved.card_type === 'reading_note' &&
              saved.source_paper_id === paperId
            ) {
              const key = noteTurnKey(paperId, saved.category, form.readingTurn)
              setSavedTurns((previous) => {
                const next = new Map(previous)
                next.set(key, new Set([...(previous.get(key) || []), saved.id]))
                return next
              })
            }
          }
          void knowledgeApi
            .getCategories()
            .then(({ data }) => {
              if (alive.current) setCategories(data)
            })
            .catch(() => undefined)
          setPersonal((value) =>
            form?.personalDraft !== undefined &&
            value.trim() === form.personalDraft
              ? ''
              : value,
          )
        }}
      />
    </main>
  )
}

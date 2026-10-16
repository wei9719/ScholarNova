import { useEffect, useRef, useState } from 'react'
import { Loader2, Save } from 'lucide-react'
import {
  readerApi,
  type ReadingLibrary,
  type ReadingResponse,
} from '@/api/readerClient'
import type { KnowledgeItem } from '@/api/types'
import { safeErrorMessage } from '@/utils/safeError'

interface Props {
  currentPaperId: string
  category: string
  chinese: boolean
  onReview: (item: Partial<KnowledgeItem>) => void
  onReadSource: (paperId: string, page: number, hash: string) => void
}

export default function WritingSamplesPanel({
  currentPaperId,
  category,
  chinese: zh,
  onReview,
  onReadSource,
}: Props) {
  const [library, setLibrary] = useState<ReadingLibrary | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [question, setQuestion] = useState('')
  const [personal, setPersonal] = useState('')
  const [result, setResult] = useState<ReadingResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const pending = useRef<AbortController | null>(null)
  const revisionRef = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setLibrary(null)
    setSelected([])
    setResult(null)
    void readerApi
      .library(controller.signal)
      .then(({ data }) => {
        if (controller.signal.aborted) return
        setLibrary(data)
        setSelected(
          data.papers.some((item) => item.paper_id === currentPaperId)
            ? [currentPaperId]
            : [],
        )
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            safeErrorMessage(
              reason,
              zh
                ? '本地论文列表读取失败，请重试。'
                : 'Could not load local papers. Retry.',
            ),
          )
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => {
      controller.abort()
      pending.current?.abort()
      pending.current = null
      revisionRef.current++
    }
  }, [currentPaperId, revision, zh])

  useEffect(() => {
    if (!busy) return
    const started = Date.now()
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [busy])

  const invalidate = () => {
    pending.current?.abort()
    pending.current = null
    revisionRef.current++
    setBusy(false)
    setResult(null)
    setError('')
  }
  const toggle = (id: string) => {
    if (!selected.includes(id) && selected.length >= 5) return
    if (
      result &&
      !window.confirm(
        zh
          ? '改变样本会清空当前未保存的对照结果。继续吗？'
          : 'Changing samples clears this unsaved comparison. Continue?',
      )
    )
      return
    invalidate()
    setSelected((previous) =>
      previous.includes(id)
        ? previous.filter((value) => value !== id)
        : [...previous, id],
    )
  }
  const generate = async () => {
    if (busy || selected.length < 2 || selected.length > 5) return
    const controller = new AbortController()
    pending.current = controller
    const requestRevision = ++revisionRef.current
    const ids = [...selected]
    setBusy(true)
    setElapsed(0)
    setError('')
    setResult(null)
    try {
      const hashes: Record<string, string> = {}
      // PDF parsing has a bounded worker pool; serialize these small snapshots.
      for (const id of ids) {
        const { data: item } = await readerApi.document(id, controller.signal)
        if (
          controller.signal.aborted ||
          requestRevision !== revisionRef.current
        )
          return
        if (item.paper_id !== id || !item.available || !item.content_hash)
          throw new Error('missing sample')
        hashes[item.paper_id] = item.content_hash
      }
      const { data } = await readerApi.writingSamples(
        { paper_ids: ids, question: question.trim(), content_hashes: hashes },
        controller.signal,
      )
      if (
        controller.signal.aborted ||
        pending.current !== controller ||
        requestRevision !== revisionRef.current
      )
        return
      if (
        data.sources.some(
          (source) =>
            !source.paper_id ||
            !ids.includes(source.paper_id) ||
            hashes[source.paper_id] !== source.content_hash,
        )
      ) {
        setError(
          zh
            ? '样例来源与选定论文版本不一致，请重新获取后对照。'
            : 'Sample sources do not match the selected document versions. Reload and retry.',
        )
        return
      }
      setResult(data)
    } catch (reason) {
      if (!controller.signal.aborted && requestRevision === revisionRef.current)
        setError(
          safeErrorMessage(
            reason,
            zh
              ? '写作对照未完成。请确认所选论文都有可提取文字的全文，再重试；个人记录保留。'
              : 'Comparison did not finish. Check that each selected paper has readable full text, then retry. Personal notes are unchanged.',
          ),
        )
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setBusy(false)
      }
    }
  }
  const review = () => {
    if (!result?.model_completed || !category.trim()) return
    const papers =
      library?.papers.filter((item) => selected.includes(item.paper_id)) || []
    const parts = [
      result.source_notes &&
        `样本观察（AI 归纳，需核对）\n${result.source_notes}`,
      result.teaching_notes &&
        `写作功能与模板建议（非期刊官方规范）\n${result.teaching_notes}`,
      result.exercise && `练习建议\n${result.exercise}`,
    ].filter(Boolean)
    if (!parts.length) parts.push(result.answer)
    const provenance = result.sources
      .map(
        (source) =>
          `[${source.label}] ${source.paper_title || source.paper_id}\n发表源：${source.venue || '未提供'}\nPDF 第 ${source.page_number} 页；文件版本 ${source.content_hash}\n${source.excerpt}\n伴读位置：/assistant?paper=${encodeURIComponent(source.paper_id!)}&page=${source.page_number}&version=${encodeURIComponent(source.content_hash)}`,
      )
      .join('\n\n')
    onReview({
      title: `${zh ? '多篇写作样例对照' : 'Writing sample comparison'}：${
        question.trim().slice(0, 60) ||
        papers
          .map((item) => item.venue || item.title)
          .slice(0, 2)
          .join(' / ')
      }`,
      category: category.trim(),
      card_type: 'skill',
      source_paper_id: null,
      source_paper_title: zh
        ? `${papers.length} 篇本地全文样本`
        : `${papers.length} local full-text samples`,
      source_paper_doi: null,
      tags: [
        '写作样例',
        '多篇对照',
        ...Array.from(
          new Set(papers.map((item) => item.venue).filter(Boolean)),
        ).map((venue) => `期刊/发表源：${venue}`),
      ],
      content: [
        ...parts,
        `材料范围：${result.coverage}`,
        ...result.warnings,
        `来源快照\n${provenance}`,
        `模型：${result.provider || '—'}/${result.model || '—'}；AI 建议，请回原文核对。`,
      ]
        .filter(Boolean)
        .join('\n\n'),
      notes: personal.trim(),
    })
  }

  return (
    <section
      className="reader-samples"
      aria-label={zh ? '多篇写作样例对照' : 'Multiple writing samples'}
    >
      <h3>{zh ? '多篇写作样例对照' : 'Compare writing samples'}</h3>
      <p className="reader-hint">
        {zh
          ? '选择 2–5 篇已导入本地全文的论文。只比较每篇首、次、末页的有界摘录，整理摘要、标题、图表衔接等写作功能，不代表整本期刊的统一规范。'
          : 'Choose 2–5 local full-text papers. Compare bounded excerpts from their first, second and last pages, not journal-wide rules.'}
      </p>
      {loading ? (
        <p role="status" className="reader-status">
          {zh ? '正在读取本地全文列表…' : 'Loading local full-text papers…'}
        </p>
      ) : (
        <>
          {library?.warnings.map((warning, index) => (
            <p key={index} className="reader-status">
              {warning}
            </p>
          ))}
          <div className="reader-sample-list custom-scrollbar">
            {library?.papers.map((item) => (
              <label key={item.paper_id} className="reader-sample-choice">
                <input
                  type="checkbox"
                  checked={selected.includes(item.paper_id)}
                  disabled={
                    !selected.includes(item.paper_id) && selected.length >= 5
                  }
                  onChange={() => toggle(item.paper_id)}
                />
                <span>
                  {item.title}
                  <small>
                    {[
                      item.year,
                      item.venue,
                      item.source === 'uploaded'
                        ? zh
                          ? '已导入 PDF'
                          : 'Imported PDF'
                        : zh
                          ? '开放全文缓存'
                          : 'Open full-text cache',
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </small>
                </span>
              </label>
            ))}
          </div>
          {!library?.papers.length && (
            <p className="reader-status">
              {zh
                ? '暂无本地全文。先从论文详情导入或缓存 PDF，再回来对照。'
                : 'No local full text. Import or cache PDFs from paper details first.'}
            </p>
          )}
          <p className="reader-status">
            {zh
              ? `已选 ${selected.length}/5 篇；列表最多显示 ${library?.limit || 50} 篇本地材料。`
              : `${selected.length}/5 selected; up to ${library?.limit || 50} local papers are listed.`}
          </p>
        </>
      )}
      <label className="reader-label" htmlFor="writing-sample-focus">
        {zh ? '比较重点（可选）' : 'Comparison focus (optional)'}
      </label>
      <textarea
        id="writing-sample-focus"
        className="reader-textarea"
        maxLength={2000}
        rows={2}
        value={question}
        disabled={busy}
        onChange={(event) => {
          invalidate()
          setQuestion(event.target.value)
        }}
        placeholder={
          zh
            ? '例如：比较摘要中问题—方法—结果的衔接，整理可以自己填写的提纲。'
            : 'For example: Compare how the abstracts connect problem, method and result.'
        }
      />
      <div className="reader-actions">
        <button
          className="reader-button primary"
          disabled={
            loading || busy || selected.length < 2 || selected.length > 5
          }
          onClick={() => void generate()}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : null}
          {busy
            ? `${zh ? '对照中' : 'Comparing'} ${elapsed}s`
            : zh
              ? '比较所选样例'
              : 'Compare selected samples'}
        </button>
        {busy && (
          <button className="reader-button" onClick={invalidate}>
            {zh ? '停止等待' : 'Stop waiting'}
          </button>
        )}
        <button
          className="reader-button"
          disabled={busy}
          onClick={() => {
            if (
              !result ||
              window.confirm(
                zh
                  ? '刷新会清空当前未保存的对照结果，继续吗？'
                  : 'Refresh clears the unsaved comparison. Continue?',
              )
            ) {
              invalidate()
              setRevision((value) => value + 1)
            }
          }}
        >
          {zh ? '刷新本地列表' : 'Refresh local list'}
        </button>
      </div>
      {busy && (
        <p className="reader-status">
          {zh
            ? '改变勾选会停止等待并重新确定样本；服务商已经处理的请求仍可能产生费用。'
            : 'Changing selection stops waiting and resets samples; provider charges may still apply.'}
        </p>
      )}
      {error && (
        <p role="alert" className="reader-error">
          {error}
        </p>
      )}
      {result && (
        <article className="reader-sample-result">
          {result.source_notes && (
            <>
              <h4>
                {zh
                  ? '样本观察（AI 归纳，需核对）'
                  : 'Sample observations (AI summary; verify)'}
              </h4>
              <p className="reader-response-text">{result.source_notes}</p>
            </>
          )}
          {result.teaching_notes && (
            <>
              <h4>
                {zh
                  ? '写作功能与模板建议'
                  : 'Writing functions & template suggestions'}
              </h4>
              <p className="reader-response-text">{result.teaching_notes}</p>
            </>
          )}
          {result.exercise && (
            <>
              <h4>{zh ? '自己动手改写' : 'Practise your own writing'}</h4>
              <p className="reader-response-text">{result.exercise}</p>
            </>
          )}
          {!result.source_notes &&
            !result.teaching_notes &&
            !result.exercise && (
              <p className="reader-response-text">{result.answer}</p>
            )}
          <p className="reader-status">
            {result.coverage} · {result.provider || '—'}/{result.model || '—'} ·{' '}
            {result.total_tokens} Token ·{' '}
            {result.model_completed
              ? zh
                ? '待核对'
                : 'Review needed'
              : zh
                ? '模型未完成'
                : 'Model incomplete'}
          </p>
          {result.warnings.map((warning, index) => (
            <p key={index} className="reader-status">
              {warning}
            </p>
          ))}
          <details>
            <summary className="reader-status">
              {zh
                ? `来源快照（${result.sources.length}）`
                : `Source snapshots (${result.sources.length})`}
            </summary>
            {result.sources.map((source, index) => (
              <div key={`${source.label}-${index}`} className="reader-source">
                <button
                  className="reader-back"
                  onClick={() => {
                    if (
                      window.confirm(
                        zh
                          ? '请先保存需要的对照结果。现在前往这篇论文的原文页吗？'
                          : 'Save any comparison you need before leaving. Open this source page?',
                      )
                    )
                      onReadSource(
                        source.paper_id!,
                        source.page_number,
                        source.content_hash,
                      )
                  }}
                >
                  [{source.label}] {source.paper_title} · PDF{' '}
                  {source.page_number}
                </button>
                <p className="reader-status">{source.excerpt}</p>
                <p className="reader-status">{source.content_hash}</p>
              </div>
            ))}
          </details>
        </article>
      )}
      <label className="reader-label" htmlFor="writing-sample-personal">
        {zh
          ? '我的写作观察（不自动发给 AI）'
          : 'My writing observations (not sent to AI)'}
      </label>
      <textarea
        id="writing-sample-personal"
        className="reader-textarea"
        maxLength={15000}
        rows={2}
        value={personal}
        onChange={(event) => setPersonal(event.target.value)}
      />
      <button
        className="reader-button"
        disabled={!result?.model_completed || !category.trim()}
        onClick={review}
      >
        <Save size={14} />
        {zh ? '核对后整理为技能 / 写作卡' : 'Review in a writing skill card'}
      </button>
      <p className="reader-status">
        {zh
          ? `确认后打开现有知识库编辑器，保存分类为“${category.trim() || '请先填写分类'}”；不会自动保存或另建知识库。`
          : `Opens the existing knowledge editor in “${category.trim()}”. Nothing is saved automatically.`}
      </p>
    </section>
  )
}

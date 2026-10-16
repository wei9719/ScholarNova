import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AnalysisResult, PaperDetail as Paper } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import PaperDetail from '../PaperDetail/PaperDetail'

const api = vi.hoisted(() => ({ fulltextStatus: vi.fn(), uploadFulltext: vi.fn(), toastSuccess: vi.fn(), toastError: vi.fn() }))
vi.mock('@/api/client', () => ({ papersApi: api, knowledgeApi: {}, zoteroApi: {} }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: api.toastSuccess, error: api.toastError }) }))

const paper: Paper = {
  id: 'visual-fixture', title: 'Visual fixture', authors: ['Researcher'], abstract: 'Synthetic evidence.',
  year: 2026, venue: null, citation_count: 0, doi: null, url: null, pdf_url: null,
  source: 'arxiv', relevance_score: null, is_open_access: true,
  references: [], citations: [], fields_of_study: [], keywords: [],
  publication_date: null, volume: null, issue: null, pages: null,
}
const fallbackNote = '已提取 2 个图表页面，但视觉模型未完成读取；本次仅依据正文、图注和表格文字分析，可稍后重试图表读取'

function renderDetail(overrides: Partial<AnalysisResult> = {}) {
  const analysis: AnalysisResult = {
    paper_id: paper.id, analysis_type: 'full', summary: '仅依据正文分析。', methodology: null,
    key_findings: [], strengths: [], weaknesses: [], relevance_to_query: null,
    document_coverage: 'fulltext', document_source: 'fulltext:uploaded', document_error: fallbackNote,
    visual_pages_read: 0, model_completed: true, prompt_tokens: 3, completion_tokens: 2,
    total_tokens: 5, created_at: '2026-10-03T00:00:00Z', ...overrides,
  }
  const onAnalyze = vi.fn()
  render(<PaperDetail paper={paper} analysis={analysis} analysisLoading={false}
    evidenceSpans={[]} evidenceLoading={false} onClose={vi.fn()}
    onAnalyze={onAnalyze} onFulltextUploaded={vi.fn()} />)
  return onAnalyze
}

beforeEach(() => {
  vi.resetAllMocks()
  useLocaleStore.getState().setLocale('zh')
  api.fulltextStatus.mockResolvedValue({ data: { available: true, source: 'uploaded', file_size: 100 } })
  delete window.scholarAccess
})
afterEach(() => {
  cleanup()
  useLocaleStore.getState().setLocale('zh')
  useLocaleStore.persist.clearStorage()
  delete window.scholarAccess
})

it('shows extracted-but-unread figures separately from text success and allows retry', async () => {
  const onAnalyze = renderDetail()
  expect(await screen.findByText(fallbackNote)).toBeInTheDocument()
  expect(screen.getByText(/已分析 PDF 文字/)).toBeInTheDocument()
  expect(screen.queryByText(/已读取全文/)).not.toBeInTheDocument()
  expect(screen.getByText(/本次未成功读取图表页面/)).toBeInTheDocument()
  expect(screen.queryByText(/未提取到可用图表页/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '全面分析' }))
  expect(onAnalyze).toHaveBeenCalledOnce()
  expect(screen.getByText('仅依据正文分析。')).toBeInTheDocument()
})

it('does not infer missing figures from legacy zero-page results', async () => {
  renderDetail({ document_error: null })
  await waitFor(() => expect(api.fulltextStatus).toHaveBeenCalledOnce())
  expect(screen.getByText(/本次未成功读取图表页面/)).toBeInTheDocument()
  expect(screen.queryByText(/未提取到可用图表页|视觉模型未完成读取/)).not.toBeInTheDocument()
})

it('keeps the successfully-read page count and document selection warning', async () => {
  renderDetail({ visual_pages_read: 2, document_error: '正文仅提供章节摘录' })
  expect(await screen.findByText('正文仅提供章节摘录')).toBeInTheDocument()
  expect(screen.getByText(/并读取 2 个图表页面/)).toBeInTheDocument()
  expect(screen.queryByText(/本次未成功读取图表页面/)).not.toBeInTheDocument()
})

it('preserves the visual failure explanation when text models also fail', async () => {
  renderDetail({ model_completed: false, total_tokens: 0 })
  expect(await screen.findByText(/PDF 材料已准备，但模型服务未完成本次分析/)).toBeInTheDocument()
  expect(screen.getByText(fallbackNote)).toBeInTheDocument()
  expect(screen.queryByText(/已分析 PDF 文字|已读取全文/)).not.toBeInTheDocument()
})

it('uses an honest English zero-page label rather than claiming extraction failed', async () => {
  useLocaleStore.getState().setLocale('en')
  renderDetail()
  expect(await screen.findByText(fallbackNote)).toBeInTheDocument()
  expect(screen.getByText(/PDF text analyzed/)).toBeInTheDocument()
  expect(screen.queryByText(/Full text read/)).not.toBeInTheDocument()
  expect(screen.getByText(/no visual page was successfully read/)).toBeInTheDocument()
  expect(screen.queryByText(/no visual page was extracted/)).not.toBeInTheDocument()
})

function nativeDetail() {
  const listeners: Array<(event: { paper_id: string }) => void> = []
  const unsubscribe = vi.fn()
  window.scholarAccess = { open: vi.fn(), onImported: vi.fn(callback => { listeners.push(callback); return unsubscribe }) }
  const onUploaded = vi.fn()
  const props = { paper, analysis: null, analysisLoading: false, evidenceSpans: [], evidenceLoading: false, onClose: vi.fn(), onAnalyze: vi.fn(), onFulltextUploaded: onUploaded }
  const view = render(<PaperDetail {...props} />)
  return { ...view, props, onUploaded, listeners, unsubscribe }
}

it('does not let an initial status request overwrite a newer authorized import status', async () => {
  let oldFinish!: (value: any) => void
  api.fulltextStatus.mockReturnValueOnce(new Promise(resolve => { oldFinish = resolve }))
    .mockResolvedValueOnce({ data: { available: true, source: 'uploaded', file_size: 500 } })
  const detail = nativeDetail()
  act(() => detail.listeners.at(-1)!({ paper_id: paper.id }))
  await screen.findByRole('button', { name: '替换全文' })
  await act(async () => oldFinish({ data: { available: false, source: null, file_size: 0 } }))
  expect(screen.getByRole('button', { name: '替换全文' })).toBeInTheDocument()
  expect(detail.onUploaded).toHaveBeenCalledWith(false)
  expect(detail.props.onAnalyze).not.toHaveBeenCalled()
})

it('ignores old-paper native callbacks and pending status responses after switching papers', async () => {
  let oldFinish!: (value: any) => void
  api.fulltextStatus.mockReturnValueOnce(new Promise(resolve => { oldFinish = resolve }))
    .mockResolvedValue({ data: { available: false, source: null, file_size: 0 } })
  const detail = nativeDetail()
  const oldCallback = detail.listeners.at(-1)!
  const second = { ...paper, id: 'second-paper', title: 'Second paper' }
  detail.rerender(<PaperDetail {...detail.props} paper={second} />)
  await waitFor(() => expect(api.fulltextStatus).toHaveBeenCalledWith('second-paper'))
  act(() => { oldCallback({ paper_id: paper.id }); detail.listeners.at(-1)!({ paper_id: paper.id }) })
  await act(async () => oldFinish({ data: { available: true, source: 'uploaded', file_size: 100 } }))
  expect(screen.getByRole('button', { name: '导入全文 PDF' })).toBeInTheDocument()
  expect(detail.onUploaded).not.toHaveBeenCalled()
  expect(api.toastSuccess).not.toHaveBeenCalled()
  expect(detail.unsubscribe).toHaveBeenCalled()
})

it('removes native listeners and ignores an already queued callback after the detail closes', async () => {
  const detail = nativeDetail()
  await waitFor(() => expect(api.fulltextStatus).toHaveBeenCalledTimes(1))
  const queued = detail.listeners.at(-1)!
  detail.unmount()
  act(() => queued({ paper_id: paper.id }))
  expect(api.fulltextStatus).toHaveBeenCalledTimes(1)
  expect(detail.onUploaded).not.toHaveBeenCalled()
  expect(api.toastSuccess).not.toHaveBeenCalled()
  expect(detail.unsubscribe).toHaveBeenCalledTimes(1)
})

it('does not show an old manual PDF upload status on the next paper, but invalidates the old paper cache', async () => {
  api.fulltextStatus.mockResolvedValue({ data: { available: false, source: null, file_size: 0 } })
  let finish!: (value: any) => void
  api.uploadFulltext.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const detail = nativeDetail()
  const input = detail.container.querySelector<HTMLInputElement>('input[type="file"]')!
  fireEvent.change(input, { target: { files: [new File(['PDF'], 'old-paper.pdf', { type: 'application/pdf' })] } })
  expect(api.uploadFulltext).toHaveBeenCalledTimes(1)
  detail.rerender(<PaperDetail {...detail.props} paper={{ ...paper, id: 'second-paper', title: 'Second paper' }} />)
  await waitFor(() => expect(api.fulltextStatus).toHaveBeenCalledWith('second-paper'))
  await act(async () => finish({ data: { available: true, source: 'uploaded', file_size: 500 } }))
  expect(screen.getByRole('button', { name: '导入全文 PDF' })).toBeEnabled()
  expect(detail.onUploaded).toHaveBeenCalledTimes(1)
  expect(api.toastSuccess).not.toHaveBeenCalled()
})

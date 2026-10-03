import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AnalysisResult, PaperDetail as Paper } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import PaperDetail from '../PaperDetail/PaperDetail'

const api = vi.hoisted(() => ({ fulltextStatus: vi.fn() }))
vi.mock('@/api/client', () => ({ papersApi: api, knowledgeApi: {}, zoteroApi: {} }))

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
})
afterEach(() => {
  cleanup()
  useLocaleStore.getState().setLocale('zh')
  useLocaleStore.persist.clearStorage()
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

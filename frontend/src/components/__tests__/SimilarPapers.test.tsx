import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SimilarPapers from '../PaperDetail/SimilarPapers'
import { useLocaleStore } from '@/stores/localeStore'
import type { SimilarPapersResponse } from '@/api/types'

const api = vi.hoisted(() => ({ similar: vi.fn() }))
vi.mock('@/api/client', () => ({ recommendationsApi: api }))

function response(title = 'Traffic reconstruction with Qwen'): SimilarPapersResponse {
  return {
    seed_paper_id: 'seed', mode: 'topic', scope: 'live_sources', warnings: [],
    items: [{ paper: { id: 'related', title, authors: [], abstract: 'Background: missing data. Methods: imputation. Results: improvement.',
      year: 2025, venue: 'Transport Research', citation_count: 1, doi: '10.1234/test', url: null,
      pdf_url: null, source: 'openalex', is_open_access: false, relevance_score: null },
      reason: '共同研究交通数据缺失与修复', matched_terms: ['Qwen'], structure_labels: ['背景', '方法', '结果'] }],
    statistics: { candidate_count: 8, matched_count: 3, returned_count: 1, same_venue_count: 2,
      terms: [{ term: 'Qwen', count: 2 }], basis: 'retrieved_candidates', term_sample_count: 8, term_sample_scope: 'retrieved_candidates' },
    source_statuses: [{ source: 'openalex', success: true, paper_count: 8, elapsed_ms: 1250 }],
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  useLocaleStore.getState().setLocale('zh')
  api.similar.mockResolvedValue({ data: response() })
})
afterEach(() => { cleanup(); useLocaleStore.persist.clearStorage() })

it('shows real candidate metadata, an explicit sample denominator and the original abstract', async () => {
  const select = vi.fn()
  render(<SimilarPapers paperId="seed" onSelect={select} />)
  expect(await screen.findByText('Traffic reconstruction with Qwen')).toBeInTheDocument()
  expect(screen.getByText('Qwen · 2/8')).toBeInTheDocument()
  expect(screen.getByText(/非全文词频或全刊占比/)).toBeInTheDocument()
  expect(screen.getByText(/OpenAlex Works API/)).toBeInTheDocument()
  fireEvent.click(screen.getByText('展开原始摘要，对照阅读'))
  expect(screen.getByText(/Background: missing data/)).toBeInTheDocument()
  fireEvent.click(screen.getByText('在本页查看与分析'))
  expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: 'related' }))
  expect(screen.getByRole('link', { name: '查看原文' })).toHaveAttribute('href', 'https://doi.org/10.1234/test')
})

it('aborts the previous mode and ignores its late result', async () => {
  let finish!: (value: unknown) => void
  api.similar.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  render(<SimilarPapers paperId="seed" />)
  const signal = api.similar.mock.calls[0][2] as AbortSignal
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'structure' } })
  expect(signal.aborted).toBe(true)
  expect(await screen.findByText('Traffic reconstruction with Qwen')).toBeInTheDocument()
  expect(api.similar).toHaveBeenLastCalledWith('seed', 'structure', expect.any(AbortSignal))
  await act(async () => { finish({ data: response('Stale paper') }) })
  expect(screen.queryByText('Stale paper')).not.toBeInTheDocument()
  expect(screen.getByText(/不代表全文结构相同/)).toBeInTheDocument()
})

it('clears results on seed changes and aborts on unmount', async () => {
  const view = render(<SimilarPapers paperId="seed" />)
  await screen.findByText('Traffic reconstruction with Qwen')
  api.similar.mockImplementation(() => new Promise(() => {}))
  view.rerender(<SimilarPapers paperId="another" />)
  expect(screen.queryByText('Traffic reconstruction with Qwen')).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toBeInTheDocument()
  const signal = api.similar.mock.calls.at(-1)![2] as AbortSignal
  view.unmount()
  expect(signal.aborted).toBe(true)
})

it('allows a retry after a service-capacity failure', async () => {
  api.similar.mockRejectedValueOnce({ response: { data: { detail: '当前任务较多，请稍后重试' } } })
  render(<SimilarPapers paperId="seed" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('当前任务较多')
  fireEvent.click(screen.getByRole('button', { name: '重新检索推荐' }))
  expect(await screen.findByText('Traffic reconstruction with Qwen')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('labels empty and partial results honestly without unsafe source links', async () => {
  const empty = response()
  empty.items = []
  empty.statistics.matched_count = 0
  empty.warnings = ['Crossref 暂时不可用，本次结果不完整']
  api.similar.mockResolvedValueOnce({ data: empty })
  const view = render(<SimilarPapers paperId="seed" />)
  expect(await screen.findByText(/本次未找到满足条件/)).toBeInTheDocument()
  expect(screen.getByText(/Crossref 暂时不可用/)).toBeInTheDocument()
  const unsafe = response()
  unsafe.items[0].paper.url = 'javascript:alert(1)'
  api.similar.mockResolvedValueOnce({ data: unsafe })
  view.rerender(<SimilarPapers paperId="new-seed" />)
  await screen.findByText('Traffic reconstruction with Qwen')
  expect(screen.queryByRole('link', { name: '查看原文' })).not.toBeInTheDocument()
})

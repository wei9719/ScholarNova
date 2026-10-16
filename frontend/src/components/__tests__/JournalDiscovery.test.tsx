import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import JournalDiscovery from '../JournalDiscovery/JournalDiscovery'
import { useLocaleStore } from '@/stores/localeStore'
import type { JournalDiscoveryResponse } from '@/api/journalsClient'
import type { Paper } from '@/api/types'

const api = vi.hoisted(() => ({ discover: vi.fn(), papers: vi.fn() }))
vi.mock('@/api/journalsClient', () => ({ journalsApi: api }))

const paper: Paper = { id: 'p1', title: 'Traffic speed imputation', authors: [], abstract: null, year: 2025,
  venue: 'Transport Research', citation_count: 1, doi: null, url: null, pdf_url: null, source: 'openalex', is_open_access: false, relevance_score: null }
function response(): JournalDiscoveryResponse {
  return { query: 'traffic', candidate_count: 1, discovery_candidate_count: 40, scope: '仅本次样本，非全刊发文占比', cached: false,
    checked_at: '2026-10-16T00:00:00Z', warnings: ['未读取官方作者指南'], journals: [{ name: 'Transport Research', source_id: 'S123',
      issns: ['1234-567X'], verification: 'verified_journal', homepage_url: 'https://publisher.example/journal', author_guidelines_url: null,
      reason: '本次关键词补充查询命中。', candidate_match_count: 1, candidate_paper_ids: ['p1'], discovery_match_count: 2,
      metrics: [{ name: 'OpenAlex 近两年篇均被引', value: 4.2, year: null, source: 'OpenAlex Sources API', source_url: 'https://openalex.org/S123', basis: '当前快照，不是 JIF' }] }] }
}
beforeEach(() => {
  vi.resetAllMocks(); useLocaleStore.getState().setLocale('zh')
  api.discover.mockResolvedValue({ data: response() })
  api.papers.mockResolvedValue({ data: { papers: [paper], warnings: [], scope: '已核验期刊样本' } })
})
afterEach(() => { cleanup(); useLocaleStore.persist.clearStorage() })

it('requires user action and shows provenance without presenting open metrics as JIF', async () => {
  render(<JournalDiscovery query="traffic" papers={[paper]} />)
  expect(api.discover).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('查询相关期刊'))
  expect(await screen.findByText('Transport Research')).toBeInTheDocument()
  expect(screen.getByText(/OpenAlex 近两年篇均被引/)).toBeInTheDocument()
  expect(screen.getByText(/指标年度未提供/)).toBeInTheDocument()
  expect(screen.getByText(/正式 JIF 尚未提供/)).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '官方作者指南' })).not.toBeInTheDocument()
})

it('selects persisted same-journal papers and creates an unsaved existing skill draft', async () => {
  const select = vi.fn(), draft = vi.fn()
  render(<JournalDiscovery query="traffic" papers={[paper]} category="交通" dateFrom="2024-01-01" onSelectPaper={select} onCreateWritingDraft={draft} />)
  fireEvent.click(screen.getByText('查询相关期刊'))
  fireEvent.click(await screen.findByText('查看该刊相关论文'))
  fireEvent.click(await screen.findByText('Traffic speed imputation'))
  expect(select).toHaveBeenCalledWith(paper)
  expect(api.papers).toHaveBeenCalledWith('S123', 'traffic', { date_from: '2024-01-01', date_to: undefined }, expect.any(AbortSignal))
  fireEvent.click(screen.getByText('建立写作参考草稿'))
  expect(draft).toHaveBeenCalledWith(expect.objectContaining({ category: '交通', card_type: 'skill', auto_polish: false }))
  expect(draft.mock.calls[0][0].content).toContain('尚未读取作者指南')
})

it('keeps pending sources distinct and does not request journal papers for them', async () => {
  const data = response()
  data.journals[0] = { ...data.journals[0], verification: 'pending', source_id: null, issns: [], metrics: [], homepage_url: 'javascript:alert(1)' }
  api.discover.mockResolvedValue({ data })
  render(<JournalDiscovery query="traffic" papers={[paper]} onCreateWritingDraft={vi.fn()} />)
  fireEvent.click(screen.getByText('查询相关期刊'))
  fireEvent.click(await screen.findByText('查看当前命中文献'))
  expect(api.papers).not.toHaveBeenCalled()
  expect(screen.getByText('Traffic speed imputation')).toBeInTheDocument()
  expect(screen.queryByText('建立写作参考草稿')).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /期刊主页/ })).not.toBeInTheDocument()
})

it('aborts and ignores late results when the query changes', async () => {
  let finish!: (value: unknown) => void
  api.discover.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const view = render(<JournalDiscovery query="old" papers={[paper]} />)
  fireEvent.click(screen.getByText('查询相关期刊'))
  const signal = api.discover.mock.calls[0][2] as AbortSignal
  view.rerender(<JournalDiscovery query="new" papers={[paper]} />)
  expect(signal.aborted).toBe(true)
  await screen.findByText('Transport Research')
  const old = response(); old.journals[0].name = 'Stale journal'
  await act(async () => finish({ data: old }))
  expect(screen.queryByText('Stale journal')).not.toBeInTheDocument()
})

it('retries a failed query without affecting the surrounding search', async () => {
  api.discover.mockRejectedValueOnce({ response: { data: { detail: '当前任务较多' } } })
  render(<JournalDiscovery query="traffic" papers={[paper]} />)
  fireEvent.click(screen.getByText('查询相关期刊'))
  expect(await screen.findByRole('alert')).toHaveTextContent('当前任务较多')
  fireEvent.click(screen.getByText('重新查询'))
  expect(await screen.findByText('Transport Research')).toBeInTheDocument()
})

it('formats numeric metric values with at most two decimals without changing source data or quartiles', async () => {
  const data = response()
  data.journals[0].metrics[0].value = 4.239999999999
  data.journals[0].metrics.push({ ...data.journals[0].metrics[0], name: 'H-index', value: 14523.123456 }, { ...data.journals[0].metrics[0], name: 'JCR 分区', value: 'Q1' })
  api.discover.mockResolvedValue({ data })
  render(<JournalDiscovery query="traffic" papers={[paper]} />)
  fireEvent.click(screen.getByText('查询相关期刊'))
  expect(await screen.findByText('OpenAlex 近两年篇均被引：4.24')).toBeInTheDocument()
  expect(screen.getByText('H-index：14,523.12')).toBeInTheDocument()
  expect(screen.getByText('JCR 分区：Q1')).toBeInTheDocument()
  expect(data.journals[0].metrics[0].value).toBe(4.239999999999)
})

it('collapses and restores journal cards without refetching or hiding selected journal papers', async () => {
  render(<JournalDiscovery query="traffic" papers={[paper]} />)
  fireEvent.click(screen.getByText('查询相关期刊'))
  const journal = await screen.findByText('Transport Research')
  fireEvent.click(screen.getByText('查看该刊相关论文'))
  await screen.findByText('Traffic speed imputation')
  const collapse = screen.getByRole('button', { name: '收起期刊列表' })
  expect(collapse).toHaveAttribute('aria-expanded', 'true')
  fireEvent.click(collapse)
  expect(journal).not.toBeVisible()
  expect(screen.getByText('Traffic speed imputation')).toBeVisible()
  const expand = screen.getByRole('button', { name: '展开期刊列表（1）' })
  expect(expand).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(expand)
  expect(journal).toBeVisible()
  expect(api.discover).toHaveBeenCalledTimes(1)
  expect(api.papers).toHaveBeenCalledTimes(1)
})

it('expands a newly completed query even if the preceding list was collapsed', async () => {
  render(<JournalDiscovery query="traffic" papers={[paper]} />)
  fireEvent.click(screen.getByText('查询相关期刊'))
  await screen.findByText('Transport Research')
  fireEvent.click(screen.getByRole('button', { name: '收起期刊列表' }))
  fireEvent.click(screen.getByText('重新查询'))
  expect(await screen.findByRole('button', { name: '收起期刊列表' })).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByText('Transport Research')).toBeVisible()
  expect(api.discover).toHaveBeenCalledTimes(2)
})

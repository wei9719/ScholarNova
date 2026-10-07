import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useSearchStore } from '@/stores/searchStore'
import { useLocaleStore } from '@/stores/localeStore'
import Search from '../Search'

const api = vi.hoisted(() => ({ create: vi.fn(), getRun: vi.fn() }))
vi.mock('@/api/client', () => ({ searchApi: api, papersApi: {}, networkApi: {} }))
vi.mock('@/components/QueryPlan/QueryPlan', () => ({ default: () => null }))
vi.mock('@/components/SearchInsights/SearchInsights', () => ({ default: () => null }))
vi.mock('@/components/PaperDetail/PaperDetail', () => ({ default: () => null }))

beforeEach(() => {
  vi.resetAllMocks()
  useSearchStore.getState().clearSearch()
  useLocaleStore.getState().setLocale('zh')
  api.create.mockResolvedValue({ data: { run_id: 'run' } })
  api.getRun.mockResolvedValue({ data: {
    run_id: 'run', status: 'completed', query: 'traffic', results: [
      { id: 'a', title: 'Paper A', authors: [], abstract: 'Useful abstract', citation_count: 0, source: 'crossref', relevance_score: 0.8 },
      { id: 'b', title: 'Paper B', authors: [], abstract: null, citation_count: 0, source: 'crossref', relevance_score: 0.5 },
    ],
  } })
})
afterEach(() => {
  cleanup()
  sessionStorage.clear()
})

it('does not search when filtering and resets the filter even when repeating the same query', async () => {
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  await screen.findByRole('heading', { name: 'Paper A' })
  fireEvent.click(screen.getByRole('button', { name: '有摘要 (1)' }))
  expect(screen.queryByRole('heading', { name: 'Paper B' })).not.toBeInTheDocument()
  expect(api.create).toHaveBeenCalledOnce()
  expect(api.getRun).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: '搜索' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
  await screen.findByRole('heading', { name: 'Paper B' })
  expect(screen.getByRole('button', { name: '全部 (2)' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: '相关度' })).toHaveAttribute('aria-pressed', 'true')
})

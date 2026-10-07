import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AnalysisResult, PaperDetail, SimilarPapersResponse } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import { useSearchStore } from '@/stores/searchStore'
import Search from '../Search'

const api = vi.hoisted(() => ({
  create: vi.fn(), getRun: vi.fn(), get: vi.fn(), analyze: vi.fn(),
  fulltextStatus: vi.fn(), similar: vi.fn(),
}))
vi.mock('@/api/client', () => ({
  searchApi: { create: api.create, getRun: api.getRun },
  papersApi: { get: api.get, analyze: api.analyze, fulltextStatus: api.fulltextStatus },
  recommendationsApi: { similar: api.similar },
  knowledgeApi: {}, zoteroApi: {}, networkApi: {},
}))
vi.mock('@/components/QueryPlan/QueryPlan', () => ({ default: () => null }))
vi.mock('@/components/SearchInsights/SearchInsights', () => ({ default: () => null }))

function paper(id: string): PaperDetail {
  return {
    id, title: `Paper ${id}`, authors: ['Researcher'], abstract: `Original abstract for ${id}.`,
    year: 2025, venue: null, citation_count: 1, doi: null, url: null, pdf_url: null,
    source: 'openalex', relevance_score: 0.8, is_open_access: false,
    references: [], citations: [], fields_of_study: [], keywords: [],
    publication_date: null, volume: null, issue: null, pages: null,
  }
}

function analysis(id: string): AnalysisResult {
  return {
    paper_id: id, analysis_type: 'full', summary: `Analysis for ${id} completed.`,
    methodology: null, key_findings: [], strengths: [], weaknesses: [], relevance_to_query: null,
    document_coverage: 'abstract', model_completed: true, prompt_tokens: 3, completion_tokens: 2,
    total_tokens: 5, created_at: '2026-10-07T00:00:00Z',
  }
}

function recommendations(): SimilarPapersResponse {
  return {
    seed_paper_id: 'seed', mode: 'topic', scope: 'retrieved metadata only', warnings: [],
    items: ['related', 'latest'].map(id => ({
      paper: paper(id), reason: 'Shared research problem', matched_terms: ['traffic'], structure_labels: [],
    })),
    statistics: {
      candidate_count: 2, matched_count: 2, returned_count: 2, same_venue_count: 0,
      terms: [{ term: 'traffic', count: 2 }], basis: 'retrieved_candidates',
      term_sample_count: 2, term_sample_scope: 'retrieved_candidates',
    },
    source_statuses: [{ source: 'openalex', success: true, paper_count: 2, elapsed_ms: 100 }],
  }
}

async function openRecommendations() {
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('heading', { name: 'Paper seed', level: 3 }))
  await screen.findByRole('heading', { name: 'Paper seed', level: 2 })
  fireEvent.click(screen.getByRole('button', { name: '相似推荐' }))
  await screen.findByRole('heading', { name: 'Paper related', level: 4 })
}

function selectRecommendation(id: string) {
  const article = screen.getByRole('heading', { name: `Paper ${id}`, level: 4 }).closest('article')!
  fireEvent.click(within(article).getByRole('button', { name: '在本页查看与分析' }))
}

beforeEach(() => {
  vi.resetAllMocks()
  useSearchStore.getState().clearSearch()
  useLocaleStore.getState().setLocale('zh')
  api.create.mockResolvedValue({ data: { run_id: 'run' } })
  api.getRun.mockResolvedValue({ data: { run_id: 'run', status: 'completed', query: 'traffic', results: [paper('seed')] } })
  api.get.mockImplementation((id: string) => Promise.resolve({ data: paper(id) }))
  api.fulltextStatus.mockResolvedValue({ data: { available: false, source: null, file_size: 0 } })
  api.similar.mockResolvedValue({ data: recommendations() })
  api.analyze.mockImplementation((id: string) => Promise.resolve({ data: analysis(id) }))
})

afterEach(() => {
  cleanup()
  sessionStorage.clear()
  useLocaleStore.persist.clearStorage()
})

it('opens an actual recommended paper through Search and analyzes that paper rather than the seed', async () => {
  await openRecommendations()
  expect(api.similar).toHaveBeenCalledWith('seed', 'topic', expect.any(AbortSignal))
  selectRecommendation('related')
  await screen.findByRole('heading', { name: 'Paper related', level: 2 })
  expect(api.get).toHaveBeenLastCalledWith('related')
  expect(screen.getByText('Original abstract for related.')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Paper seed', level: 2 })).not.toBeInTheDocument()
  expect(api.analyze).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: '全面分析' }))
  expect(api.analyze).toHaveBeenCalledWith('related', expect.objectContaining({ analysis_type: 'full' }), expect.any(AbortSignal))
  expect(await screen.findByText('Analysis for related completed.')).toBeInTheDocument()
  expect(useSearchStore.getState().analysis?.paper_id).toBe('related')
  expect(api.create).toHaveBeenCalledOnce()
}, 15000)

it('keeps the latest recommendation selected when an earlier recommended detail arrives late', async () => {
  let finishEarlier!: (value: { data: PaperDetail }) => void
  api.get.mockImplementation((id: string) => id === 'related'
    ? new Promise(resolve => { finishEarlier = resolve })
    : Promise.resolve({ data: paper(id) }))
  await openRecommendations()
  selectRecommendation('related')
  expect(api.get).toHaveBeenLastCalledWith('related')
  selectRecommendation('latest')
  await screen.findByRole('heading', { name: 'Paper latest', level: 2 })
  await act(async () => { finishEarlier({ data: paper('related') }) })
  expect(screen.getByRole('heading', { name: 'Paper latest', level: 2 })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Paper related', level: 2 })).not.toBeInTheDocument()
  expect(useSearchStore.getState().selectedPaper?.id).toBe('latest')

  fireEvent.click(screen.getByRole('button', { name: '全面分析' }))
  await waitFor(() => expect(api.analyze).toHaveBeenCalledOnce())
  expect(api.analyze).toHaveBeenCalledWith('latest', expect.objectContaining({ analysis_type: 'full' }), expect.any(AbortSignal))
  expect(await screen.findByText('Analysis for latest completed.')).toBeInTheDocument()
}, 15000)

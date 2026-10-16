import api from './client'
import type { Paper } from './types'

export interface JournalMetric {
  name: string
  value: number | string
  year: number | null
  source: string
  source_url: string | null
  basis: string
}

export interface JournalRecommendation {
  name: string
  source_id: string | null
  issns: string[]
  verification: 'verified_journal' | 'pending'
  homepage_url: string | null
  author_guidelines_url: string | null
  reason: string
  candidate_match_count: number
  candidate_paper_ids: string[]
  discovery_match_count: number
  metrics: JournalMetric[]
}

export interface JournalDiscoveryResponse {
  query: string
  journals: JournalRecommendation[]
  candidate_count: number
  discovery_candidate_count: number
  scope: string
  warnings: string[]
  checked_at: string
  cached: boolean
}

export interface JournalPapersResponse {
  source_id: string
  query: string
  papers: Paper[]
  scope: string
  warnings: string[]
}

export const journalsApi = {
  discover: (query: string, papers: Paper[], signal?: AbortSignal) => api.post<JournalDiscoveryResponse>(
    '/journals/discover', { query, candidates: papers.slice(0, 100).map(paper => ({
      id: paper.id, title: paper.title.slice(0, 2000), venue: paper.venue?.slice(0, 500) || null,
    })) }, { signal, timeout: 22000 },
  ),
  papers: (sourceId: string, query: string, dates: { date_from?: string; date_to?: string } = {}, signal?: AbortSignal) =>
    api.post<JournalPapersResponse>(`/journals/${encodeURIComponent(sourceId)}/papers`, { query, limit: 15, ...dates }, { signal, timeout: 22000 }),
}

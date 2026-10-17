import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { SearchRunDetail } from '@/api/types'
import SearchInsights from '../SearchInsights/SearchInsights'

vi.mock('@/stores/localeStore', () => ({ useLocaleStore: () => ({ locale: 'zh' }) }))
afterEach(cleanup)

it('shows zero-match diagnostics and the match boundary instead of hiding source failures', () => {
  const run = {
    status: 'completed', results: [],
    result_summary: { total: 0, highly_relevant: 0, partially_relevant: 0, relevance_note: '仅显示达到匹配下限的候选，不凑满结果。' },
    runtime_metrics: { api_calls: 2, search_rounds: 1, latency_ms: 3000 },
    source_status: [{ source: 'semantic_scholar', label: 'Semantic Scholar', success: false, paper_count: 0, elapsed_ms: 3000 }],
  } as SearchRunDetail
  render(<SearchInsights run={run} />)
  expect(screen.getByText('检索质量快照')).toBeInTheDocument()
  expect(screen.getByText('仅显示达到匹配下限的候选，不凑满结果。')).toBeInTheDocument()
  expect(screen.getByText('Semantic Scholar')).toBeInTheDocument()
  expect(screen.getByText('调用失败')).toBeInTheDocument()
  expect(screen.getByText('2 / 1轮')).toBeInTheDocument()
})

it('does not invent a quality snapshot for a legacy response without metrics', () => {
  render(<SearchInsights run={{ status: 'completed', results: [] } as unknown as SearchRunDetail} />)
  expect(screen.queryByText('检索质量快照')).not.toBeInTheDocument()
})

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Paper } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import ResultsList from '../ResultsList/ResultsList'

const api = vi.hoisted(() => ({ journalQuality: vi.fn(), translate: vi.fn(), create: vi.fn() }))
vi.mock('@/api/client', () => ({ papersApi: api, searchApi: { create: api.create } }))

const paper = (id: string, overrides: Partial<Paper> = {}): Paper => ({
  id, title: `Paper ${id}`, authors: [], abstract: null, year: null, venue: null,
  citation_count: 0, doi: null, url: null, pdf_url: null, source: 'crossref',
  relevance_score: null, is_open_access: false, ...overrides,
})
const titles = () => screen.queryAllByRole('heading', { level: 3 }).map(node => node.textContent)

beforeEach(() => {
  vi.clearAllMocks()
  useLocaleStore.getState().setLocale('zh')
})
afterEach(() => {
  cleanup()
  useLocaleStore.getState().setLocale('zh')
  useLocaleStore.persist.clearStorage()
})

describe('result sorting and abstract filters', () => {
  it('defaults to relevance and keeps composite, year and citation sorting independent', () => {
    render(<ResultsList papers={[
      paper('A', { relevance_score: 0.9, ranking_score: 0.1, year: 2020, citation_count: 5 }),
      paper('B', { relevance_score: 0.4, ranking_score: 0.9, year: 2024, citation_count: 2 }),
      paper('C', { relevance_score: 0.6, ranking_score: 0.6, year: 2018, citation_count: 9 }),
    ]} />)
    expect(titles()).toEqual(['Paper A', 'Paper C', 'Paper B'])
    expect(screen.getByRole('button', { name: '相关度' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '综合推荐' }))
    expect(titles()).toEqual(['Paper B', 'Paper C', 'Paper A'])
    fireEvent.click(screen.getByRole('button', { name: '年份' }))
    expect(titles()).toEqual(['Paper B', 'Paper A', 'Paper C'])
    fireEvent.click(screen.getByRole('button', { name: '引用' }))
    expect(titles()).toEqual(['Paper C', 'Paper A', 'Paper B'])
    fireEvent.click(screen.getByRole('button', { name: '相关度' }))
    fireEvent.click(screen.getByRole('button', { name: '相关度' }))
    expect(titles()).toEqual(['Paper B', 'Paper C', 'Paper A'])
    expect(screen.getByText(/分数为启发式参考，不是概率/)).toBeInTheDocument()
    expect(screen.getByText('90/100')).toBeInTheDocument()
    expect(screen.queryByText('90%')).not.toBeInTheDocument()
  })

  it('filters the returned papers locally, with truthful counts and no new search or model calls', () => {
    const onPaperClick = vi.fn()
    const papers = [
      paper('A', { abstract: '<p>Useful abstract.</p>' }),
      paper('B', { abstract: ' \n\t ' }),
      paper('C', { abstract: '<p>&nbsp;<br></p>' }),
      paper('D', { abstract: 'No abstract available.' }),
      paper('E', { abstract: null }),
    ]
    const view = render(<ResultsList papers={papers} onPaperClick={onPaperClick} selectedPaperId="A" />)
    expect(screen.getByRole('button', { name: '全部 (5)' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '有摘要 (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '暂无摘要 (4)' })).toBeInTheDocument()
    expect(screen.getByText('Useful abstract.')).toBeInTheDocument()
    expect(screen.getAllByText('暂无摘要（数据源未提供）')).toHaveLength(4)
    fireEvent.click(screen.getByRole('button', { name: '有摘要 (1)' }))
    expect(titles()).toEqual(['Paper A'])
    expect(screen.getByText('筛选后 1 / 5 篇论文')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('heading', { name: 'Paper A' }))
    expect(onPaperClick).toHaveBeenCalledWith(papers[0])
    expect(screen.getByRole('heading', { name: 'Paper A' }).closest('.paper-card')).toHaveClass('paper-card-selected')
    fireEvent.click(screen.getByRole('button', { name: '暂无摘要 (4)' }))
    expect(titles()).toEqual(['Paper B', 'Paper C', 'Paper D', 'Paper E'])
    // More results in this same run update the counts without clearing the filter.
    view.rerender(<ResultsList papers={[...papers, paper('F', { abstract: 'Another abstract' })]} />)
    expect(screen.getByText('筛选后 4 / 6 篇论文')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全部 (6)' }))
    expect(titles()).toHaveLength(6)
    expect(api.create).not.toHaveBeenCalled()
    expect(api.translate).not.toHaveBeenCalled()
  })

  it.each(['available', 'missing'])('explains an empty %s filter and lets users reset it', (filter) => {
    render(<ResultsList papers={[paper('A', { abstract: filter === 'missing' ? 'Present' : null })]} />)
    fireEvent.click(screen.getByRole('button', { name: filter === 'missing' ? '暂无摘要 (0)' : '有摘要 (0)' }))
    expect(titles()).toEqual([])
    expect(screen.getByRole('status')).toHaveTextContent('本次结果中没有符合摘要筛选的论文')
    expect(screen.getByRole('status')).toHaveTextContent('合法获取 PDF 后导入全文分析')
    expect(screen.queryByText('未找到论文')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重置筛选，查看全部' }))
    expect(titles()).toEqual(['Paper A'])
  })

  it('starts a new result session with all papers and the default relevance order', () => {
    const papers = [paper('A'), paper('B', { abstract: 'Present' })]
    const view = render(<ResultsList key="run-1" papers={papers} />)
    fireEvent.click(screen.getByRole('button', { name: '有摘要 (1)' }))
    fireEvent.click(screen.getByRole('button', { name: '年份' }))
    view.rerender(<ResultsList key="run-2" papers={papers} />)
    expect(screen.getByRole('button', { name: '全部 (2)' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '相关度' })).toHaveAttribute('aria-pressed', 'true')
    expect(titles()).toHaveLength(2)
  })

  it('provides equivalent English filtering and recovery text', () => {
    act(() => useLocaleStore.getState().setLocale('en'))
    render(<ResultsList papers={[paper('A')]} />)
    fireEvent.click(screen.getByRole('button', { name: 'With abstract (0)' }))
    expect(screen.getByText('Showing 0 / 1 results')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('legally obtained PDF')
    fireEvent.click(screen.getByRole('button', { name: 'Reset filter and show all' }))
    expect(screen.getByRole('button', { name: 'All (1)' })).toHaveAttribute('aria-pressed', 'true')
  })
})

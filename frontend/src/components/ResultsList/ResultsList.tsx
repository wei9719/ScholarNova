import { useState, useMemo } from 'react'
import { FileSearch, ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react'
import clsx from 'clsx'
import type { Paper } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import { getAbstractText } from '@/utils/abstract'
import PaperCard from './PaperCard'
import './ResultsList.css'

type SortKey = 'relevance' | 'recommended' | 'year' | 'citations'
type AbstractFilter = 'all' | 'available' | 'missing'

interface ResultsListProps {
  papers: Paper[]
  selectedPaperId?: string | null
  onPaperClick?: (paper: Paper) => void
}

export default function ResultsList({ papers, selectedPaperId, onPaperClick }: ResultsListProps) {
  const { locale } = useLocaleStore()
  const isZh = locale === 'zh'
  const [sortKey, setSortKey] = useState<SortKey>('relevance')
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc')
  const [abstractFilter, setAbstractFilter] = useState<AbstractFilter>('all')
  const papersWithAbstract = useMemo(() => new Set(
    papers.filter(paper => getAbstractText(paper.abstract)).map(paper => paper.id)
  ), [papers])

  const sortedPapers = useMemo(() => {
    const arr = papers.filter(paper => abstractFilter === 'all'
      || papersWithAbstract.has(paper.id) === (abstractFilter === 'available'))
    arr.sort((a, b) => {
      let cmp = 0
      if (sortKey === 'year') cmp = (a.year || 0) - (b.year || 0)
      else if (sortKey === 'citations') cmp = a.citation_count - b.citation_count
      else if (sortKey === 'recommended') cmp = (a.ranking_score ?? 0) - (b.ranking_score ?? 0)
      else cmp = (a.relevance_score ?? 0) - (b.relevance_score ?? 0)
      return sortDir === 'desc' ? -cmp : cmp
    })
    return arr
  }, [papers, papersWithAbstract, abstractFilter, sortKey, sortDir])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const SortBtn = ({ k, label }: { k: SortKey; label: string }) => (
    <button type="button" onClick={() => toggleSort(k)} aria-pressed={sortKey === k}
      className={clsx('inline-flex items-center gap-0.5 px-2 py-1 rounded text-xs font-medium transition-colors',
        sortKey === k ? 'bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-300'
          : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800')}>
      {sortKey === k ? (sortDir === 'desc' ? <ArrowDown className="w-3 h-3" /> : <ArrowUp className="w-3 h-3" />) : <ArrowUpDown className="w-3 h-3" />}
      {label}
    </button>
  )

  if (papers.length === 0) {
    return (
      <div className="results-empty">
        <FileSearch className="w-12 h-12 text-gray-300 dark:text-gray-600 mb-3" />
        <p className="text-gray-500 dark:text-gray-400 font-medium">
          {isZh ? '未找到论文' : 'No papers found'}
        </p>
        <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">
          {isZh ? '尝试换一个搜索关键词' : 'Try a different search query'}
        </p>
      </div>
    )
  }

  return (
    <div className="results-list">
      <div className="results-list-header">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
          {abstractFilter === 'all'
            ? `${papers.length} ${isZh ? '篇论文' : 'results'}`
            : isZh ? `筛选后 ${sortedPapers.length} / ${papers.length} 篇论文` : `Showing ${sortedPapers.length} / ${papers.length} results`}
        </span>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label={isZh ? '结果排序' : 'Sort results'}>
          <SortBtn k="relevance" label={isZh ? '相关度' : 'Relevance'} />
          <SortBtn k="recommended" label={isZh ? '综合推荐' : 'Recommended'} />
          <SortBtn k="year" label={isZh ? '年份' : 'Year'} />
          <SortBtn k="citations" label={isZh ? '引用' : 'Citations'} />
        </div>
      </div>
      <div className="results-filters">
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label={isZh ? '摘要筛选' : 'Filter by abstract'}>
          <span className="text-xs text-gray-500 dark:text-gray-400 mr-1">{isZh ? '摘要' : 'Abstract'}</span>
          {([
            ['all', isZh ? '全部' : 'All', papers.length],
            ['available', isZh ? '有摘要' : 'With abstract', papersWithAbstract.size],
            ['missing', isZh ? '暂无摘要' : 'No abstract', papers.length - papersWithAbstract.size],
          ] as const).map(([filter, label, count]) => (
            <button key={filter} type="button" aria-pressed={abstractFilter === filter}
              onClick={() => setAbstractFilter(filter)}
              className={clsx('px-2 py-1 rounded text-xs font-medium transition-colors',
                abstractFilter === filter ? 'bg-primary-100 dark:bg-primary-900/40 text-primary-700 dark:text-primary-300'
                  : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800')}>
              {label} ({count})
            </button>
          ))}
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500">
          {isZh
            ? '仅筛选本次已返回结果，不会重新检索。相关度按匹配分排序，综合推荐按综合分排序；分数为启发式参考，不是概率。'
            : 'Filters only the results returned in this search, without a new search. Relevance uses the match score; Recommended uses the composite score. Scores are heuristic, not probabilities.'}
        </p>
      </div>
      <div className="results-list-body custom-scrollbar">
        {sortedPapers.length === 0 && (
          <div className="results-empty" role="status">
            <FileSearch className="w-10 h-10 text-gray-300 dark:text-gray-600 mb-3" />
            <p className="text-gray-500 dark:text-gray-400 font-medium">
              {isZh ? '本次结果中没有符合摘要筛选的论文' : 'No results match this abstract filter'}
            </p>
            <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">
              {isZh
                ? '这不代表没有相关论文；数据源可能未提供摘要。可重置筛选查看论文详情，合法获取 PDF 后导入全文分析。'
                : 'This does not mean no relevant papers exist; a source may not provide an abstract. Reset the filter to open paper details, then import a legally obtained PDF for full-text analysis.'}
            </p>
            <button type="button" className="mt-3 text-sm text-primary-600 dark:text-primary-400 hover:underline"
              onClick={() => setAbstractFilter('all')}>
              {isZh ? '重置筛选，查看全部' : 'Reset filter and show all'}
            </button>
          </div>
        )}
        {sortedPapers.map((paper, index) => (
          <PaperCard
            key={paper.id}
            paper={paper}
            isSelected={paper.id === selectedPaperId}
            onClick={onPaperClick}
            autoEnrich={index < 8}
          />
        ))}
      </div>
    </div>
  )
}

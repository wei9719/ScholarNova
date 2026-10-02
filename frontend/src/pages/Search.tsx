import { useEffect, useCallback, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Loader2, Search as SearchIcon, AlertCircle, BookOpen, Clock3, Database, ExternalLink } from 'lucide-react'
import { searchApi, papersApi, networkApi } from '@/api/client'
import type { AnalysisResult } from '@/api/types'
import { useSearchStore } from '@/stores/searchStore'
import { useLocaleStore } from '@/stores/localeStore'
import { addSearchHistory, clearSearchHistory, getSearchHistory } from '@/utils/searchHistory'
import SearchBar from '@/components/SearchBar/SearchBar'
import QueryPlan from '@/components/QueryPlan/QueryPlan'
import SearchInsights from '@/components/SearchInsights/SearchInsights'
import ResultsList from '@/components/ResultsList/ResultsList'
import PaperDetailPanel from '@/components/PaperDetail/PaperDetail'
import { PaperCardSkeleton } from '@/components/Skeleton'
import toast from 'react-hot-toast'
import './Search.css'

export default function Search() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryParam = searchParams.get('q') || ''
  const { t, locale } = useLocaleStore()

  const {
    searchRun, isLoading, error, query,
    selectedPaper, analysis, analysisLoading, evidenceSpans, evidenceLoading,
    setSearchRun, setIsLoading, setError, setQuery,
    setSelectedPaper, setAnalysis, setAnalysisLoading,
    setEvidenceSpans, clearSearch,
  } = useSearchStore()

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastStartedQueryRef = useRef<string | null>(null)
  const searchStartedAtRef = useRef<number | null>(null)
  const analysisCacheRef = useRef<Map<string, AnalysisResult>>(new Map())
  const analysisRequestsRef = useRef<Map<string, AbortController>>(new Map())
  const selectedPaperIdRef = useRef<string | null>(selectedPaper?.id || null)
  const resizeStartRef = useRef<{ x: number; width: number } | null>(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [panelWidth, setPanelWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem('scholarnova-detail-width'))
    return Number.isFinite(saved) && saved >= 360 ? saved : 440
  })
  const [isResizing, setIsResizing] = useState(false)
  const generationRef = useRef(0)
  const selectionRef = useRef(0)
  const [searchHistory, setSearchHistory] = useState<ReturnType<typeof getSearchHistory>>(() => getSearchHistory())

  // A late request must never repopulate a closed or replaced search session.
  useEffect(() => {
    const cache = analysisCacheRef.current
    const requests = analysisRequestsRef.current
    return () => {
      generationRef.current++
      selectionRef.current++
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
      cache.clear()
      requests.forEach((request) => request.abort())
      requests.clear()
      clearSearch()
      lastStartedQueryRef.current = null
    }
  }, [clearSearch])

  useEffect(() => {
    if (!isLoading || searchStartedAtRef.current == null) return
    const timer = window.setInterval(() => {
      setElapsedMs(Date.now() - (searchStartedAtRef.current || Date.now()))
    }, 250)
    return () => window.clearInterval(timer)
  }, [isLoading])

  useEffect(() => {
    selectedPaperIdRef.current = selectedPaper?.id || null
  }, [selectedPaper?.id])

  useEffect(() => {
    if (!isResizing) return
    const previousCursor = document.body.style.cursor
    const previousSelection = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handlePointerMove = (event: PointerEvent) => {
      const start = resizeStartRef.current
      if (!start) return
      const maximum = Math.max(360, Math.min(900, window.innerWidth * 0.72))
      const nextWidth = Math.min(maximum, Math.max(360, start.width + start.x - event.clientX))
      setPanelWidth(Math.round(nextWidth))
    }
    const stopResize = () => {
      setIsResizing(false)
      resizeStartRef.current = null
    }
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', stopResize, { once: true })
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', stopResize)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelection
    }
  }, [isResizing])

  useEffect(() => {
    window.localStorage.setItem('scholarnova-detail-width', String(panelWidth))
  }, [panelWidth])

  // 搜索 query 变化时执行搜索
  useEffect(() => {
    if (queryParam) {
      // 如果 URL 有查询且 store 里没有对应结果，执行搜索
      if ((queryParam !== query || !searchRun) && lastStartedQueryRef.current !== queryParam) {
        lastStartedQueryRef.current = queryParam
        setQuery(queryParam)
        performSearch(queryParam)
      }
    }
  }, [queryParam])

  const performSearch = async (searchQuery: string) => {
    const generation = ++generationRef.current
    selectionRef.current++
    setQuery(searchQuery)
    setAnalysisLoading(false)
    lastStartedQueryRef.current = searchQuery
    setSearchHistory(addSearchHistory(searchQuery))
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    setIsLoading(true)
    setError(null)
    setSearchRun(null)
    setSelectedPaper(null)
    setAnalysis(null)
    setEvidenceSpans([])
    analysisCacheRef.current.clear()
    analysisRequestsRef.current.forEach((request) => request.abort())
    analysisRequestsRef.current.clear()
    searchStartedAtRef.current = Date.now()
    setElapsedMs(0)

    try {
      const response = await searchApi.create({ query: searchQuery })
      if (generation !== generationRef.current) return
      pollSearchStatus(response.data.run_id, generation)
    } catch (err: any) {
      if (generation !== generationRef.current) return
      setError(err.response?.data?.detail || (t('common.error') + '. ' + t('common.retry')))
      setIsLoading(false)
    }
  }

  const pollSearchStatus = (runId: string, generation: number) => {
    let attempts = 0
    const poll = async () => {
      try {
        const response = await searchApi.getRun(runId)
        if (generation !== generationRef.current) return
        setSearchRun(response.data)
        if (response.data.status === 'completed' || response.data.status === 'failed') {
          setIsLoading(false)
          if (response.data.status !== 'completed') {
            setError(response.data.progress?.message || '搜索失败，请稍后重试或调整数据源')
          }
          return
        }
        attempts++
        if (attempts < 350) pollTimerRef.current = setTimeout(poll, 850)
        else { setError('搜索超时'); setIsLoading(false) }
      } catch (err: any) {
        if (generation !== generationRef.current) return
        setError(err.response?.data?.detail || '获取搜索状态失败')
        setIsLoading(false)
      }
    }
    poll()
  }

  const handleSearch = (newQuery: string) => {
    if (newQuery.trim() === queryParam.trim()) {
      performSearch(newQuery.trim())
      return
    }
    navigate(`/search?q=${encodeURIComponent(newQuery)}`, { replace: true })
  }

  const handlePaperClick = useCallback(async (paper: any) => {
    const selection = ++selectionRef.current
    const generation = generationRef.current
    selectedPaperIdRef.current = paper.id
    // Keep the old paper and its analysis together until the new details arrive.
    setEvidenceSpans([])
    try {
      const response = await papersApi.get(paper.id)
      if (generation !== generationRef.current || selection !== selectionRef.current) return
      setSelectedPaper(response.data)
      setAnalysis(analysisCacheRef.current.get(paper.id) || null)
      setAnalysisLoading(analysisRequestsRef.current.has(paper.id))
    } catch {
      if (generation !== generationRef.current || selection !== selectionRef.current) return
      setSelectedPaper({
        ...paper, references: [], citations: [], fields_of_study: [],
        keywords: [], publication_date: null, volume: null, issue: null, pages: null,
      })
      setAnalysis(analysisCacheRef.current.get(paper.id) || null)
      setAnalysisLoading(analysisRequestsRef.current.has(paper.id))
    }
  }, [searchRun?.run_id])

  const renderedGeneration = generationRef.current
  const renderedSelection = selectionRef.current
  const handleAnalyze = useCallback(async (customQuery?: string, replacePending = false) => {
    if (!selectedPaper || renderedGeneration !== generationRef.current ||
        (!replacePending && renderedSelection !== selectionRef.current) ||
        selectedPaperIdRef.current !== selectedPaper.id ||
        useSearchStore.getState().selectedPaper?.id !== selectedPaper.id) return
    const paperId = selectedPaper.id
    const generation = generationRef.current
    const pending = analysisRequestsRef.current.get(paperId)
    if (pending && !replacePending) return
    pending?.abort()
    const controller = new AbortController()
    analysisRequestsRef.current.set(paperId, controller)
    const isCurrentRequest = () => generation === generationRef.current
      && analysisRequestsRef.current.get(paperId) === controller
      && !controller.signal.aborted
    setAnalysisLoading(true)
    try {
      const response = await papersApi.analyze(paperId, {
        query: customQuery || query || t('search.placeholder'),
        analysis_type: 'full',
      }, controller.signal)
      if (!isCurrentRequest()) return
      analysisCacheRef.current.set(paperId, response.data)
      if (selectedPaperIdRef.current === paperId && useSearchStore.getState().selectedPaper?.id === paperId) {
        setAnalysis(response.data)
      }
    } catch (requestError: any) {
      if (!isCurrentRequest()) return
      const detail = requestError.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : t('common.error') + '. ' + t('common.retry'))
    } finally {
      if (isCurrentRequest()) {
        analysisRequestsRef.current.delete(paperId)
        if (selectedPaperIdRef.current === paperId && useSearchStore.getState().selectedPaper?.id === paperId) {
          setAnalysisLoading(false)
        }
      }
    }
  }, [selectedPaper, query, renderedGeneration, renderedSelection])

  const handleFulltextUploaded = useCallback(() => {
    if (!selectedPaper || renderedGeneration !== generationRef.current) return
    const paperId = selectedPaper.id
    // Uploads may finish after this paper's detail panel has been closed or replaced.
    analysisCacheRef.current.delete(paperId)
    analysisRequestsRef.current.get(paperId)?.abort()
    analysisRequestsRef.current.delete(paperId)
    if (selectedPaperIdRef.current !== paperId || useSearchStore.getState().selectedPaper?.id !== paperId) return
    setAnalysis(null)
    void handleAnalyze(undefined, true)
  }, [selectedPaper, handleAnalyze, renderedGeneration])

  const beginPanelResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (window.innerWidth < 768) return
    event.preventDefault()
    resizeStartRef.current = { x: event.clientX, width: panelWidth }
    setIsResizing(true)
  }

  const handleResizeKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const maximum = Math.max(360, Math.min(900, window.innerWidth * 0.72))
    const delta = event.key === 'ArrowLeft' ? 24 : -24
    setPanelWidth((current) => Math.min(maximum, Math.max(360, current + delta)))
  }

  const handleLibrarySearch = async () => {
    const activeQuery = (queryParam || query).trim()
    if (!activeQuery) return
    try {
      const { data } = await networkApi.libraryLink(activeQuery)
      await navigator.clipboard.writeText(data.query)
      window.open(data.url, '_blank', 'noopener,noreferrer')
      toast.success(locale === 'zh'
        ? '检索词已复制，请在图书馆页面登录后粘贴检索'
        : 'Query copied. Sign in to the library portal and paste it to search.')
    } catch {
      toast.error(locale === 'zh' ? '无法打开图书馆入口' : 'Unable to open library portal')
    }
  }

  const handleCloseDetail = () => {
    selectionRef.current++
    selectedPaperIdRef.current = null
    setAnalysisLoading(false)
    setSelectedPaper(null); setAnalysis(null); setEvidenceSpans([])
  }

  const progress = searchRun?.progress
  const progressPercent = progress
    ? Math.round((progress.completed_sources / Math.max(progress.total_sources, 1)) * 100)
    : 0
  const displayedElapsed = Math.max(elapsedMs, progress?.latency_ms || 0)
  const sourceCalls = progress?.source_calls || searchRun?.source_status || []

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      <div className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-3">
        <div className="max-w-4xl mx-auto flex items-center gap-2">
          <div className="flex-1">
            <SearchBar defaultValue={queryParam || query} size="sm" loading={isLoading} onSubmit={handleSearch} />
          </div>
          <button type="button" onClick={handleLibrarySearch}
            className="hidden sm:inline-flex items-center gap-1.5 px-3 h-10 rounded-xl border border-gray-200 dark:border-gray-700 text-xs text-gray-600 dark:text-gray-300 hover:border-primary-400 hover:text-primary-600 transition-colors"
            title={locale === 'zh' ? '复制检索词并打开学校图书馆' : 'Copy query and open the library portal'}>
            <ExternalLink className="w-3.5 h-3.5" />
            {locale === 'zh' ? '图书馆馆藏' : 'Library'}
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="h-1 bg-gray-100 dark:bg-gray-800">
          <div className="progress-bar" style={{ width: `${progressPercent}%` }} />
        </div>
      )}

      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          <div className="max-w-4xl mx-auto px-4 py-4">
            {error && (
              <div className="flex items-start gap-3 p-4 mb-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg animate-fade-in">
                <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-800 dark:text-red-300">{error}</p>
                  <button onClick={() => queryParam && performSearch(queryParam)}
                    className="text-xs text-red-600 dark:text-red-400 hover:underline mt-1">{t('common.retry')}</button>
                </div>
              </div>
            )}

            {isLoading && !searchRun && (
              <div className="space-y-3">{[1, 2, 3].map((i) => <PaperCardSkeleton key={i} />)}</div>
            )}

            {isLoading && searchRun && (
              <div className="p-4 mb-4 bg-primary-50 dark:bg-primary-900/20 border border-primary-100 dark:border-primary-800 rounded-xl">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin text-primary-600 dark:text-primary-400" />
                    <span className="text-sm font-medium text-primary-700 dark:text-primary-300">
                      {progress?.current_phase === 'queued' ? (locale === 'zh' ? '正在等待检索席位，尚未开始调用数据源...' : 'Waiting for a search slot; sources have not been called yet...') :
                       progress?.current_phase === 'searching' ? '正在并行检索学术 API...' :
                       progress?.current_phase === 'refining' ? '低召回，正在进行第二轮有界扩展...' :
                       progress?.current_phase === 'deduplicating' ? '正在跨来源去重...' :
                       progress?.current_phase === 'ranking' ? '正在计算相关度与质量排序...' :
                       progress?.current_phase === 'caching' ? '正在整理并缓存结果...' :
                       '正在理解查询并规划检索式...'}
                    </span>
                  </div>
                  <span className="inline-flex items-center gap-1 text-xs tabular-nums text-primary-600 dark:text-primary-300">
                    <Clock3 className="w-3.5 h-3.5" />
                    {locale === 'zh' ? '已用时' : 'Elapsed'} {(displayedElapsed / 1000).toFixed(1)}s
                  </span>
                </div>
                <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
                  {locale === 'zh'
                    ? '查询规划通常不超过 12 秒；各来源并行检索，慢源最长等待 45 秒。已完成的来源会立即显示。'
                    : 'Planning usually takes under 12s. Sources run in parallel with a 45s per-source ceiling.'}
                </p>
                {sourceCalls.length > 0 && (
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {sourceCalls.map((call, index) => (
                      <div key={`${call.source}-${call.query}-${index}`}
                        className="flex items-start gap-2 rounded-lg border border-primary-100/80 dark:border-primary-800/70 bg-white/70 dark:bg-gray-950/25 px-2.5 py-2">
                        {call.status === 'pending'
                          ? <Loader2 className="w-3.5 h-3.5 mt-0.5 animate-spin text-amber-500" />
                          : <Database className={`w-3.5 h-3.5 mt-0.5 ${call.success ? 'text-emerald-500' : 'text-red-500'}`} />}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-semibold truncate text-gray-700 dark:text-gray-200">
                              {call.label || call.source}
                            </span>
                            <span className="text-[10px] text-gray-500 dark:text-gray-400 whitespace-nowrap">
                              {call.status === 'pending'
                                ? (locale === 'zh' ? '等待/检索中' : 'Pending')
                                : call.success
                                  ? `${call.paper_count} ${locale === 'zh' ? '篇' : 'papers'} · ${(call.elapsed_ms / 1000).toFixed(1)}s`
                                  : (locale === 'zh' ? '失败降级' : 'Failed')}
                            </span>
                          </div>
                          <div className="text-[10px] text-gray-400 truncate" title={call.endpoint}>
                            {call.api_name || call.endpoint || call.source}
                          </div>
                          {call.query && <div className="text-[10px] text-gray-400 truncate" title={call.query}>{call.query}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {searchRun?.query_plan && (
              <QueryPlan plan={searchRun.query_plan} originalQuery={searchRun.original_query} />
            )}

            {searchRun?.status === 'completed' && (
              <SearchInsights run={searchRun} />
            )}

            {/* 搜索结果 */}
            {searchRun && (!isLoading || searchRun.results.length > 0) && (
              <div className="mt-4">
                <ResultsList papers={searchRun.results} selectedPaperId={selectedPaper?.id} onPaperClick={handlePaperClick} />
              </div>
            )}

            {/* 没有 URL 查询也没有历史结果时：显示空状态 + 最近搜索。 */}
            {!queryParam && !searchRun && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <BookOpen className="w-16 h-16 text-gray-200 dark:text-gray-700 mb-4" />
                <h3 className="text-lg font-medium text-gray-600 dark:text-gray-400 mb-2">
                  {locale === 'zh' ? '开始你的研究' : 'Start your research'}
                </h3>
                <p className="text-sm text-gray-400 dark:text-gray-500 max-w-sm mb-6">
                  {locale === 'zh'
                    ? '输入研究问题或主题，AI 会规划优化的子查询并搜索多个学术数据库'
                    : 'Enter a research question or topic above. The AI will plan optimized sub-queries and search across multiple academic databases.'}
                </p>
                {searchHistory.length > 0 && (
                  <div className="w-full max-w-xl">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-gray-400 dark:text-gray-500 flex items-center gap-1">
                        <Clock3 className="w-3.5 h-3.5" />
                        {locale === 'zh' ? '最近搜索' : 'Recent searches'}
                      </span>
                      <button
                        type="button"
                        onClick={() => { clearSearchHistory(); setSearchHistory([]) }}
                        className="text-xs text-gray-400 hover:text-red-500 transition-colors"
                      >
                        {locale === 'zh' ? '清空' : 'Clear'}
                      </button>
                    </div>
                    <div className="flex flex-wrap justify-center gap-2">
                      {searchHistory.slice(0, 8).map((item) => (
                        <button
                          key={`${item.query}-${item.at}`}
                          type="button"
                          onClick={() => navigate(`/search?q=${encodeURIComponent(item.query)}`)}
                          className="px-3 py-1.5 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-sm text-gray-600 dark:text-gray-300 hover:border-primary-300 dark:hover:border-primary-600 hover:text-primary-600 dark:hover:text-primary-400 transition-colors"
                        >
                          {item.query}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {!isLoading && searchRun?.status === 'completed' && searchRun.results.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <SearchIcon className="w-12 h-12 text-gray-300 dark:text-gray-600 mb-3" />
                <p className="text-gray-500 dark:text-gray-400 font-medium">{t('search.noResults')}</p>
                <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">{t('search.noResultsDesc')}</p>
              </div>
            )}
          </div>
        </div>

        {selectedPaper && (
          <div
            className={`search-detail-shell ${isResizing ? 'is-resizing' : ''}`}
            style={{ '--detail-panel-width': `${panelWidth}px` } as CSSProperties}
          >
            <div
              className="search-detail-resizer"
              role="separator"
              aria-label={locale === 'zh' ? '拖动调整论文详情宽度' : 'Resize paper details'}
              aria-orientation="vertical"
              aria-valuemin={360}
              aria-valuemax={900}
              aria-valuenow={panelWidth}
              tabIndex={0}
              onPointerDown={beginPanelResize}
              onKeyDown={handleResizeKeyDown}
            />
            <PaperDetailPanel
              key={selectedPaper.id}
              paper={selectedPaper} analysis={analysis} analysisLoading={analysisLoading}
              evidenceSpans={evidenceSpans} evidenceLoading={evidenceLoading}
              runId={searchRun?.run_id ?? null} onClose={handleCloseDetail} onAnalyze={handleAnalyze}
              onFulltextUploaded={handleFulltextUploaded}
            />
          </div>
        )}
      </div>
    </div>
  )
}

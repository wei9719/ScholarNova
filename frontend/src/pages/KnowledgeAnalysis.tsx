import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Sparkles, Loader2, AlertCircle,
  FolderOpen, ChevronDown, ChevronRight, CheckCircle,
} from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import { useLocaleStore } from '@/stores/localeStore'
import AnalysisViz from '@/components/AnalysisViz'
import { knowledgeApi } from '@/api/client'
import type { KnowledgeItem, AIAnalyzeResponse } from '@/api/types'
import './KnowledgeAnalysis.css'

const STORAGE_KEY = 'scholar-analysis-result'
const PAGE_SIZE = 20
const MAX_SELECTION = 50

interface AnalysisSnapshot {
  result: AIAnalyzeResponse
  knowledgeIds: string[]
  query: string
  title: string
  routeId?: string
  saveUncertain?: boolean
}

function restoreAnalysis(): AnalysisSnapshot | null {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    const result = saved?.result || saved
    if (typeof result?.analysis !== 'string' || !result.analysis.trim()) return null
    // Legacy results have no source IDs. Display them, but do not guess links.
    return {
      result: { ...result, model_completed: result.model_completed === true },
      knowledgeIds: Array.isArray(saved?.knowledgeIds)
        ? saved.knowledgeIds.filter((id: unknown) => typeof id === 'string').slice(0, MAX_SELECTION) : [],
      query: typeof saved?.query === 'string' ? saved.query : '',
      title: typeof saved?.title === 'string' ? saved.title : '',
      routeId: typeof saved?.routeId === 'string' ? saved.routeId : undefined,
      saveUncertain: saved?.saveUncertain === true,
    }
  } catch { return null }
}

export default function KnowledgeAnalysis() {
  const navigate = useNavigate()
  const { t, locale } = useLocaleStore()
  const isChinese = locale === 'zh'

  const [categories, setCategories] = useState<{ name: string; count: number }[]>([])
  const [categoryItems, setCategoryItems] = useState<Record<string, KnowledgeItem[]>>({})
  const [categoryPages, setCategoryPages] = useState<Record<string, number>>({})
  const [categoryLoading, setCategoryLoading] = useState<string | null>(null)
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeElapsed, setAnalyzeElapsed] = useState(0)
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(restoreAnalysis)
  const result = snapshot?.result || null
  const [query, setQuery] = useState(snapshot?.query || '')
  const [saving, setSaving] = useState(false)
  const active = useRef(true)
  const operation = useRef(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])

  useEffect(() => {
    try {
      if (snapshot) localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      toast.error(isChinese ? '本地缓存空间不足，请及时保存研究路线' : 'Local cache unavailable. Please save the research route.')
    }
  }, [snapshot, isChinese])

  const fetchCategories = useCallback(async () => {
    setLoading(true)
    try {
      const response = await knowledgeApi.getCategories()
      if (active.current) setCategories(response.data)
    } catch {
      if (active.current) setError(t('common.error'))
    } finally {
      if (active.current) setLoading(false)
    }
  }, [])

  useEffect(() => { fetchCategories() }, [fetchCategories])

  // 分析进行中显示已用时长
  useEffect(() => {
    if (analyzing) {
      const startedAt = Date.now()
      setAnalyzeElapsed(0)
      const timer = window.setInterval(() => {
        setAnalyzeElapsed(Math.floor((Date.now() - startedAt) / 1000))
      }, 500)
      return () => window.clearInterval(timer)
    }
  }, [analyzing])

  const loadCategory = async (catName: string, page: number) => {
    if (categoryLoading) return
    setCategoryLoading(catName)
    try {
      const response = await knowledgeApi.list(catName, { page, page_size: PAGE_SIZE })
      if (!active.current) return
      setCategoryItems((prev) => ({ ...prev, [catName]: [
        ...(page === 1 ? [] : prev[catName] || []), ...response.data.items,
      ].filter((item, index, all) => all.findIndex((other) => other.id === item.id) === index) }))
      setCategoryPages((prev) => ({ ...prev, [catName]: page }))
      setCategories((prev) => prev.map((cat) => cat.name === catName ? { ...cat, count: response.data.total } : cat))
    } catch {
      if (active.current) toast.error(t('common.error'))
    } finally {
      if (active.current) setCategoryLoading(null)
    }
  }

  const toggleCategory = async (catName: string) => {
    if (expandedCategory === catName) {
      setExpandedCategory(null)
      return
    }
    setExpandedCategory(catName)
    if (!categoryItems[catName]) {
      await loadCategory(catName, 1)
    }
  }

  const toggleItem = (id: string) => {
    if (analyzing) return
    if (!selectedIds.includes(id) && selectedIds.length >= MAX_SELECTION) {
      toast.error(isChinese ? '每轮最多选择 50 条知识，请分批分析' : 'Select up to 50 notes per analysis.')
      return
    }
    setSelectedIds((prev) => prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id])
  }

  const toggleAllInCategory = (catName: string) => {
    const items = categoryItems[catName] || []
    const allSelected = items.every((i) => selectedIds.includes(i.id))
    if (allSelected) {
      setSelectedIds((prev) => prev.filter((id) => !items.find((i) => i.id === id)))
    } else {
      const next = [...new Set([...selectedIds, ...items.map((i) => i.id)])]
      if (next.length > MAX_SELECTION) {
        toast.error(isChinese ? '每轮最多选择 50 条知识，请分批分析' : 'Select up to 50 notes per analysis.')
        return
      }
      setSelectedIds(next)
    }
  }

  const handleAnalyze = async () => {
    if (operation.current) return
    if (selectedIds.length === 0) {
      toast.error(isChinese ? '请至少选择一个知识点' : 'Select at least one item')
      return
    }
    operation.current = true
    setAnalyzing(true)
    setError(null)
    const knowledgeIds = [...selectedIds]
    const requirement = query.trim()
    const selectedCategories = Object.keys(categoryItems).filter((cat) =>
      categoryItems[cat].some((item) => knowledgeIds.includes(item.id)))
    try {
      const response = await knowledgeApi.aiAnalyze(knowledgeIds, requirement || undefined)
      if (!active.current) return
      setSnapshot({ result: response.data, knowledgeIds, query: requirement,
        title: selectedCategories.join(' / ').slice(0, 100) || (isChinese ? '研究路线' : 'Research Route') })
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      const msg = (typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map((item) => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean).slice(0, 3).join('；')
        : '') || t('common.error')
      if (active.current) { setError(msg); toast.error(msg) }
    } finally {
      operation.current = false
      if (active.current) setAnalyzing(false)
    }
  }

  const handleSaveRoute = async () => {
    if (!snapshot || snapshot.routeId || snapshot.saveUncertain || !snapshot.knowledgeIds.length || !snapshot.result.model_completed || operation.current) return
    const pendingSnapshot = { ...snapshot, saveUncertain: true }
    // Persist uncertainty before sending a write. Leaving the page must not
    // turn a possibly successful server write into an apparently unsaved result.
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(pendingSnapshot))
    } catch {
      setError(isChinese ? '无法保存本地确认标记，本次尚未创建路线。请释放缓存空间后重试。' : 'Could not persist the save marker. No route was created. Free local storage and retry.')
      return
    }
    operation.current = true
    setSaving(true)
    setSnapshot(pendingSnapshot)
    setError(null)
    try {
      const response = await knowledgeApi.createRoute({
        title: snapshot.title,
        description: [snapshot.query ? `研究要求：${snapshot.query}` : '', snapshot.result.analysis].filter(Boolean).join('\n\n'),
        knowledge_ids: snapshot.knowledgeIds,
      })
      if (!active.current) return
      setSnapshot((current) => current === pendingSnapshot ? { ...pendingSnapshot, routeId: response.data.id, saveUncertain: false } : current)
      toast.success(isChinese ? '完整分析与来源已保存为研究路线' : 'Full analysis and sources saved as a research route')
    } catch {
      if (active.current) setError(isChinese ? '保存结果未确认，分析仍在。请先检查研究路线列表，再决定是否重试，避免重复创建。' : 'Save not confirmed. Your analysis is retained. Check existing routes before retrying to avoid duplicates.')
    } finally {
      operation.current = false
      if (active.current) setSaving(false)
    }
  }

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      <div className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-3">
        <div className="max-w-4xl mx-auto flex items-center gap-3">
          <button onClick={() => navigate('/knowledge')}
            className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 transition-colors">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <Sparkles className="w-5 h-5 text-primary-600 dark:text-primary-400" />
          <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t('knowledge.aiAnalysis')}</h1>
          {selectedIds.length > 0 && !result && (
            <span className="ml-auto text-xs text-gray-500">{isChinese ? `已选 ${selectedIds.length} 个` : `${selectedIds.length} selected`}</span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="max-w-4xl mx-auto px-4 py-6">
          {!result ? (
            <>
              <div className="mb-4">
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
                  {isChinese ? '选择与研究问题相关的知识点，每轮最多 50 条。分析基于知识摘录，不代表阅读了完整论文。' : 'Select up to 50 relevant notes. Analysis uses note excerpts, not full papers.'}
                </p>
                <label htmlFor="research-requirement" className="block text-sm font-medium mb-2">
                  {isChinese ? '研究目标与约束（可选）' : 'Research goal and constraints (optional)'}
                </label>
                <textarea id="research-requirement" value={query} maxLength={2000} disabled={analyzing}
                  onChange={(event) => setQuery(event.target.value)} rows={3}
                  placeholder={isChinese ? '例如：比较两种食品保鲜方法，重点关注实验设计，不涉及模型训练。' : 'For example: compare two preservation methods, focusing on experimental design.'}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 p-3 text-sm" />
                <p className="text-xs text-gray-500 mt-1">{query.length}/2000 · {isChinese ? `已选 ${selectedIds.length}/50 条` : `${selectedIds.length}/50 selected`}</p>
              </div>

              {loading ? (
                <div className="space-y-2">{[1, 2].map((i) => <div key={i} className="skeleton h-14 rounded-lg" />)}</div>
              ) : categories.length === 0 ? (
                <div className="text-center py-8 text-gray-400">{isChinese ? '知识库为空' : 'Empty knowledge base'}</div>
              ) : (
                <div className="space-y-2">
                  {categories.map((cat) => {
                    const isExpanded = expandedCategory === cat.name
                    const items = categoryItems[cat.name] || []
                    const allSelected = items.length > 0 && items.every((i) => selectedIds.includes(i.id))

                    return (
                      <div key={cat.name} className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
                        <div className="flex items-center gap-2 p-3 bg-gray-50 dark:bg-gray-800/50">
                          <button onClick={() => toggleCategory(cat.name)} disabled={analyzing || !!categoryLoading} className="flex items-center gap-2 flex-1 text-left">
                            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                            <FolderOpen className="w-4 h-4 text-primary-500" />
                            <span className="text-sm font-medium text-gray-800 dark:text-gray-200">{cat.name}</span>
                            <span className="text-xs text-gray-400">({cat.count})</span>
                          </button>
                          {isExpanded && items.length > 0 && (
                            <button onClick={() => toggleAllInCategory(cat.name)} disabled={analyzing}
                              className="text-xs text-primary-600 dark:text-primary-400 hover:underline px-2">
                              {allSelected ? (isChinese ? '取消已加载选择' : 'Deselect loaded') : (isChinese ? '选择已加载条目' : 'Select loaded')}
                            </button>
                          )}
                        </div>
                        {isExpanded && (
                          <div className="border-t border-gray-200 dark:border-gray-700">
                            {items.length === 0 ? (
                              <div className="p-3 text-sm text-gray-400 text-center">
                                {categoryLoading === cat.name ? (isChinese ? '加载中...' : 'Loading...') : (
                                  <button onClick={() => loadCategory(cat.name, 1)} disabled={analyzing}>
                                    {isChinese ? '暂无条目，点击重新加载' : 'No items loaded. Retry'}
                                  </button>
                                )}
                              </div>
                            ) : (
                              <div className="divide-y divide-gray-100 dark:divide-gray-700/50">
                                {items.map((item) => (
                                  <button key={item.id} onClick={() => toggleItem(item.id)} disabled={analyzing} aria-pressed={selectedIds.includes(item.id)}
                                    className={clsx('w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors',
                                      selectedIds.includes(item.id) ? 'bg-primary-50 dark:bg-primary-900/20' : 'hover:bg-gray-50 dark:hover:bg-gray-800/30')}>
                                    <div className={clsx('w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0',
                                      selectedIds.includes(item.id) ? 'border-primary-500 bg-primary-500' : 'border-gray-300 dark:border-gray-600')}>
                                      {selectedIds.includes(item.id) && <CheckCircle className="w-3 h-3 text-white" />}
                                    </div>
                                    <span className="text-sm text-gray-700 dark:text-gray-300 truncate">{item.title}</span>
                                  </button>
                                ))}
                              </div>
                            )}
                            {items.length < cat.count && items.length > 0 && (
                              <button disabled={analyzing || !!categoryLoading}
                                onClick={() => loadCategory(cat.name, (categoryPages[cat.name] || 1) + 1)}
                                className="w-full p-3 text-sm text-primary-600 dark:text-primary-400 disabled:opacity-50">
                                {categoryLoading === cat.name ? (isChinese ? '加载中...' : 'Loading...') :
                                  (isChinese ? `加载更多（已显示 ${items.length}/${cat.count}）` : `Load more (${items.length}/${cat.count})`)}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}

              <div className="flex justify-center mt-6">
                <button onClick={handleAnalyze} disabled={analyzing || selectedIds.length === 0}
                  className={clsx('inline-flex items-center gap-2 px-6 py-3 text-sm font-medium rounded-lg transition-colors',
                    analyzing || selectedIds.length === 0 ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 cursor-not-allowed' : 'bg-primary-600 text-white hover:bg-primary-700')}>
                  {analyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                  {analyzing ? (isChinese ? `分析中... 已用时 ${analyzeElapsed}s` : `Analyzing... ${analyzeElapsed}s`) : (isChinese ? 'AI 分析研究方向' : 'AI Research Analysis')}
                </button>
              </div>

              {error && (
                <div role="alert" className="mt-4 flex items-center gap-2 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-400">
                  <AlertCircle className="w-4 h-4" />{error}
                </div>
              )}
            </>
          ) : (
            <div className="animate-fade-in">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t('knowledge.aiResultTitle')}</h2>
                <button disabled={saving} onClick={() => { setSnapshot(null); setSelectedIds(snapshot?.knowledgeIds || []); setError(null) }}
                  className="text-sm text-primary-600 dark:text-primary-400 hover:underline">
                  {isChinese ? '重新分析' : 'Re-analyze'}
                </button>
              </div>

              {snapshot?.query && <p className="mb-3 text-sm text-gray-600 dark:text-gray-300">
                {isChinese ? '本次研究要求：' : 'Research requirements: '}{snapshot.query}
              </p>}

              <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                <span className={clsx('rounded-full px-2.5 py-1', result.model_completed
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300')}>
                  {result.model_completed
                    ? (result.fallback_used ? (isChinese ? '备用模型已接管' : 'Fallback model used') : (isChinese ? '模型分析完成' : 'Model analysis complete'))
                    : (isChinese ? '规则兜底结果' : 'Deterministic fallback')}
                </span>
                {result.provider && result.model && <span>{result.provider}/{result.model}</span>}
                <span>Token {result.total_tokens || 0}</span>
              </div>

              <AnalysisViz
                analysis={result.analysis || ''}
                architectureJson={result.architecture_json || null}
              />
              <div className="mt-5 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <p className="text-sm text-gray-500 mb-3">
                  {isChinese ? '核对分析后再保存。保存会保留完整文字、研究要求和所选知识关联，不会自动把 AI 推测写入知识库。' : 'Review before saving. The route keeps the full text, requirements and source links without adding AI conjectures to your knowledge base.'}
                </p>
                {snapshot?.routeId ? (
                  <button onClick={() => navigate(`/knowledge/route/${snapshot.routeId}`)} className="text-primary-600 dark:text-primary-400 hover:underline">
                    {isChinese ? '查看已保存的研究路线' : 'Open saved research route'}
                  </button>
                ) : (
                  <button onClick={handleSaveRoute} disabled={saving || snapshot?.saveUncertain || !result.model_completed || !snapshot?.knowledgeIds.length || !result.analysis.trim()}
                    className="rounded-lg bg-primary-600 text-white px-4 py-2 text-sm disabled:opacity-50">
                    {saving ? (isChinese ? '保存中...' : 'Saving...') : (isChinese ? '保存为研究路线' : 'Save as research route')}
                  </button>
                )}
                {snapshot?.saveUncertain && !snapshot.routeId && !saving && (
                  <div role="status" className="mt-3 text-sm text-amber-700 dark:text-amber-300">
                    <p>{isChinese ? '上次保存结果尚未确认。请先核对研究路线列表，避免重复创建；确认没有该路线后才允许重新保存。' : 'The previous save is unconfirmed. Check the route list to avoid duplicates; retry only after confirming the route is absent.'}</p>
                    <div className="mt-2 flex flex-wrap gap-3">
                      <button onClick={() => navigate('/knowledge')} className="underline">{isChinese ? '查看研究路线列表' : 'Check research route list'}</button>
                      <button onClick={() => { setSnapshot({ ...snapshot, saveUncertain: false }); setError(null) }} className="underline">
                        {isChinese ? '已核对且未找到，允许重新保存' : 'Checked and not found; allow saving again'}
                      </button>
                    </div>
                  </div>
                )}
                {!result.model_completed && <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
                  {isChinese ? '模型未完成分析，请重新分析后再保存研究路线。' : 'The model did not complete the analysis. Retry before saving a route.'}
                </p>}
                {!snapshot?.knowledgeIds.length && <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
                  {isChinese ? '旧结果没有保存来源关联，请重新选择知识并分析。' : 'This legacy result has no source links. Select notes and analyze again.'}
                </p>}
                {error && <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

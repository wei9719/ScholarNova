import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Plus, Search, BookMarked, FolderOpen, Route,
  Sparkles, Loader2, AlertCircle, Trash2,
} from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import { useLocaleStore } from '@/stores/localeStore'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { knowledgeApi } from '@/api/client'
import KnowledgeDetail from '@/components/KnowledgeDetail/KnowledgeDetail'
import KnowledgeForm from '@/components/KnowledgeForm/KnowledgeForm'

export default function Knowledge() {
  const navigate = useNavigate()
  const { t, locale } = useLocaleStore()
  const isChinese = locale === 'zh'

  const {
    items, total, categories, selectedCategory, searchQuery,
    selectedItem, detailOpen,
    formOpen, editingItem, formPrefill,
    routes,
    setItems, setCategories, setSelectedCategory, setSearchQuery,
    setSelectedItem, setDetailOpen,
    setFormOpen, setEditingItem, setFormPrefill,
    setRoutes,
  } = useKnowledgeStore()

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [routesLoading, setRoutesLoading] = useState(false)
  const [routesError, setRoutesError] = useState(false)
  const [routesPage, setRoutesPage] = useState(1)
  const [routesTotal, setRoutesTotal] = useState(0)
  const routesFetchSequence = useRef(0)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState(searchQuery.trim())
  const fetchSequence = useRef(0)
  const pageSize = 20

  useEffect(() => {
    const nextKeyword = searchQuery.trim()
    if (nextKeyword === keyword) return
    const timer = window.setTimeout(() => {
      setKeyword(nextKeyword)
      setPage(1)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [searchQuery, keyword])

  const fetchItems = useCallback(async () => {
    const sequence = ++fetchSequence.current
    setLoading(true)
    setError(null)
    try {
      const response = await knowledgeApi.list(selectedCategory || undefined, {
        page, page_size: pageSize, ...(keyword ? { keyword } : {}),
      })
      if (sequence !== fetchSequence.current) return
      const lastPage = Math.max(1, Math.ceil(response.data.total / pageSize))
      if (page > lastPage) {
        setPage(lastPage)
        return
      }
      setItems(response.data.items, response.data.total)
      setCategories(response.data.categories)
    } catch {
      if (sequence === fetchSequence.current) setError(t('common.error'))
    } finally {
      if (sequence === fetchSequence.current) setLoading(false)
    }
  }, [selectedCategory, keyword, page])

  const fetchRoutes = useCallback(async () => {
    const sequence = ++routesFetchSequence.current
    setRoutesLoading(true)
    setRoutesError(false)
    try {
      const response = await knowledgeApi.listRoutes({ page: routesPage, page_size: pageSize })
      if (sequence !== routesFetchSequence.current) return
      const lastPage = Math.max(1, Math.ceil(response.data.total / pageSize))
      if (routesPage > lastPage) {
        setRoutesPage(lastPage)
        return
      }
      setRoutes(response.data.items)
      setRoutesTotal(response.data.total)
    } catch {
      if (sequence === routesFetchSequence.current) setRoutesError(true)
    } finally {
      if (sequence === routesFetchSequence.current) setRoutesLoading(false)
    }
  }, [routesPage])

  useEffect(() => {
    fetchItems()
    return () => { fetchSequence.current += 1 }
  }, [fetchItems])

  useEffect(() => {
    fetchRoutes()
    return () => { routesFetchSequence.current += 1 }
  }, [fetchRoutes])

  const handleCreate = () => {
    setEditingItem(null)
    setFormPrefill(null)
    setFormOpen(true)
  }

  const handleEdit = (item: any) => {
    setEditingItem(item)
    setFormPrefill(null)
    setFormOpen(true)
    setDetailOpen(false)
  }

  const handleDelete = (_id: string) => {
    fetchItems()
    fetchRoutes()
  }

  const handleFormSuccess = () => {
    fetchItems()
    fetchRoutes()
  }


  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const totalRoutePages = Math.max(1, Math.ceil(routesTotal / pageSize))
  const chooseCategory = (category: string | null) => {
    setPage(1)
    setSelectedCategory(category)
  }

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      {/* Header */}
      <div className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BookMarked className="w-5 h-5 text-primary-600 dark:text-primary-400" />
            <div>
              <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                {t('knowledge.title')}
              </h1>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {t('knowledge.subtitle')}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={isChinese ? '搜索全部知识的标题和正文' : 'Search all knowledge titles and text'}
                className="pl-9 pr-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500 w-48 sm:w-64"
              />
            </div>
            <button
              onClick={() => navigate('/knowledge/analysis')}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 hover:bg-primary-100 dark:hover:bg-primary-900/50 transition-colors"
            >
              <Sparkles className="w-4 h-4" />
              <span className="hidden sm:inline">{t('knowledge.aiAnalysis')}</span>
            </button>
            <button
              onClick={handleCreate}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700 transition-colors"
            >
              <Plus className="w-4 h-4" />
              <span className="hidden sm:inline">{t('knowledge.create')}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar - Categories */}
        <div className="w-56 flex-shrink-0 border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50 overflow-y-auto custom-scrollbar">
          <div className="p-3">
            <h3 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2 px-2">
              {t('knowledge.category')}
            </h3>
            <div className="space-y-0.5">
              <button
                onClick={() => chooseCategory(null)}
                className={clsx(
                  'w-full flex items-center justify-between px-2 py-1.5 rounded-md text-sm transition-colors',
                  !selectedCategory
                    ? 'bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 font-medium'
                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                )}
              >
                <span className="flex items-center gap-2">
                  <FolderOpen className="w-4 h-4" />
                  {t('knowledge.all')}
                </span>
                <span className="text-xs text-gray-400">{categories.reduce((sum, cat) => sum + cat.count, 0)}</span>
              </button>
              {categories.map((cat) => (
                <div key={cat.name} className={clsx(
                  'flex items-center px-2 py-1.5 rounded-md text-sm transition-colors group',
                  selectedCategory === cat.name
                    ? 'bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 font-medium'
                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                )}>
                  <button
                    onClick={() => chooseCategory(cat.name)}
                    className="flex-1 flex items-center gap-2 text-left"
                  >
                    <FolderOpen className="w-4 h-4" />
                    {cat.name}
                    <span className="text-xs text-gray-400 ml-auto">{cat.count}</span>
                  </button>
                </div>
              ))}
            </div>
            <p className="mt-3 px-2 text-xs text-gray-400">
              {isChinese ? '分类批量删除暂不可用，请在条目详情逐条确认删除。' : 'Bulk category deletion is unavailable. Delete individual entries from their details.'}
            </p>

            {/* Routes Section */}
            <div className="mt-6">
              <h3 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2 px-2">
                {t('knowledge.routes')}
              </h3>
              {routesLoading ? (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                </div>
              ) : routesError ? (
                <div role="alert" className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
                  <p>{isChinese ? '研究路线加载失败，尚无法确认列表。' : 'Research routes could not be loaded; the list is unavailable.'}</p>
                  <button onClick={fetchRoutes} aria-label={isChinese ? '重试加载研究路线' : 'Retry loading research routes'} className="mt-1 underline">
                    {t('common.retry')}
                  </button>
                </div>
              ) : routes.length > 0 ? (
                <div className="space-y-0.5">
                  {routes.map((route) => (
                    <div key={route.id} className="flex items-center group">
                      <button
                        onClick={() => navigate(`/knowledge/route/${route.id}`)}
                        className="flex-1 flex items-center gap-2 px-2 py-1.5 rounded-md text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-left"
                      >
                        <Route className="w-4 h-4 flex-shrink-0" />
                        <span className="truncate">{route.title}</span>
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          if (confirm(isChinese ? `确定删除研究路线「${route.title}」吗？` : `Delete route "${route.title}"?`)) {
                            knowledgeApi.deleteRoute(route.id).then(() => {
                              toast.success(isChinese ? `已删除「${route.title}」` : `Deleted "${route.title}"`)
                              fetchRoutes()
                            }).catch(() => toast.error(t('common.error')))
                          }
                        }}
                        className="p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-gray-400 px-2">{t('common.noData')}</p>
              )}
              {!routesError && (
                <nav aria-label={isChinese ? '研究路线分页' : 'Research route pagination'} className="mt-3 px-2 text-xs text-gray-500">
                  <p>{isChinese ? `共 ${routesTotal} 条路线 · 第 ${routesPage} / ${totalRoutePages} 页` : `${routesTotal} routes · Page ${routesPage} / ${totalRoutePages}`}</p>
                  <div className="mt-2 flex gap-2">
                    <button aria-label={isChinese ? '上一页路线' : 'Previous routes page'} disabled={routesLoading || routesPage <= 1} onClick={() => setRoutesPage((value) => value - 1)} className="rounded border px-2 py-1 disabled:opacity-40">
                      {isChinese ? '上一页' : 'Previous'}
                    </button>
                    <button aria-label={isChinese ? '下一页路线' : 'Next routes page'} disabled={routesLoading || routesPage >= totalRoutePages} onClick={() => setRoutesPage((value) => value + 1)} className="rounded border px-2 py-1 disabled:opacity-40">
                      {isChinese ? '下一页' : 'Next'}
                    </button>
                  </div>
                </nav>
              )}
            </div>
          </div>
        </div>

        {/* Right Content - Knowledge List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          <div className="px-4 py-4">
            {error && (
              <div className="flex items-start gap-3 p-4 mb-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg animate-fade-in">
                <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-800 dark:text-red-300">{error}</p>
                  <button onClick={fetchItems} className="text-xs text-red-600 dark:text-red-400 hover:underline mt-1">
                    {t('common.retry')}
                  </button>
                </div>
              </div>
            )}

            {loading ? (
              <div className="space-y-3">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="skeleton h-28 rounded-lg" />
                ))}
              </div>
            ) : items.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <BookMarked className="w-16 h-16 text-gray-200 dark:text-gray-700 mb-4" />
                <h3 className="text-lg font-medium text-gray-600 dark:text-gray-400 mb-2">
                  {keyword ? (isChinese ? '没有匹配的知识条目' : 'No matching knowledge entries') : t('knowledge.empty')}
                </h3>
                <p className="text-sm text-gray-400 dark:text-gray-500 max-w-sm mb-6">
                  {keyword ? (isChinese ? '已搜索所选范围的全部标题与正文，请调整关键词。' : 'All titles and text in the selected scope were searched. Try another keyword.') : t('knowledge.emptyDesc')}
                </p>
                <button
                  onClick={handleCreate}
                  className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700 transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  {t('knowledge.create')}
                </button>
              </div>
            ) : (
              <div className="space-y-3">
                {items.map((item) => (
                  <KnowledgeCard
                    key={item.id}
                    item={item}
                    isChinese={isChinese}
                    onClick={() => setSelectedItem(item)}
                  />
                ))}
              </div>
            )}
            <nav aria-label={isChinese ? '知识库分页' : 'Knowledge pagination'} className="mt-4 flex items-center justify-between gap-3 text-sm text-gray-500">
              <span>{isChinese ? `共 ${total} 条 · 第 ${page} / ${totalPages} 页` : `${total} entries · Page ${page} / ${totalPages}`}</span>
              <div className="flex gap-2">
                <button disabled={loading || page <= 1} onClick={() => setPage((value) => value - 1)} className="rounded border px-3 py-1.5 disabled:opacity-40">
                  {isChinese ? '上一页' : 'Previous'}
                </button>
                <button disabled={loading || page >= totalPages} onClick={() => setPage((value) => value + 1)} className="rounded border px-3 py-1.5 disabled:opacity-40">
                  {isChinese ? '下一页' : 'Next'}
                </button>
              </div>
            </nav>
          </div>
        </div>
      </div>

      {/* Detail Panel - Overlay */}
      {detailOpen && selectedItem && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={() => setDetailOpen(false)} />
          <div className="relative w-full max-w-lg h-full bg-white dark:bg-gray-900 shadow-2xl overflow-y-auto">
            <KnowledgeDetail
              item={selectedItem}
              onClose={() => setDetailOpen(false)}
              onEdit={handleEdit}
              onDelete={handleDelete}
            />
          </div>
        </div>
      )}

      {/* Create/Edit Form */}
      <KnowledgeForm
        open={formOpen}
        editingItem={editingItem}
        prefill={formPrefill}
        categories={categories}
        onClose={() => { setFormOpen(false); setEditingItem(null); setFormPrefill(null) }}
        onSuccess={handleFormSuccess}
      />

    </div>
  )
}

// Knowledge Card Component
function KnowledgeCard({
  item, isChinese, onClick,
}: {
  item: any
  isChinese: boolean
  onClick: () => void
}) {
  const cardType = item.card_type
  const cardStyles: Record<string, { icon: string; bg: string; border: string }> = {
    direction: { icon: '🧭', bg: 'bg-blue-50 dark:bg-blue-900/20', border: 'border-blue-200 dark:border-blue-700' },
    architecture: { icon: '🏗️', bg: 'bg-purple-50 dark:bg-purple-900/20', border: 'border-purple-200 dark:border-purple-700' },
    paper: { icon: '📄', bg: 'bg-orange-50 dark:bg-orange-900/20', border: 'border-orange-200 dark:border-orange-700' },
    research_point: { icon: '💡', bg: 'bg-green-50 dark:bg-green-900/20', border: 'border-green-200 dark:border-green-700' },
  }
  const style = cardType ? cardStyles[cardType] : null

  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full text-left p-4 rounded-lg border transition-all animate-fade-in',
        style
          ? `${style.bg} ${style.border} hover:shadow-md`
          : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-primary-300 dark:hover:border-primary-700 hover:shadow-sm'
      )}
    >
      <div className="flex items-start gap-3">
        <div className={clsx(
          'w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 text-base',
          style ? 'bg-white/60 dark:bg-gray-800/60' : 'bg-primary-50 dark:bg-primary-900/30'
        )}>
          {style ? style.icon : <BookMarked className="w-4 h-4 text-primary-500" />}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
            {item.title}
          </h3>
          {cardType && (
            <span className={clsx(
              'inline-block px-1.5 py-0.5 text-[10px] font-medium rounded mt-1',
              style?.bg, style?.border
            )}>
              {cardType === 'direction' ? (isChinese ? '研究方向' : 'Direction') :
               cardType === 'architecture' ? (isChinese ? '架构图' : 'Architecture') :
               cardType === 'paper' ? (isChinese ? '推荐论文' : 'Paper') :
               (isChinese ? '研究点' : 'Research Point')}
            </span>
          )}
          {item.source_paper_title && !cardType && (
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">
              {isChinese ? '来源: ' : 'Source: '}{item.source_paper_title}
            </p>
          )}
          {item.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {item.tags.slice(0, 5).map((tag: string, i: number) => (
                <span key={i} className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-white/60 dark:bg-gray-800/60 text-gray-600 dark:text-gray-400">
                  #{tag}
                </span>
              ))}
              {item.tags.length > 5 && (
                <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-white/60 dark:bg-gray-800/60 text-gray-500">
                  +{item.tags.length - 5}
                </span>
              )}
            </div>
          )}
          <div className="flex items-center gap-3 mt-2 text-[11px] text-gray-400 dark:text-gray-500">
            <span>{item.category || (isChinese ? '未分类' : 'Uncategorized')}</span>
            <span>{new Date(item.created_at).toLocaleDateString()}</span>
          </div>
        </div>
      </div>
    </button>
  )
}

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Loader2, Route, BookMarked, Sparkles,
  AlertCircle, RefreshCw, FileDown, Printer,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { useLocaleStore } from '@/stores/localeStore'
import { knowledgeApi } from '@/api/client'
import type { ResearchRoute, KnowledgeItem } from '@/api/types'
import AnalysisViz from '@/components/AnalysisViz'
import { buildRouteDocumentHtml, cleanRouteMarkdown } from '@/utils/routeDocument'
import './KnowledgeAnalysis.css'

export default function RouteDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t, locale } = useLocaleStore()
  const isChinese = locale === 'zh'

  const [route, setRoute] = useState<ResearchRoute | null>(null)
  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeItem[]>([])
  const [unavailableKnowledge, setUnavailableKnowledge] = useState(0)
  const routeFetchSequence = useRef(0)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [descExpanded, setDescExpanded] = useState(false)
  const [generateElapsed, setGenerateElapsed] = useState(0)
  const [generateProgress, setGenerateProgress] = useState(0)
  const [generateStage, setGenerateStage] = useState<string>('')

  // 从 ai_analysis 解析实际调用模型（格式：## 文字分析（zhipu/glm-4-plus））
  const modelLabels = useMemo(() => {
    const textMatch = route?.ai_analysis?.match(/##\s*文字分析[（(]([^）)]+)[）)]/)
    const diagramMatch = route?.ai_analysis?.match(/##\s*研究架构图[（(]([^）)]+)[）)]/)
    return {
      text: textMatch?.[1] || '',
      diagram: diagramMatch?.[1] || '',
    }
  }, [route?.ai_analysis])

  const handleExportDoc = useCallback(() => {
    if (!route) return
    const html = buildRouteDocumentHtml(route, window.location.origin)
    const blob = new Blob(['\ufeff' + html], { type: 'application/msword' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(route.title || '研究路线').replace(/[\\/:*?"<>|]/g, '_')}.doc`
    a.click()
    URL.revokeObjectURL(url)
  }, [route])

  const handleExportPdf = useCallback(() => {
    if (!route) return
    const html = buildRouteDocumentHtml(route, window.location.origin)
    const iframe = document.createElement('iframe')
    iframe.style.position = 'fixed'
    iframe.style.right = '0'
    iframe.style.bottom = '0'
    iframe.style.width = '0'
    iframe.style.height = '0'
    iframe.style.border = '0'
    document.body.appendChild(iframe)
    const doc = iframe.contentDocument || iframe.contentWindow?.document
    if (doc) {
      doc.open()
      doc.write(html)
      doc.close()
      setTimeout(() => {
        iframe.contentWindow?.focus()
        iframe.contentWindow?.print()
        setTimeout(() => iframe.remove(), 2000)
      }, 400)
    }
  }, [route])

  const fetchRoute = useCallback(async () => {
    if (!id) return
    const sequence = ++routeFetchSequence.current
    setLoading(true)
    setError(null)
    setKnowledgeItems([])
    setUnavailableKnowledge(0)
    try {
      const response = await knowledgeApi.getRoute(id)
      if (sequence !== routeFetchSequence.current) return
      setRoute(response.data)
      const ids = [...new Set(response.data.knowledge_ids || [])]
      const related: KnowledgeItem[] = []
      let unavailable = 0
      // Resolve actual references, not the first page of the global library.
      for (let offset = 0; offset < ids.length; offset += 4) {
        const results = await Promise.allSettled(ids.slice(offset, offset + 4).map((kid) => knowledgeApi.get(kid)))
        if (sequence !== routeFetchSequence.current) return
        for (const result of results) {
          if (result.status === 'fulfilled') related.push(result.value.data)
          else unavailable += 1
        }
      }
      setKnowledgeItems(related)
      setUnavailableKnowledge(unavailable)
    } catch {
      if (sequence === routeFetchSequence.current) setError(t('common.error'))
    } finally {
      if (sequence === routeFetchSequence.current) setLoading(false)
    }
  }, [id])

  useEffect(() => {
    fetchRoute()
    return () => { routeFetchSequence.current += 1 }
  }, [fetchRoute])

  const handleGenerate = async () => {
    if (!id) return
    setGenerating(true)
    setGenerateElapsed(0)
    setGenerateProgress(0)
    setGenerateStage('')
    const startedAt = Date.now()
    const timer = window.setInterval(() => {
      setGenerateElapsed(Math.floor((Date.now() - startedAt) / 1000))
    }, 500)
    try {
      let streamError: string | null = null
      let completed = false
      let diagramGenerated = false
      let roadmapGenerated = false
      let planningFallback = false
      await knowledgeApi.generateRouteAnalysisStream(id, (evt) => {
        if (evt.progress != null) setGenerateProgress(evt.progress)
        if (evt.stage) setGenerateStage(evt.stage)
        if (evt.event === 'error') {
          streamError = evt.message || t('common.error')
        }
        if (evt.event === 'done') completed = true
        if (evt.stage === 'diagram' && evt.data) diagramGenerated = Boolean(evt.data.image_url)
        if (evt.stage === 'roadmap' && evt.data) roadmapGenerated = Boolean(evt.data.roadmap_url)
        if (evt.data?.plan_source && evt.data.plan_source !== 'model') planningFallback = true
      })
      if (streamError || !completed) {
        toast.error(streamError || (isChinese ? '生成连接已中断，请重试' : 'Generation ended before completion. Please retry.'))
        return
      }
      await fetchRoute()
      if (!diagramGenerated || !roadmapGenerated) {
        toast.error(isChinese ? '分析已保存，部分图像未生成，请查看结果后重试' : 'Analysis saved, but some images were not generated. Review the results and retry.')
      } else if (planningFallback) {
        toast.error(isChinese ? '图像已生成，但部分规划为规则回退，尚非完整定制方案' : 'Images generated, but some plans use generic fallback templates, not a fully tailored plan.')
      } else {
        toast.success(isChinese ? '分析生成成功' : 'Analysis generated successfully')
      }
    } catch {
      toast.error(t('common.error'))
    } finally {
      window.clearInterval(timer)
      setGenerating(false)
    }
  }

  if (loading) {
    return (
      <div className="h-[calc(100vh-3.5rem)] flex flex-col">
        <div className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-3">
          <div className="max-w-4xl mx-auto flex items-center gap-3">
            <button onClick={() => navigate('/knowledge')} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500">
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-2">
              <Route className="w-5 h-5 text-primary-600 dark:text-primary-400" />
              <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t('knowledge.routeDetail')}</h1>
            </div>
          </div>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-primary-500" />
        </div>
      </div>
    )
  }

  if (error || !route) {
    return (
      <div className="h-[calc(100vh-3.5rem)] flex flex-col">
        <div className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-3">
          <div className="max-w-4xl mx-auto flex items-center gap-3">
            <button onClick={() => navigate('/knowledge')} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500">
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-2">
              <Route className="w-5 h-5 text-primary-600 dark:text-primary-400" />
              <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t('knowledge.routeDetail')}</h1>
            </div>
          </div>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center gap-3">
          <AlertCircle className="w-12 h-12 text-red-300" />
          <p className="text-gray-500">{error || t('common.noData')}</p>
          <button onClick={fetchRoute} className="text-sm text-primary-600 hover:underline">{t('common.retry')}</button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      {/* Header */}
      <div className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-3">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => navigate('/knowledge')} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500">
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-2">
              <Route className="w-5 h-5 text-primary-600 dark:text-primary-400" />
              <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100">{route.title}</h1>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {generating && (
              <span className="inline-flex items-center gap-1.5 text-xs text-primary-600 dark:text-primary-300 tabular-nums">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                {generateProgress > 0 ? `${generateProgress}%` : '连接中...'} · {generateElapsed}s
              </span>
            )}
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 hover:bg-primary-100 dark:hover:bg-primary-900/50 transition-colors disabled:opacity-50"
            >
              {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {generating ? t('knowledge.routeGenerating') : t('knowledge.routeGenerate')}
            </button>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="max-w-4xl mx-auto px-4 py-6">
          {/* Description */}
          {route.description && (
            <div className="analysis-section mb-6">
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                {t('knowledge.routeDescription')}
              </h2>
              {(() => {
                const cleanDesc = cleanRouteMarkdown(route.description)
                const isLong = cleanDesc.length > 320
                const shown = descExpanded ? cleanDesc : cleanDesc.slice(0, 320) + (isLong ? '…' : '')
                return (
                  <div>
                    <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed whitespace-pre-line">
                      {shown}
                    </p>
                    {isLong && (
                      <button onClick={() => setDescExpanded(!descExpanded)}
                        className="mt-2 text-xs text-primary-600 dark:text-primary-400 hover:underline">
                        {descExpanded ? (isChinese ? '收起' : 'Show less') : (isChinese ? '展开完整路线描述' : 'Show full description')}
                      </button>
                    )}
                  </div>
                )
              })()}
            </div>
          )}

          {/* Status */}
          <div className="flex items-center gap-3 mb-6">
            <span className="text-xs font-medium text-gray-500">{t('knowledge.routeStatus')}:</span>
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400">
              {route.status}
            </span>
          </div>

          {/* AI Analysis */}
          {generating && (
            <div className="mb-6 rounded-xl border border-blue-200 dark:border-blue-800/40 bg-blue-50 dark:bg-blue-900/20 p-4">
              <div className="flex items-center justify-between mb-2 text-sm text-blue-700 dark:text-blue-300">
                <span className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {generateStage === 'analysis' ? (isChinese ? 'AI 文字分析中...' : 'Analyzing...')
                    : generateStage === 'diagram' ? (isChinese ? '架构图生成中...' : 'Generating diagram...')
                    : generateStage === 'roadmap' ? (isChinese ? '科研阶段路线图生成中...' : 'Building roadmap...')
                    : (isChinese ? 'AI 分析中...' : 'AI analyzing...')}
                </span>
                <span className="tabular-nums">{generateProgress > 0 ? `${generateProgress}%` : ''} · 已用时 {generateElapsed}s</span>
              </div>
              <div className="h-1.5 bg-blue-200 dark:bg-blue-800 rounded-full overflow-hidden">
                <div className="h-full bg-blue-500 rounded-full transition-all duration-700"
                  style={{ width: `${Math.max(3, generateProgress)}%` }} />
              </div>
            </div>
          )}

          {/* AI Analysis */}
          {route.ai_analysis && (
            <div className="mb-6">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary-500" />
                  {isChinese ? 'AI 分析结果' : 'AI Analysis Results'}
                </h2>
                <div className="flex items-center gap-2 text-xs">
                  {modelLabels.text && (
                    <span className="px-2 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 font-medium"
                      title="文字分析模型">{modelLabels.text}</span>
                  )}
                  {modelLabels.diagram && (
                    <span className="px-2 py-0.5 rounded bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 font-medium"
                      title="架构图模型">{modelLabels.diagram}</span>
                  )}
                  <button onClick={handleExportDoc}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-900/50 transition-colors font-medium"
                    title={isChinese ? '导出为 Word 文档' : 'Export as Word'}>
                    <FileDown className="w-3.5 h-3.5" />{isChinese ? '导出 Word' : 'Word'}
                  </button>
                  <button onClick={handleExportPdf}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-rose-50 dark:bg-rose-900/30 text-rose-600 dark:text-rose-400 hover:bg-rose-100 dark:hover:bg-rose-900/50 transition-colors font-medium"
                    title={isChinese ? '打印 / 另存为 PDF' : 'Print / save as PDF'}>
                    <Printer className="w-3.5 h-3.5" />{isChinese ? '导出 PDF' : 'PDF'}
                  </button>
                </div>
              </div>
              <AnalysisViz
                analysis={route.ai_analysis}
              />
            </div>
          )}

          {/* Related Knowledge */}
          {unavailableKnowledge > 0 && (
            <p role="status" className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
              {isChinese ? `${unavailableKnowledge} 条关联知识已删除或暂时无法读取；其余材料仍可查看。` : `${unavailableKnowledge} linked entries were deleted or could not be loaded; other entries remain available.`}
              <button onClick={fetchRoute} className="ml-2 underline">{t('common.retry')}</button>
            </p>
          )}
          {knowledgeItems.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
                <BookMarked className="w-4 h-4 text-primary-500" />
                {t('knowledge.routeKnowledge')} ({knowledgeItems.length})
              </h2>
              <div className="space-y-2">
                {knowledgeItems.map((item) => (
                  <div
                    key={item.id}
                    className="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800"
                  >
                    <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">{item.title}</h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      {item.category}
                      {item.tags.length > 0 && ` | ${item.tags.map((t) => `#${t}`).join(' ')}`}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Empty State */}
          {!route.ai_analysis && knowledgeItems.length === 0 && (
            <div className="text-center py-12">
              <Route className="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
              <p className="text-gray-500 dark:text-gray-400">
                {isChinese ? '点击上方按钮生成 AI 分析' : 'Click the button above to generate AI analysis'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

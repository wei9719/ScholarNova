import { useEffect, useState } from 'react'
import { Database, ExternalLink, Globe, Loader2, RefreshCw, Upload } from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import { networkApi, papersApi } from '@/api/client'

type Details = Record<string, { latency_ms?: number; status_code?: number | null; message?: string }>

export default function NetworkConfig() {
  const [libraryUrl, setLibraryUrl] = useState('https://lib.lut.edu.cn/')
  const [campusProxyUrl, setCampusProxyUrl] = useState('')
  const [status, setStatus] = useState<Record<string, boolean | null>>({})
  const [details, setDetails] = useState<Details>({})
  const [detecting, setDetecting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [rankingStatus, setRankingStatus] = useState<any>(null)
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    networkApi.getConfig().then(({ data }) => {
      setLibraryUrl(data.library_url || 'https://lib.lut.edu.cn/')
      setCampusProxyUrl(data.campus_proxy_url || '')
      setStatus(data.results || {})
      setDetails(data.details || {})
    }).catch(() => undefined)
    papersApi.rankingStatus().then(({ data }) => setRankingStatus(data)).catch(() => undefined)
  }, [])

  const handleDetect = async () => {
    setDetecting(true)
    try {
      const { data } = await networkApi.detect()
      setStatus(data.results)
      setDetails(data.details || {})
      toast.success('网络与数据源检测完成')
    } catch { toast.error('网络检测失败') }
    finally { setDetecting(false) }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await networkApi.saveConfig({
        library_url: libraryUrl,
        campus_proxy_url: campusProxyUrl,
      })
      toast.success('图书馆与校园代理配置已保存')
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '保存失败')
    } finally { setSaving(false) }
  }

  const handleImport = async (file?: File) => {
    if (!file) return
    setImporting(true)
    try {
      const content = await file.text()
      const { data } = await papersApi.importRankings(file.name, content)
      setRankingStatus(data)
      toast.success(`已导入 ${data.entry_count} 条期刊分区与指标记录`)
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '期刊分区与指标文件导入失败')
    } finally { setImporting(false) }
  }

  const sourceRows = [
    { key: 'crossref', label: 'Crossref REST API', free: true },
    { key: 'semantic_scholar', label: 'Semantic Scholar Graph API', free: true },
    { key: 'openalex', label: 'OpenAlex Works / Sources API', free: true },
    { key: 'google_scholar', label: 'Google Scholar 网页', free: false },
    { key: 'library', label: '兰州理工大学图书馆入口', free: false },
    { key: 'campus_proxy', label: 'HTTP(S) 校园代理', free: false },
  ]

  return (
    <div className="space-y-4">
      <div className="bg-gray-50 dark:bg-gray-800/50 rounded-xl p-4 border border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-2">
            <Globe className="w-4 h-4 text-primary-500" />数据源连通性
          </h4>
          <button onClick={handleDetect} disabled={detecting}
            className="text-xs text-primary-600 dark:text-primary-400 flex items-center gap-1 disabled:opacity-50">
            {detecting ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            重新检测
          </button>
        </div>
        <div className="grid sm:grid-cols-2 gap-2">
          {sourceRows.map(({ key, label, free }) => {
            const ok = status[key]
            return (
              <div key={key} className="flex items-center gap-2 text-xs p-2 rounded-lg bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-700">
                <span className={clsx('w-2.5 h-2.5 rounded-full', ok === true ? 'bg-green-500' : ok === false ? 'bg-red-500' : 'bg-gray-300')} />
                <span className="min-w-0 truncate text-gray-600 dark:text-gray-400" title={label}>{label}</span>
                <span className="ml-auto text-[10px] text-gray-400 whitespace-nowrap">
                  {details[key]?.latency_ms != null ? `${details[key].latency_ms}ms` : free ? '开放 API' : '—'}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <div className="bg-blue-50 dark:bg-blue-900/20 rounded-xl p-4 border border-blue-200 dark:border-blue-800/30">
        <h4 className="text-sm font-semibold text-blue-700 dark:text-blue-300 mb-2">🏫 学校图书馆与校园代理</h4>
        <p className="text-xs leading-5 text-blue-700/80 dark:text-gray-400 mb-3">
          图书馆网页不是代理服务器。ScholarNova 会复制当前检索词并打开馆藏入口；订阅全文仍需校园网、学校 VPN 或统一身份认证，系统不会绕过授权。若学校提供真正的 HTTP(S) 代理，可单独填写。
        </p>
        <label className="block text-xs text-gray-500 mb-1">图书馆门户</label>
        <input value={libraryUrl} onChange={(e) => setLibraryUrl(e.target.value)}
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 outline-none focus:ring-2 focus:ring-primary-500" />
        <label className="block text-xs text-gray-500 mt-3 mb-1">校园 HTTP(S) 代理（可选，不是图书馆网址）</label>
        <input value={campusProxyUrl} onChange={(e) => setCampusProxyUrl(e.target.value)}
          placeholder="例如 http://proxy.example.edu:8080"
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 outline-none focus:ring-2 focus:ring-primary-500" />
        <div className="flex flex-wrap gap-2 mt-3">
          <a href={libraryUrl} target="_blank" rel="noopener noreferrer"
            className="px-3 py-2 text-sm border border-blue-200 dark:border-blue-700 rounded-lg flex items-center gap-1 hover:bg-blue-100/60">
            <ExternalLink className="w-3.5 h-3.5" />访问图书馆
          </a>
          <button onClick={handleSave} disabled={saving}
            className="px-4 py-2 text-sm font-medium bg-primary-600 text-white rounded-lg disabled:opacity-50">
            {saving ? '保存中...' : '保存配置'}
          </button>
        </div>
      </div>

      <div className="bg-amber-50 dark:bg-amber-900/15 rounded-xl p-4 border border-amber-200 dark:border-amber-800/30">
        <h4 className="text-sm font-semibold text-amber-800 dark:text-amber-300 flex items-center gap-2">
          <Database className="w-4 h-4" />期刊分区、影响因子与开放指标
        </h4>
        <p className="mt-2 text-xs leading-5 text-amber-800/80 dark:text-gray-400">
          搜索结果会异步补充 OpenAlex H-index、两年平均被引和 DOAJ 状态。OpenAlex 两年平均被引不等于 JIF 影响因子，也不代表 JCR 或中科院分区。可导入合法获得的 CSV/JSON，支持 JCR、历史中科院、SJR 分区，以及 JIF、JIF 年份和来源；影响因子须同时保留报告年份与数据来源，不同指标不能混用。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="inline-flex cursor-pointer items-center gap-1.5 px-3 py-2 rounded-lg bg-amber-500 text-white text-sm font-medium hover:bg-amber-600">
            {importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            导入分区 / JIF CSV/JSON
            <input type="file" accept=".csv,.json,text/csv,application/json" className="hidden"
              disabled={importing} onChange={(event) => handleImport(event.target.files?.[0])} />
          </label>
          <span className="text-xs text-amber-700 dark:text-amber-400">
            当前 {rankingStatus?.entry_count || 0} 条
            {rankingStatus?.filename ? ` · ${rankingStatus.filename}` : ''}
          </span>
        </div>
      </div>
    </div>
  )
}

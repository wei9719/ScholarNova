import { useEffect, useState } from 'react'
import { ExternalLink, Loader2, RefreshCw } from 'lucide-react'
import { recommendationsApi } from '@/api/client'
import type { Paper, SimilarPaperMode, SimilarPapersResponse } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import { safeErrorMessage } from '@/utils/safeError'
import { getAbstractText } from '@/utils/abstract'

const modes: Array<{ value: SimilarPaperMode; zh: string; en: string }> = [
  { value: 'topic', zh: '研究内容相近', en: 'Related research' },
  { value: 'structure', zh: '摘要结构参考', en: 'Abstract structure' },
  { value: 'journal', zh: '同刊相似论文', en: 'Same journal' },
]

function externalUrl(paper: Paper) {
  const url = paper.url || (paper.doi ? `https://doi.org/${paper.doi}` : '')
  return /^https?:\/\//i.test(url) ? url : null
}

export default function SimilarPapers({ paperId, onSelect }: {
  paperId: string
  onSelect?: (paper: Paper) => void
}) {
  const isZh = useLocaleStore().locale === 'zh'
  const [mode, setMode] = useState<SimilarPaperMode>('topic')
  const [revision, setRevision] = useState(0)
  const [result, setResult] = useState<SimilarPapersResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setResult(null)
    setError('')
    setLoading(true)
    setElapsed(0)
    const start = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 500)
    recommendationsApi.similar(paperId, mode, controller.signal)
      .then(({ data }) => { if (active) setResult(data) })
      .catch((err: unknown) => {
        if (active) setError(safeErrorMessage(err, isZh ? '推荐暂未完成，请稍后重试。' : 'Recommendations unavailable. Please retry.'))
      })
      .finally(() => { if (active) setLoading(false); window.clearInterval(timer) })
    return () => { active = false; controller.abort(); window.clearInterval(timer) }
  }, [paperId, mode, revision, isZh])

  return (
    <section className="space-y-3" aria-label={isZh ? '相似论文推荐' : 'Similar papers'}>
      <label className="block text-sm font-medium">
        {isZh ? '你想参考什么？' : 'What would you like to explore?'}
        <select className="mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-2"
          value={mode} onChange={event => setMode(event.target.value as SimilarPaperMode)}>
          {modes.map(item => <option key={item.value} value={item.value}>{isZh ? item.zh : item.en}</option>)}
        </select>
      </label>
      <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
        {mode === 'structure'
          ? (isZh ? '根据原始摘要中可识别的背景、方法、结果等结构线索推荐，展开摘要即可对照写法；不代表全文结构相同。' : 'Compare observable background, methods and results cues in original abstracts, not full-paper structure.')
          : mode === 'journal'
            ? (isZh ? '在本次检索候选中筛选刊名一致且主题相关的文章；样本不代表整本期刊。' : 'Related candidates with the same journal name; this sample does not represent the entire journal.')
            : (isZh ? '围绕当前论文的问题、方法及模型术语查找更多真实论文，展示推荐依据。' : 'Find real papers sharing research problems, methods or model terms, with matching reasons.')}
      </p>
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-primary-600">
        <Loader2 className="w-4 h-4 animate-spin" />{isZh ? `正在排队或检索推荐 · ${elapsed}s` : `Queued or finding papers · ${elapsed}s`}
      </p>}
      {error && <p role="alert" className="text-sm text-amber-700 dark:text-amber-400">{error}</p>}
      {result && <>
        <div className="rounded-lg bg-gray-50 dark:bg-gray-900 p-3 text-xs text-gray-500 dark:text-gray-400 space-y-2">
          <p>{isZh
            ? `本次获取 ${result.statistics.candidate_count} 篇去重候选，匹配 ${result.statistics.matched_count} 篇，展示 ${result.items.length} 篇。`
            : `${result.statistics.candidate_count} unique candidates, ${result.statistics.matched_count} matches, ${result.items.length} shown.`}</p>
          <ul className="space-y-1">{result.source_statuses.map(source => <li key={source.source}>
            {source.source === 'openalex' ? 'OpenAlex Works API' : source.source === 'crossref' ? 'Crossref REST API' : source.source}
            {' · '}{source.success ? (isZh ? `${source.paper_count} 篇` : `${source.paper_count} papers`) : (isZh ? '未完成' : 'Unavailable')}
            {' · '}{(source.elapsed_ms / 1000).toFixed(1)}s
          </li>)}</ul>
          {result.statistics.terms.length > 0 && <div>
            <p>{isZh
              ? `术语出现篇数（${result.statistics.term_sample_scope === 'same_venue' ? '同刊' : '本轮'}候选样本 ${result.statistics.term_sample_count} 篇，按标题与摘要统计，非全文词频或全刊占比）`
              : `Term occurrences in ${result.statistics.term_sample_count} ${result.statistics.term_sample_scope === 'same_venue' ? 'same-journal' : 'retrieved'} candidates; titles and abstracts only, not full-text or journal-wide frequency.`}</p>
            <div className="flex flex-wrap gap-2 mt-2">{result.statistics.terms.map(term => <span key={term.term}
              className="rounded border border-gray-200 dark:border-gray-700 px-2 py-1">{term.term} · {term.count}/{result.statistics.term_sample_count}</span>)}</div>
          </div>}
        </div>
        {result.warnings.map((warning, index) => <p key={index} className="text-xs text-amber-700 dark:text-amber-400">{warning}</p>)}
        {result.items.length === 0 && <p className="py-4 text-sm text-gray-500">{isZh ? '本次未找到满足条件的论文，可以换一种参考方式或稍后重试。' : 'No papers met these criteria. Try another mode or retry later.'}</p>}
        {result.items.map(item => <article key={item.paper.id} className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 space-y-2">
          <h4 className="text-sm font-semibold leading-relaxed">{item.paper.title}</h4>
          <p className="text-xs text-gray-500">{[item.paper.year, item.paper.venue, item.paper.source].filter(Boolean).join(' · ')}</p>
          <p className="text-xs text-primary-700 dark:text-primary-300">{item.reason}</p>
          {item.matched_terms.length > 0 && <p className="text-xs text-gray-500">{isZh ? '共同术语：' : 'Shared terms: '}{item.matched_terms.join(' · ')}</p>}
          {item.structure_labels.length > 0 && <p className="text-xs text-gray-500">{isZh ? '摘要结构线索：' : 'Abstract cues: '}{item.structure_labels.join(' / ')}</p>}
          {getAbstractText(item.paper.abstract) && <details className="text-xs leading-relaxed">
            <summary className="cursor-pointer text-primary-600">{isZh ? '展开原始摘要，对照阅读' : 'Read original abstract'}</summary>
            <p className="mt-2 whitespace-pre-line text-gray-600 dark:text-gray-300">{getAbstractText(item.paper.abstract)}</p>
          </details>}
          <div className="flex flex-wrap gap-3 text-xs">
            {onSelect && <button type="button" onClick={() => onSelect(item.paper)} className="text-primary-600 hover:underline">{isZh ? '在本页查看与分析' : 'View and analyze here'}</button>}
            {externalUrl(item.paper) && <a href={externalUrl(item.paper)!} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary-600 hover:underline">
              <ExternalLink className="w-3 h-3" />{isZh ? '查看原文' : 'View source'}
            </a>}
          </div>
        </article>)}
      </>}
      {!loading && <button type="button" onClick={() => setRevision(value => value + 1)} className="inline-flex items-center gap-1 text-xs text-primary-600 hover:underline">
        <RefreshCw className="w-3 h-3" />{isZh ? '重新检索推荐' : 'Refresh recommendations'}
      </button>}
    </section>
  )
}

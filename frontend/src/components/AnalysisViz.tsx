/**
 * AI 分析结果可视化组件
 * Provider-neutral text analysis + generic SVG architecture renderer
 *
 * 架构图渲染原则（泛化设计）：
 * - 任何来源的架构文字（## 研究架构图 / 3.研究架构图(文字描述) /
 *   "研究架构图（文字描述）" + 代码块 / 纯分层文本）都会先解析为
 *   通用「层 → 模块」结构，再交给同一个 SVG 布局引擎渲染。
 * - 不依赖 AI 生图模型：每次都是确定性代码生成，结果稳定、可排版。
 */
import { useState, useEffect } from 'react'
import { Clock, Sparkles, FlaskConical, ChevronDown } from 'lucide-react'
import clsx from 'clsx'
import { useLocaleStore } from '@/stores/localeStore'

interface AnalysisVizProps {
  analysis: string
  loading?: boolean
  estimatedTime?: number
  architectureJson?: ArchitectureJson | null
}

interface ArchLayer {
  title: string
  modules: string[]
  formula?: string
}

interface ArchitectureJson {
  title?: string
  layers?: { name: string; modules?: { name: string; desc?: string }[]; formula?: string }[]
}

/**
 * 连续同名层合并：AI 常把同一层拆成多步（如 决策生成 7/8/9/10），
 * 渲染前合并成一层，避免出现大量重复空层与空白。
 */
function mergeConsecutiveDupLayers(layers: ArchLayer[]): ArchLayer[] {
  const out: ArchLayer[] = []
  for (const l of layers) {
    const last = out[out.length - 1]
    if (last && last.title === l.title) {
      last.modules.push(...l.modules)
      if (l.formula && !last.formula) last.formula = l.formula
    } else {
      out.push({ ...l, modules: [...l.modules] })
    }
  }
  return out
}

/**
 * 把 AI 评判提炼的统一架构 JSON 归一化成渲染结构；无效返回 null。
 * 这是"AI 中间评判一次 → 确定性 SVG 最终决定"流程的输入。
 */
export function normalizeJsonLayers(json: ArchitectureJson | null | undefined): { title: string; layers: ArchLayer[] } | null {
  if (!json || !Array.isArray(json.layers) || json.layers.length === 0) return null
  const layers: ArchLayer[] = []
  for (const item of json.layers) {
    if (!item || typeof item.name !== 'string' || !item.name.trim()) continue
    const modules = Array.isArray(item.modules)
      ? item.modules.map((m) => (m && typeof m.name === 'string' ? m.name.trim() : '')).filter(Boolean)
      : []
    const formula = typeof item.formula === 'string' ? item.formula.trim() : ''
    if (modules.length === 0 && !formula) continue
    layers.push({ title: item.name.trim(), modules, formula })
  }
  if (layers.length === 0) return null
  return { title: typeof json.title === 'string' ? json.title.trim() : '', layers }
}

function renderMarkdownText(text: string) {
  // 先清理 markdown 标记
  const lines = text
    .replace(/#{4}\s*/g, '')  // ####
    .replace(/#{3}\s*/g, '')  // ###
    .replace(/#{2}\s*/g, '')  // ##
    .replace(/#{1}\s*/g, '')  // #
    .split('\n')

  return lines.map((line, i) => {
    const trimmed = line.trim()
    if (!trimmed) return null

    // 标题行（原 ## 开头的）
    const isHeading = /^[一-龥].*[:：]$/.test(trimmed) ||
      /^[一-龥].*任务|^[一-龥].*目标|^[一-龥].*模块|^[一-龥].*阶段/.test(trimmed)

    // 清理 ** 加粗标记
    const cleanLine = trimmed.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1')

    if (isHeading) {
      return (
        <h5 key={i} className="text-sm font-bold text-primary-700 dark:text-primary-300 mt-3 mb-1 border-l-2 border-primary-400 pl-2">
          {cleanLine}
        </h5>
      )
    }

    // 编号列表
    if (/^\d+[\.\)、]/.test(cleanLine)) {
      return (
        <p key={i} className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed mb-1 pl-4">
          {cleanLine}
        </p>
      )
    }

    // 列表项
    if (/^[-•]\s/.test(cleanLine)) {
      return (
        <p key={i} className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed mb-0.5 pl-6 flex items-start gap-1">
          <span className="text-primary-400 mt-0.5 flex-shrink-0">-</span>
          {cleanLine.replace(/^[-•]\s*/, '')}
        </p>
      )
    }

    // 普通段落
    return (
      <p key={i} className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed mb-1">
        {cleanLine}
      </p>
    )
  }).filter(Boolean)
}

/**
 * 通用架构解析器：把任意格式的架构文字转成「层 → 模块」结构。
 * 泛化启发式（不针对具体文字）：
 *   1. 清理 markdown 符号（代码围栏 / 标题 # / 列表符号 / 编号 / 加粗 / 反引号）
 *   2. 表格容错：跳过纯框线行（┌┐└┘├┤┬┴┼─ 等）；含竖线（│|）的行按单元格拆分，
 *      每个单元格成为模块（单元格本身是层词时建立新层）
 *   3. 行类型分流：列表项（- / * / + 开头）→ 模块；markdown 标题（###）→ 层；
 *      顶格纯文本 → 含强层词或以冒号结尾 → 层，否则模块
 */
const BOX_CHARS = /[┌┐└┘├┤┬┴┼─═║╔╗╚╝╠╣╦╩╬]/g

function parseLayers(text: string): ArchLayer[] {
  const lines = text.split('\n').filter((l) => l.trim().length > 0)
  const layers: ArchLayer[] = []
  let current: ArchLayer | null = null

  const isLayerTitle = (clean: string): boolean => {
    if (clean.includes('|') || clean.includes('=')) return false
    if (/(层|阶段|模块组|环节|编码器|解码器|主干|Encoder|Decoder|Layer|Stage|Phase|Block|Pipeline|Controller|Policy|Critic|Reward|Optimizer)/i.test(clean)) return true
    if (/[:：]$/.test(clean)) return true
    if (clean.length <= 18 && !clean.includes(':') && !clean.includes('、') && !clean.includes(',') &&
        /^(输入|输出|特征|表示|决策|应用|优化|训练|推理|融合|多模态|模型|编码|解码|系统|架构|整体|端到端|数据流|信息流|动作|奖励|策略|环境|经验回放|轨迹)/.test(clean)) return true
    return false
  }
  const modName = (clean: string): string =>
    clean.replace(/^(.{2,28}?)[:：].*$/, '$1').trim()
  // 数学特征行（含 = → 希腊字母 / argmax 等）→ 作为当前层的公式，而不是模块
  const isFormulaLine = (s: string): boolean =>
    /[=→Σ∏θαβγδλμωΩ∫∂∈]|argmax|argmin|concat|Attention\(|softmax|normalize|Loss|encode\(|decoder\(/.test(s)
  const openLayer = (clean: string): ArchLayer => {
    const layer: ArchLayer = { title: clean.replace(/[:：]\s*$/, ''), modules: [], formula: '' }
    layers.push(layer)
    return layer
  }
  const addFormula = (clean: string) => {
    if (!current) current = openLayer('架构模块')
    current.formula = current.formula ? `${current.formula}; ${clean}` : clean
  }

  for (const line of lines) {
    const stripped = line.trim()
    if (/^```+/.test(stripped)) continue
    // markdown 图片 / 链接（独立一行）跳过，绝不当作模块（否则会显示 ![..](..) 乱码）
    if (/^!\[[^\]]*\]\([^)]*\)\s*$/.test(stripped)) continue
    if (/^\[[^\]]*\]\([^)]*\)\s*$/.test(stripped)) continue
    // 表格纯框线行（无正文）跳过
    if (/^[\s┌┐└┘├┤┬┴┼─═║╔╗╚╝╠╣╦╩╬|=\-]+$/.test(stripped)) continue
    // 含竖线/框线的行：按单元格拆分
    if (stripped.includes('│') || stripped.includes('|')) {
      const cells = stripped
        .split(/[│|]/)
        .map((cell) => cell.replace(BOX_CHARS, '').replace(/^#{1,6}\s*/, '').replace(/^[-*+•]\s+/, '').trim())
        .filter(Boolean)
      if (cells.length === 0) continue
      for (const cell of cells) {
        if (isFormulaLine(cell)) {
          addFormula(cell)
        } else if (isLayerTitle(cell)) {
          current = openLayer(cell)
        } else if (current) {
          current.modules.push(modName(cell))
        } else {
          current = openLayer('架构模块')
          current.modules.push(modName(cell))
        }
      }
      continue
    }
    const isList = /^[-*+•]\s+/.test(stripped)
    const isHeading = /^#{1,6}\s+/.test(stripped)
    const clean = stripped
      .replace(/^#{1,6}\s*/g, '')
      .replace(/^[-*+•]\s+/g, '')
      .replace(/^\d+[.、)．:：]\s*/g, '')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/\*([^*]+)\*/g, '$1')
      .replace(/`/g, '')
      .trim()
    if (!clean) continue

    if (isFormulaLine(clean)) {
      addFormula(clean)
    } else if (isList) {
      if (current) current.modules.push(modName(clean))
    } else if (isHeading || isLayerTitle(clean)) {
      current = openLayer(clean)
    } else if (current) {
      current.modules.push(modName(clean))
    }
  }
  return layers.filter((l) => l.title || l.modules.length > 0)
}

/**
 * 通用 SVG 架构图引擎
 * 设计：层 = 渐变泳道（左侧色条 + 左上角层标题 + 右上角序号）；
 *       模块 = 白色圆角卡片（层主色描边 + 微阴影），行内居中排布；
 *       层间 = 虚线箭头流线。每层配色取自 7 色学术色板，自动循环。
 */
export function ArchitectureSvg({ title, layers, fitContainer = false }: { title: string; layers: ArchLayer[]; fitContainer?: boolean }) {
  // ---- 布局参数 ----
  const width = 840
  const modW = 176
  const modH = 38
  const gap = 14
  const padX = 20
  const padTop = 42
  const padBottom = 14
  const arrowH = 30
  const formulaH = 32
  const titleH = title ? 46 : 0
  const cols = Math.max(1, Math.floor((width - padX * 2 + gap) / (modW + gap)))
  const rowH = modH + 10

  const palette = [
    { main: '#2563eb', bg: '#eff6ff', border: '#bfdbfe', text: '#1e40af' },
    { main: '#0891b2', bg: '#ecfeff', border: '#a5f3fc', text: '#155e75' },
    { main: '#059669', bg: '#ecfdf5', border: '#a7f3d0', text: '#065f46' },
    { main: '#d97706', bg: '#fffbeb', border: '#fde68a', text: '#92400e' },
    { main: '#7c3aed', bg: '#f5f3ff', border: '#ddd6fe', text: '#5b21b6' },
    { main: '#db2777', bg: '#fdf2f8', border: '#fbcfe8', text: '#9d174d' },
    { main: '#475569', bg: '#f8fafc', border: '#cbd5e1', text: '#334155' },
  ]

  // 空模块层压缩高度（0 行），避免大面积空白
  const rowCounts = layers.map((l) => (l.modules.length > 0 ? Math.ceil(Math.min(l.modules.length, 16) / cols) : 0))
  const layerHs = rowCounts.map((rows, i) => padTop + rows * rowH + (layers[i].formula ? formulaH : 0) + padBottom)
  const totalH = titleH + layerHs.reduce((a, b) => a + b, 0) + arrowH * (layers.length - 1) + 16

  let y = titleH + 8
  const els: React.ReactNode[] = []

  if (title) {
    els.push(
      <g key="title">
        <text x={width / 2} y={30} fontSize={17} fontWeight={700} fill="#1f2937" textAnchor="middle">
          {title}
        </text>
        <line x1={width / 2 - 42} y1={38} x2={width / 2 + 42} y2={38} stroke="#e5e7eb" strokeWidth={2} strokeLinecap="round" />
      </g>,
    )
  }

  layers.forEach((layer, li) => {
    const pal = palette[li % palette.length]
    const layerH = layerHs[li]
    const gid = `arch-lg-${li}`
    const n = Math.min(layer.modules.length, 16)

    els.push(
      <defs key={`defs-${li}`}>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={pal.bg} />
          <stop offset="100%" stopColor={pal.bg} stopOpacity={0.5} />
        </linearGradient>
      </defs>,
      <rect key={`bg-${li}`} x={8} y={y} width={width - 16} height={layerH} rx={14}
        fill={`url(#${gid})`} stroke={pal.border} strokeWidth={1.2} />,
      <rect key={`bar-${li}`} x={8} y={y + 14} width={5} height={layerH - 28} rx={2.5} fill={pal.main} />,
      <text key={`lt-${li}`} x={26} y={y + 26} fontSize={14.5} fontWeight={700} fill={pal.text}>
        {layer.title.length > 30 ? layer.title.slice(0, 29) + '…' : layer.title}
      </text>,
      <text key={`li-${li}`} x={width - 30} y={y + 26} fontSize={11} fill="#9ca3af" textAnchor="end" fontWeight={500}>
        {li + 1} / {layers.length}
      </text>,
    )

    layer.modules.slice(0, 16).forEach((mod, mi) => {
      const row = Math.floor(mi / cols)
      const rowStart = row * cols
      const inRow = Math.min(cols, n - rowStart)
      const rowTotalW = inRow * modW + (inRow - 1) * gap
      const startX = padX + (width - padX * 2 - rowTotalW) / 2
      const col = mi - rowStart
      const mx = startX + col * (modW + gap)
      const my = y + padTop + row * rowH
      const label = mod.length > 20 ? mod.slice(0, 19) + '…' : mod
      els.push(
        <g key={`m-${li}-${mi}`}>
          <rect x={mx} y={my} width={modW} height={modH} rx={9}
            fill="#ffffff" stroke={pal.border} strokeWidth={1.4}
            filter="url(#arch-shadow)" />
          <rect x={mx} y={my} width={4} height={modH} rx={2} fill={pal.main} opacity={0.55} />
          <text x={mx + modW / 2 + 4} y={my + modH / 2 + 4.5}
            fontSize={12} fill="#374151" textAnchor="middle" fontWeight={500}>
            {label}
          </text>
        </g>,
      )
    })

    // 公式条（虚线边框，等宽字体）
    if (layer.formula) {
      const fy = y + layerH - formulaH - 8
      const ftext = layer.formula.length > 66 ? layer.formula.slice(0, 65) + '…' : layer.formula
      els.push(
        <g key={`f-${li}`}>
          <rect x={padX} y={fy} width={width - padX * 2} height={formulaH - 8} rx={7}
            fill="#ffffff" stroke={pal.border} strokeWidth={1.2} strokeDasharray="4 3" />
          <text x={padX + 14} y={fy + 18} fontSize={12.5} fill={pal.text}
            fontFamily="'Courier New', Consolas, monospace" fontWeight={600}>
            {ftext}
          </text>
        </g>,
      )
    }

    if (li < layers.length - 1) {
      const fromY = y + layerH
      const toY = fromY + arrowH
      const cx = width / 2
      els.push(
        <g key={`a-${li}`}>
          <line x1={cx} y1={fromY} x2={cx} y2={toY - 10} stroke="#94a3b8" strokeWidth={2} strokeDasharray="4 3" />
          <path d={`M ${cx - 7} ${toY - 17} L ${cx} ${toY - 3} L ${cx + 7} ${toY - 17}`} fill="none" stroke="#94a3b8" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
        </g>,
      )
    }
    y += layerH + arrowH
  })

  return (
    <div className="overflow-x-auto rounded-lg">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox={`0 0 ${width} ${totalH}`}
        style={{ minWidth: fitContainer ? undefined : 720, maxWidth: '100%', width: '100%', height: 'auto' }} role="img"
        aria-label="研究架构图" className="w-full h-auto">
        <defs>
          <filter id="arch-shadow" x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="1.5" stdDeviation="2" floodColor="#00000018" />
          </filter>
        </defs>
        {els}
      </svg>
    </div>
  )
}

function WaitTimer({ seconds }: { seconds: number }) {
  const [remaining, setRemaining] = useState(seconds)
  const [step, setStep] = useState(0)
  const steps = ['AI 文字分析...', '架构图生成...', '渲染结果...']

  useEffect(() => {
    const timer = setInterval(() => {
      setRemaining(r => r <= 1 ? (clearInterval(timer), 0) : r - 1)
      setStep(s => Math.min(s + 1, 2))
    }, (seconds / 3) * 1000)
    return () => clearInterval(timer)
  }, [seconds])

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800/30">
        <Clock className="w-4 h-4 text-blue-500 animate-pulse" />
        <span className="text-sm text-blue-700 dark:text-blue-300">
          {remaining > 0 ? `预计还需 ${remaining} 秒...` : '即将完成...'}
        </span>
        <div className="flex-1 h-1.5 bg-blue-200 dark:bg-blue-800 rounded-full overflow-hidden ml-2">
          <div className="h-full bg-blue-500 rounded-full transition-all duration-1000"
            style={{ width: `${Math.max(0, (remaining / seconds) * 100)}%` }} />
        </div>
      </div>
      <div className="flex gap-2 text-xs">
        {steps.map((s, i) => (
          <span key={i} className={clsx('px-2 py-1 rounded-full transition-colors',
            i <= step ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400' : 'bg-gray-100 dark:bg-gray-800 text-gray-400')}>
            {i < step ? '✓' : i === step ? '⏳' : '○'} {s}
          </span>
        ))}
      </div>
    </div>
  )
}

/**
 * 架构图数据源决策：优先 AI 评判提炼的统一 JSON（确定性、干净、无空层），
 * 否则回退到文字启发式解析（仍走同一个 SVG 引擎）。
 */
function resolveArchitecture(analysis: string, architectureJson?: ArchitectureJson | null) {
  // 1) 显式传入的 AI 评判 JSON
  const fromJson = normalizeJsonLayers(architectureJson)
  if (fromJson) return fromJson
  // 2) 文本内嵌 <ARCH_JSON> 块（route_pipeline 等后端可写入）
  const emb = analysis.match(/<ARCH_JSON>\s*(\{[\s\S]*?\})\s*<\/ARCH_JSON>/)
  if (emb) {
    try {
      const embedded = normalizeJsonLayers(JSON.parse(emb[1]))
      if (embedded) return embedded
    } catch {}
  }
  // 3) 文字启发式解析（兜底）
  const archIndex = analysis.search(/研究架构图/)
  const diagramPart = archIndex >= 0 ? analysis.slice(archIndex) : ''
  if (!diagramPart.trim()) return null
  let title = ''
  let body = diagramPart
  const titleMatch = diagramPart.match(/^\s*\d*[.、)．]?\s*(研究架构图[^\n]*)/)
  if (titleMatch && titleMatch[1].length <= 32) {
    title = titleMatch[1].trim().replace(/[:：]$/, '')
    body = diagramPart.replace(titleMatch[0], '')
  }
  const cleaned = body.replace(/^```+[a-zA-Z]*\s*$/gm, '').trim()
  let layers = parseLayers(cleaned)
  layers = mergeConsecutiveDupLayers(layers)
  return { title, layers }
}

export default function AnalysisViz({
  analysis, loading = false, estimatedTime = 60, architectureJson = null,
}: AnalysisVizProps) {
  const { locale } = useLocaleStore()
  const isChinese = locale === 'zh'
  const [showFull, setShowFull] = useState(false)

  if (loading) return <WaitTimer seconds={estimatedTime} />

  // 从全文中提取所有图片 URL
  const imageUrls: string[] = []
  const patterns = [
    /!\[.*?\]\(((?:https?:\/\/|\/)[^)]+)\)/g,
    /\[.*?\]\(((?:https?:\/\/|\/)[^\)]+\.(png|jpg|jpeg|webp|gif)[^\)]*)\)/gi,
    /(https?:\/\/[^\s)]+\.(png|jpg|jpeg|webp|gif)[^\s)]*)/gi,
  ]
  for (const p of patterns) {
    let m
    while ((m = p.exec(analysis)) !== null) {
      const url = m[1] || m[0]
      if (url && !imageUrls.includes(url)) imageUrls.push(url)
    }
  }
  const ossRegex = /(https?:\/\/aoss\.cn-sh-01\.sensecoreapi-oss\.cn\/[^\s)\]>"]+)/gi
  let m2
  while ((m2 = ossRegex.exec(analysis)) !== null) {
    if (m2[1] && !imageUrls.includes(m2[1])) imageUrls.push(m2[1])
  }

  // 架构图数据源：AI 评判 JSON 优先，文字解析兜底
  const arch = resolveArchitecture(analysis, architectureJson)
  const hasDiagram = arch !== null

  // 文字分析部分（架构段落之前）
  const archIndex = analysis.search(/研究架构图/)
  const textPart = archIndex >= 0 ? analysis.slice(0, archIndex) : analysis

  return (
    <div className="space-y-4">
      {/* 文字分析 */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
        <h3 className="text-sm font-bold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-primary-500" />
          {isChinese ? 'AI 文字分析' : 'AI Text Analysis'}
        </h3>
        <div className="relative">
          <div className="absolute left-3 top-0 bottom-0 w-0.5 bg-gradient-to-b from-primary-500 to-primary-300" />
          <div className="pl-8">
            {renderMarkdownText(showFull ? textPart : textPart.slice(0, 2000))}
            {textPart.length > 2000 && (
              <button onClick={() => setShowFull(!showFull)}
                className="mt-2 text-xs text-primary-600 dark:text-primary-400 hover:underline">
                {showFull ? (isChinese ? '收起' : 'Show less') : (isChinese ? '展开全部' : 'Show all')}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 架构图：确定性 SVG 引擎（AI 评判 JSON 优先；文字解析兜底） */}
      {hasDiagram ? (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <h3 className="text-sm font-bold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
            <FlaskConical className="w-4 h-4 text-purple-500" />
            {isChinese ? 'AI 研究架构图' : 'AI Research Architecture'}
            <span className="ml-auto text-[11px] font-normal text-gray-400">
              {isChinese ? 'SVG 渲染' : 'SVG rendered'}
            </span>
          </h3>
          <div className="bg-gray-50 dark:bg-gray-900 rounded-xl p-3">
            <ArchitectureSvg title={arch?.title || ''} layers={arch?.layers || []} />
          </div>
          {imageUrls.length > 0 && (
            <details className="mt-3">
              <summary className="inline-flex items-center gap-1 text-xs text-primary-600 dark:text-primary-400 hover:underline cursor-pointer select-none">
                <ChevronDown className="w-3.5 h-3.5" />
                {isChinese ? 'AI 绘制版大图' : 'AI-drawn version'}
              </summary>
              <div className="mt-2 space-y-3">
                {imageUrls.map((url, i) => (
                  <img key={i} src={url} alt={isChinese ? 'AI 绘制架构图' : 'AI-drawn architecture'}
                    className="w-full rounded-lg border border-gray-200 dark:border-gray-700" />
                ))}
              </div>
            </details>
          )}
        </div>
      ) : imageUrls.length > 0 ? (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <h3 className="text-sm font-bold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
            <FlaskConical className="w-4 h-4 text-purple-500" />
            {isChinese ? 'AI 研究架构图' : 'AI Research Architecture'}
          </h3>
          {imageUrls.map((url, i) => (
            <img key={i} src={url} alt={isChinese ? 'AI 绘制架构图' : 'AI-drawn architecture'}
              className="w-full rounded-lg shadow-md cursor-pointer hover:shadow-lg transition-shadow"
              onClick={() => window.open(url, '_blank')} />
          ))}
        </div>
      ) : null}
    </div>
  )
}

import type { ResearchRoute } from '@/api/types'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { ArchitectureSvg, normalizeJsonLayers } from '@/components/AnalysisViz'

const ARCHITECTURE_BLOCK = /<ARCH_JSON\b[^>]*>[\s\S]*?(?:<\/ARCH_JSON>|$)/gi

export function cleanRouteMarkdown(text: string): string {
  return (text || '')
    .replace(ARCHITECTURE_BLOCK, '')
    .replace(/#{1,6}\s*/g, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/^\s*[-•]\s*/gm, '• ')
    .replace(/\|/g, ' | ')
    .replace(/\s+/g, ' ')
    .trim()
}

function escapeHtml(text: string): string {
  const entities: Record<string, string> = {
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }
  return text.replace(/[&<>"']/g, (character) => entities[character])
}

function paragraphs(text: string): string {
  return text.split(/\r?\n/).map(cleanRouteMarkdown).filter(Boolean)
    .map((line) => `<p style="margin:6px 0;line-height:1.8;">${escapeHtml(line)}</p>`).join('')
}

function imageUrl(raw: string, origin: string): string | null {
  // Only explicit web URLs and root-relative app resources are exportable.
  if (!/^(?:https?:\/\/|\/(?![\\/]))/i.test(raw)) return null
  try {
    const url = new URL(raw, origin)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

type ExportRoute = Pick<ResearchRoute, 'title' | 'status' | 'description' | 'ai_analysis'> & {
  architecture_json?: Parameters<typeof normalizeJsonLayers>[0]
}

function architectureMarkup(route: ExportRoute): string {
  let architecture = normalizeJsonLayers(route.architecture_json)
  if (!architecture) {
    for (const match of (route.ai_analysis || '').matchAll(/<ARCH_JSON>\s*([\s\S]*?)\s*<\/ARCH_JSON>/gi)) {
      // Only parse bounded JSON data; never accept supplied SVG/HTML markup.
      if (match[1].length > 100_000) continue
      try {
        architecture = normalizeJsonLayers(JSON.parse(match[1]))
        if (architecture) break
      } catch { /* Invalid data falls back to the readable route text. */ }
    }
  }
  if (!architecture) return ''
  return `<section style="margin:20px 0;page-break-inside:avoid;">
    <h2 style="font-size:18px;margin-bottom:8px;">研究结构图</h2>
    <p style="color:#6b7280;font-size:12px;">依据架构数据确定性 SVG 绘制，非图像模型生成；结构规划仍需结合原文核验。</p>
    ${renderToStaticMarkup(createElement(ArchitectureSvg, { ...architecture, fitContainer: true }))}
  </section>`
}

export function buildRouteDocumentHtml(route: ExportRoute, origin: string): string {
  const architecture = architectureMarkup(route)
  const analysis = (route.ai_analysis || '').replace(ARCHITECTURE_BLOCK, '')
  const textModel = analysis.match(/##\s*文字分析[（(]([^）)]+)[）)]/)?.[1] || ''
  const diagramModel = analysis.match(/##\s*研究架构图[（(]([^）)]+)[）)]/)?.[1] || ''
  const [textRaw, diagramRaw = ''] = analysis.split(/##\s*研究架构图/)
  const textClean = paragraphs(textRaw)
  const diagramText = paragraphs(diagramRaw.replace(/!\[.*?\]\([^)]*\)/g, ''))
  const imgTags = [...analysis.matchAll(/!\[.*?\]\(([^)]*)\)/g)].map((match) => {
    const url = imageUrl(match[1], origin)
    return url ? `<div style="margin:12px 0;text-align:center;"><img src="${escapeHtml(url)}" style="max-width:100%;border:1px solid #ddd;border-radius:8px;" /></div>` : ''
  }).join('')
  const desc = paragraphs(route.description || '')
  const modelLine = [
    textModel ? `<span style="display:inline-block;margin-right:10px;padding:2px 10px;border-radius:999px;background:#eef2ff;color:#4f46e5;font-size:12px;">文字分析：${escapeHtml(textModel)}</span>` : '',
    diagramModel ? `<span style="display:inline-block;padding:2px 10px;border-radius:999px;background:#faf5ff;color:#7c3aed;font-size:12px;">架构图：${escapeHtml(diagramModel)}</span>` : '',
  ].filter(Boolean).join('')
  return `<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8" /><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src http: https:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'" /><title>${escapeHtml(route.title || '研究路线')}</title></head>
<body style="font-family:'PingFang SC','Microsoft YaHei',sans-serif;color:#1a1b1c;max-width:820px;margin:0 auto;padding:32px 24px;">
  <h1 style="font-size:24px;margin-bottom:4px;">${escapeHtml(route.title || '')}</h1>
  <p style="color:#6b7280;font-size:13px;margin-bottom:20px;">状态：${escapeHtml(route.status || '')}${modelLine ? '　' + modelLine : ''}</p>
  <h2 style="font-size:18px;border-left:4px solid #4f46e5;padding-left:10px;margin:24px 0 8px;">路线描述</h2>
  ${desc || '<p style="color:#9ca3af;">无</p>'}
  <h2 style="font-size:18px;border-left:4px solid #4f46e5;padding-left:10px;margin:24px 0 8px;">AI 分析结果</h2>
  ${textClean}
  ${architecture || '<p style="color:#6b7280;font-size:12px;">暂无可用的结构化架构数据，以下保留文字分析与路线说明，不推测补图。</p>'}
  ${imgTags}
  ${diagramText ? `<h2 style="font-size:18px;border-left:4px solid #7c3aed;padding-left:10px;margin:24px 0 8px;">研究架构图描述</h2>${diagramText}` : ''}
  <p style="margin-top:32px;padding-top:12px;border-top:1px solid #eee;color:#9ca3af;font-size:12px;">由 ScholarNova 生成 · 模型与检索依据见上</p>
</body>
</html>`
}

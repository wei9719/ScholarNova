// Strip known metadata tags before a single, inert entity decode. Never parse
// plain or decoded text as HTML: comparisons such as x<y are abstract content.
export function getAbstractText(abstract: string | null | undefined): string {
  if (!abstract) return ''
  const content = abstract
    .replace(/<(script|style)(?:\s+[^<>]*?)?\s*>[\s\S]*?<\/\1\s*>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<\/?(?:[\w-]+:)?(?:abstract|sec|title|p|div|section|article|h[1-6]|blockquote|ul|ol|li|br|hr)(?:\s+[\w:.-]+\s*=[^<>]*?)?\s*\/?>/gi, '\n')
    .replace(/<\/?(?:[\w-]+:)?(?:italic|bold|em|strong|b|i|u|span|sup|sub|a|xref|ext-link|img)(?:\s+[\w:.-]+\s*=[^<>]*?)?\s*\/?>/gi, '')
  const decoder = document.createElement('textarea')
  decoder.innerHTML = content
  const text = decoder.value
    .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
    .replace(/[^\S\n]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n').trim()
  const placeholder = /^(?:no abstract(?: (?:available|provided))?|abstract (?:unavailable|not available)|not available|n\/?a|none|null|暂无摘要|无摘要|未提供摘要|摘要不可用|abstract|摘要)[.!。！:：]?$/i
  return placeholder.test(text) ? '' : text
}

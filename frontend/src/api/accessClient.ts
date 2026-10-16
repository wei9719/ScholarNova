export interface AccessRequest { url: string; channel: 'campus' | 'public'; paper_id?: string }
declare global {
  interface Window {
    scholarAccess?: {
      open: (request: AccessRequest) => Promise<{ opened?: boolean; error?: string }>
      onImported: (callback: (event: { paper_id: string }) => void) => () => void
    }
  }
}

export async function openAcademicAccess(request: AccessRequest) {
  const url = new URL(request.url)
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('学术页面地址无效')
  if (window.scholarAccess) {
    const result = await window.scholarAccess.open(request)
    if (result.error) throw new Error(result.error)
    return 'desktop' as const
  }
  window.open(url.href, '_blank', 'noopener,noreferrer')
  return 'external' as const
}

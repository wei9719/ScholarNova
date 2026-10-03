/** Extract display text without exposing request inputs or mutating API error metadata. */
export function safeErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  const text = (value: unknown) => typeof value === 'string' && value.trim() ? value : undefined
  if (typeof detail === 'string') return text(detail) || fallback
  if (Array.isArray(detail)) {
    return detail.map((item) => text(item?.msg)).filter(Boolean).slice(0, 3).join('；') || fallback
  }
  if (detail && typeof detail === 'object') {
    return text((detail as { message?: unknown }).message) || fallback
  }
  // Raw Error.message can contain URLs, request bodies or provider output.
  return fallback
}

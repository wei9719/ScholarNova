import axios from 'axios'

export type ReadingMode =
  | 'translate'
  | 'explain'
  | 'math'
  | 'figure'
  | 'prerequisite'
  | 'writing'
  | 'extension'
export interface ReadingDocument {
  paper_id: string
  title: string
  available: boolean
  page_count: number
  content_hash: string | null
  pdf_url: string | null
  source: string | null
  warnings: string[]
}
export interface ReadingPage {
  paper_id: string
  page_number: number
  page_count: number
  content_hash: string
  text: string
  text_truncated: boolean
  text_verified: boolean
  image_url: string | null
  coverage: string
  warnings: string[]
}
export interface ReadingSource {
  label: string
  page_number: number
  content_hash: string
  excerpt: string
  paper_id?: string
  paper_title?: string
  venue?: string | null
}
export interface ReadingResponse {
  answer: string
  source_notes: string
  teaching_notes: string
  exercise: string
  sources: ReadingSource[]
  coverage: string
  warnings: string[]
  model_completed: boolean
  provider: string | null
  model: string | null
  total_tokens: number
  fallback_used: boolean
  usage?: Record<string, unknown>
  model_attempts?: Array<Record<string, unknown>>
  source_grounding?: {
    policy: 'verbatim_page_text'
    matched_quotes: number
    rejected_quotes: number
    moved_source_notes: boolean
    detail: string
  } | null
  calculation_checks?: Array<{
    metric: string
    status: 'computed' | 'matched' | 'mismatch' | 'unsupported'
    y_true: number[]
    y_pred: number[]
    mask: number[] | null
    claimed_result: number | null
    computed_result: number | null
    detail: string
  }>
}
export interface ReadingRequest {
  mode: ReadingMode
  page_number: number
  selection: string
  content_hash: string
  question: string
}
export interface ReadingLibrary {
  papers: Array<{
    paper_id: string
    title: string
    venue: string | null
    year: number | null
    source: 'uploaded' | 'oa_cache'
  }>
  limit: number
  warnings: string[]
}

const reader = axios.create({ baseURL: '/api/v1/reader', timeout: 105000 })
export const readerApi = {
  library: (signal?: AbortSignal) =>
    reader.get<ReadingLibrary>('/library', { signal }),
  writingSamples: (
    data: {
      paper_ids: string[]
      question: string
      content_hashes: Record<string, string>
    },
    signal?: AbortSignal,
  ) => reader.post<ReadingResponse>('/samples/writing', data, { signal }),
  document: (paperId: string, signal?: AbortSignal) =>
    reader.get<ReadingDocument>(`/${encodeURIComponent(paperId)}/document`, {
      signal,
    }),
  page: (paperId: string, page: number, hash: string, signal?: AbortSignal) =>
    reader.get<ReadingPage>(`/${encodeURIComponent(paperId)}/pages/${page}`, {
      params: { content_hash: hash },
      signal,
    }),
  assist: (paperId: string, request: ReadingRequest, signal?: AbortSignal) =>
    reader.post<ReadingResponse>(
      `/${encodeURIComponent(paperId)}/assist`,
      request,
      { signal },
    ),
}

// Reader previews only use our PDF/page endpoints, never arbitrary remote frames.
export function readerAssetUrl(
  value: string | null | undefined,
  paperId: string,
): string | null {
  if (!value) return null
  try {
    const url = new URL(value, window.location.origin)
    const prefix = `/api/v1/reader/${encodeURIComponent(paperId)}/`
    return url.origin === window.location.origin &&
      url.pathname.startsWith(prefix) &&
      !url.username &&
      !url.password
      ? `${url.pathname}${url.search}`
      : null
  } catch {
    return null
  }
}

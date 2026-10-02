import axios from 'axios'
import type {
  SearchRequest,
  SearchResponse,
  SearchRunDetail,
  PaperDetail,
  AnalysisRequest,
  AnalysisResult,
  CompareRequest,
  CompareResult,
  RecommendationRequest,
  RecommendationResponse,
  RecommendationFeedback,
  ModelConfig,
  ModelCapabilityReport,
  ModelCapabilityProbeResult,
  ModelProbeTask,
  LLMProvider,
  ModelTestRequest,
  ModelTestResponse,
  HealthResponse,
  EvidenceResponse,
  KnowledgeItem,
  KnowledgeListResponse,
  KnowledgeCreateRequest,
  KnowledgeUpdateRequest,
  ResearchRoute,
  RouteCreateRequest,
  AIAnalyzeResponse,
  RecommendResponse,
  FulltextStatus,
  ZoteroStatus,
  ZoteroCollection,
  ZoteroImportResult,
  AgentChatResponse,
  AgentMessage,
} from './types'

const api = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    // 可以在这里添加认证 token
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => response,
  (error) => {
    // 统一错误处理
    const message = error.response?.data?.detail || 'An error occurred'
    const expectedZoteroDiscoveryFailure =
      error.config?.url === '/integrations/zotero/status' &&
      error.response?.status === 503
    if (!expectedZoteroDiscoveryFailure) {
      console.error('API Error:', message)
    }
    return Promise.reject(error)
  }
)

// =============================================================================
// 搜索 API
// =============================================================================

export const searchApi = {
  create: (request: SearchRequest) =>
    api.post<SearchResponse>('/search', request),

  getRun: (runId: string) =>
    api.get<SearchRunDetail>(`/search/${runId}`),
}

// =============================================================================
// 论文 API
// =============================================================================

export const papersApi = {
  get: (paperId: string) =>
    api.get<PaperDetail>(`/papers/${paperId}`),

  analyze: (paperId: string, request: AnalysisRequest, signal?: AbortSignal) =>
    api.post<AnalysisResult>(`/papers/${paperId}/analyze`, request, { signal }),

  fulltextStatus: (paperId: string) =>
    api.get<FulltextStatus>(`/papers/${paperId}/fulltext/status`),

  uploadFulltext: (paperId: string, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post<FulltextStatus>(`/papers/${paperId}/fulltext`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  compare: (request: CompareRequest) =>
    api.post<CompareResult>('/papers/compare', request),

  getEvidence: (runId: string, paperId: string) =>
    api.get<EvidenceResponse>(`/papers/${runId}/${paperId}/evidence`),

  translate: (text: string, targetLang: string = 'zh') =>
    api.post<{ translated: string; cached: boolean }>('/papers/translate', {
      text,
      target_lang: targetLang,
    }),

  journalQuality: (venue: string, quality?: unknown) =>
    api.post('/papers/journal-quality', { venue, quality }),

  rankingStatus: () => api.get('/papers/journal-rankings/status'),

  importRankings: (filename: string, content: string) =>
    api.post('/papers/journal-rankings/import', { filename, content }),
}

// =============================================================================
// 推荐 API
// =============================================================================

export const recommendationsApi = {
  get: (request: RecommendationRequest) =>
    api.post<RecommendationResponse>('/recommendations', request),

  submitFeedback: (feedback: RecommendationFeedback) =>
    api.post('/recommendations/feedback', feedback),
}

// =============================================================================
// 翻译 API
// =============================================================================

export const translateApi = {
  translate: (text: string, targetLang: string = 'zh') =>
    api.post<{ translated: string; cached?: boolean; provider?: string; model?: string; total_tokens?: number; fallback_used?: boolean }>('/papers/translate', { text, target_lang: targetLang }),
}

// =============================================================================
// 模型配置 API
// =============================================================================

export const modelApi = {
  getConfig: () => api.get<ModelConfig>('/model/config'),

  getCapabilities: (provider: LLMProvider, modelName: string, task?: string) =>
    api.get<ModelCapabilityReport>('/model/capabilities', {
      params: { provider, model_name: modelName, task },
    }),

  getCapabilityProbe: (provider: LLMProvider, modelName: string, task: ModelProbeTask) =>
    api.get<ModelCapabilityProbeResult | null>('/model/capabilities/probe', {
      params: { provider, model_name: modelName, task },
    }),

  probeCapability: (request: ModelTestRequest & { task: ModelProbeTask }) =>
    api.post<ModelCapabilityProbeResult>('/model/capabilities/probe', request),

  saveConfig: (config: ModelConfig) =>
    api.post('/model/config', config),

  testConnection: (request: ModelTestRequest) =>
    api.post<ModelTestResponse>('/model/test', request),

  testEmbedding: (request: NonNullable<ModelConfig['embedding']>) =>
    api.post<ModelTestResponse>('/model/test-embedding', request),
}

// =============================================================================
// 健康检查 API
// =============================================================================

export const healthApi = {
  check: () =>
    api.get<HealthResponse>('/health'),
}

export const networkApi = {
  getConfig: () => api.get('/network/config'),
  saveConfig: (data: {
    library_url?: string
    campus_proxy_url?: string
  }) => api.post('/network/config', data),
  detect: () => api.post('/network/detect'),
  libraryLink: (query: string) =>
    api.get('/network/library-link', { params: { q: query } }),
  testProxy: () => api.get('/network/proxy-test'),
}

export const zoteroApi = {
  status: () => api.get<ZoteroStatus>('/integrations/zotero/status'),
  collections: () =>
    api.get<{ items: ZoteroCollection[]; total: number }>(
      '/integrations/zotero/collections'
    ),
  importItems: (collectionKey: string | null, limit: number = 50) =>
    api.post<ZoteroImportResult>('/integrations/zotero/import', {
      collection_key: collectionKey || null,
      limit,
    }),
  push: (data: {
    title: string
    creators?: { firstName?: string; lastName?: string }[]
    year?: string
    venue?: string
    doi?: string
    url?: string
    abstract?: string
    collection_key?: string
    pdf_path?: string
    api_key?: string
  }) => api.post<{
    success: boolean
    item_key: string
    attachment_key: string
    collection_key?: string
    collection_verified: boolean
    pdf_imported: boolean
    warnings: string[]
  }>('/integrations/zotero/push', data),
}

export const agentApi = {
  chat: (data: {
    question: string
    history?: AgentMessage[]
    use_knowledge?: boolean
    use_zotero?: boolean
    knowledge_category?: string | null
  }, signal?: AbortSignal) => api.post<AgentChatResponse>('/agent/chat', data, { signal }),
}

// =============================================================================
// 知识库 API
// =============================================================================

export const knowledgeApi = {
  list: (category?: string, options: { page?: number; page_size?: number; keyword?: string } = {}) =>
    api.get<KnowledgeListResponse>('/knowledge', { params: { ...options, ...(category ? { category } : {}) } }),

  get: (id: string) =>
    api.get<KnowledgeItem>(`/knowledge/${id}`),

  create: (data: KnowledgeCreateRequest) =>
    api.post<KnowledgeItem>('/knowledge', data),

  update: (id: string, data: KnowledgeUpdateRequest) =>
    api.put<KnowledgeItem>(`/knowledge/${id}`, data),

  delete: (id: string) =>
    api.delete(`/knowledge/${id}`),

  getCategories: () =>
    api.get<{ name: string; count: number }[]>('/knowledge/categories'),

  aiAnalyze: (knowledgeIds: string[], query?: string) =>
    api.post<AIAnalyzeResponse>('/knowledge/ai-analyze', { knowledge_ids: knowledgeIds, ...(query !== undefined ? { query } : {}) }),

  recommend: (knowledgeIds: string[], limit: number = 5) =>
    api.post<RecommendResponse>('/knowledge/recommend', { knowledge_ids: knowledgeIds, limit }),

  createRoute: (data: RouteCreateRequest) =>
    api.post<ResearchRoute>('/knowledge/routes', data),

  listRoutes: (options: { page?: number; page_size?: number } = {}) =>
    api.get<{ items: ResearchRoute[]; total: number }>('/knowledge/routes', { params: options }),

  getRoute: (id: string) =>
    api.get<ResearchRoute>(`/knowledge/routes/${id}`),

  updateRoute: (id: string, data: any) =>
    api.put<ResearchRoute>(`/knowledge/routes/${id}`, data),

  deleteRoute: (id: string) =>
    api.delete(`/knowledge/routes/${id}`),

  generateRouteAnalysis: (id: string) =>
    api.post<ResearchRoute>(`/knowledge/routes/${id}/ai-generate`),

  // SSE 流式：分步返回 文字分析→架构图→路线图 的进度
  generateRouteAnalysisStream: async (
    id: string,
    onEvent: (event: {
      event: string
      progress?: number
      stage?: string
      message?: string
      data?: any
    }) => void,
    signal?: AbortSignal,
  ) => {
    const res = await fetch(`/api/v1/knowledge/routes/${id}/ai-generate-stream`, {
      method: 'POST',
      signal,
    })
    if (!res.ok || !res.body) {
      throw new Error(`SSE request failed: ${res.status}`)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed.startsWith('data:')) {
          try {
            onEvent(JSON.parse(trimmed.slice(5).trim()))
          } catch { /* 忽略无法解析的帧 */ }
        }
      }
    }
  },
}

export default api

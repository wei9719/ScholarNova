// =============================================================================
// 枚举类型
// =============================================================================

export type DataSource = 'semantic_scholar' | 'openalex' | 'crossref' | 'arxiv' | 'zotero'

export type SearchStatus = 'pending' | 'running' | 'completed' | 'failed'

export type Verdict = 'supports' | 'contradicts' | 'neutral' | 'insufficient'

export type FeedbackType = 'helpful' | 'not_helpful' | 'saved' | 'dismissed'

export type AnalysisType = 'summary' | 'methodology' | 'findings' | 'pros_cons' | 'full'

export type LLMProvider = 'openai' | 'anthropic' | 'ollama' | 'local' | 'mimo' | 'deepseek' | 'zhipu' | 'qwen' | 'siliconflow' | 'moonshot' | 'sensenova' | 'custom'

// =============================================================================
// 请求类型
// =============================================================================

export interface SearchRequest {
  query: string
  max_results?: number
  sources?: DataSource[]
  date_from?: string
  date_to?: string
  min_citations?: number
  open_access_only?: boolean
}

export interface AnalysisRequest {
  query: string
  analysis_type?: AnalysisType
}

export interface CompareRequest {
  paper_ids: string[]
  query: string
}

export interface RecommendationRequest {
  user_id?: string
  context?: string
  limit?: number
}

export interface RecommendationFeedback {
  recommendation_id: string
  feedback_type: FeedbackType
  comment?: string
}

export interface TaskModelConfig {
  provider?: LLMProvider
  model_name?: string
  api_key?: string
  base_url?: string
  api_key_configured?: boolean
}

export interface FallbackModelConfig extends TaskModelConfig {
  enabled: boolean
}

export interface EmbeddingModelConfig {
  enabled: boolean
  provider: LLMProvider
  model_name: string
  api_key?: string
  api_key_configured?: boolean
  base_url?: string
}

export interface ModelConfig {
  provider: LLMProvider
  model_name: string
  api_key?: string
  api_key_configured?: boolean
  base_url?: string
  temperature?: number
  max_tokens?: number
  tasks?: Record<string, TaskModelConfig>
  fallback?: FallbackModelConfig
  embedding?: EmbeddingModelConfig
}

export interface ModelTestRequest {
  provider: LLMProvider
  model_name: string
  api_key?: string
  base_url?: string
}

export interface ModelCapabilityReport {
  provider: string
  model_name: string
  task?: string | null
  status: 'supported' | 'unsupported' | 'unknown'
  requirements: string[]
  capabilities: Record<string, boolean | null>
  labels: Record<string, { zh: string; en: string }>
  reason_zh: string
  reason_en: string
}

export type ModelProbeTask =
  | 'analysis'
  | 'query_planning'
  | 'translation'
  | 'vision'
  | 'recommendation'
  | 'assistant'
  | 'diagram'

export interface ModelCapabilityProbeResult {
  success: boolean
  status: 'passed' | 'failed'
  provider: string
  model_name: string
  task: ModelProbeTask
  capability: string
  tested_at: string
  latency_ms: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  detail_zh: string
  detail_en: string
  error?: string | null
}

// =============================================================================
// 响应类型
// =============================================================================

export interface SubQuery {
  query: string
  source: DataSource
  rationale: string
}

export interface QueryPlan {
  sub_queries: SubQuery[]
  strategy: string
  intent?: string | null
  keywords?: string[]
  constraints?: Array<{
    key: string
    operator: string
    value: unknown
    description?: string | null
  }>
  entities?: Record<string, string[]>
  expanded_queries?: string[]
}

export interface SearchResponse {
  run_id: string
  status: SearchStatus
  query_plan: QueryPlan | null
  message: string
}

export interface SearchProgress {
  total_sources: number
  completed_sources: number
  total_papers: number
  deduplicated_papers: number
  current_phase: string
  search_rounds?: number
  api_calls?: number
  latency_ms?: number
  source_calls?: SourceCall[]
}

export interface SourceCall {
  source: string
  label?: string
  api_name?: string
  endpoint?: string
  query?: string | null
  status?: 'pending' | 'completed' | 'failed' | string
  success?: boolean | null
  paper_count: number
  elapsed_ms: number
  error?: string | null
  round?: number
}

export interface Paper {
  id: string
  title: string
  authors: string[]
  abstract: string | null
  year: number | null
  venue: string | null
  citation_count: number
  doi: string | null
  url: string | null
  pdf_url: string | null
  source: DataSource
  corpus_id?: string | null
  relevance_score: number | null
  ranking_score?: number | null
  is_open_access: boolean
  quality?: {
    quality_score: number
    citation_percentile: number
    citation_velocity: number
    impact_label: 'highly_cited' | 'well_cited' | 'established' | 'emerging' | 'limited_signal'
    citation_basis: 'result_set' | string
    wos_indexed: boolean | null
    jcr_quartile: string | null
    cas_quartile: string | null
    sjr_quartile?: string | null
    sjr_score?: number | null
    partition_year: number | null
    partition_status: 'verified' | 'unverified' | string
    partition_source: string | null
    matched_venue?: string | null
    match_confidence?: number | null
    openalex_source_id?: string | null
    openalex_h_index?: number | null
    openalex_i10_index?: number | null
    openalex_2yr_mean_citedness?: number | null
    openalex_works_count?: number | null
    openalex_cited_by_count?: number | null
    openalex_is_in_doaj?: boolean | null
    open_metrics_source?: string | null
  } | null
}

export interface PaperReference {
  id: string
  title: string
  year: number | null
  citation_count: number
}

export interface PaperDetail extends Paper {
  references: PaperReference[]
  citations: PaperReference[]
  fields_of_study: string[]
  keywords: string[]
  publication_date: string | null
  volume: string | null
  issue: string | null
  pages: string | null
}

export interface SearchRunDetail {
  run_id: string
  status: SearchStatus
  original_query: string
  query_plan: QueryPlan | null
  progress: SearchProgress | null
  results: Paper[]
  source_status?: SourceCall[]
  runtime_metrics?: {
    api_calls?: number
    search_rounds?: number
    latency_ms?: number
    successful_calls?: number
    failed_calls?: number
    token_usage?: Record<string, unknown>
  }
  result_summary?: {
    total?: number
    highly_relevant?: number
    partially_relevant?: number
    other?: number
    year_range?: number[]
    top_venues?: Array<{ name: string; count: number }>
    intent?: string | null
    entity_dimensions?: Record<string, string[]>
  }
  created_at: string
  completed_at: string | null
}

export interface AnalysisResult {
  paper_id: string
  analysis_type: AnalysisType
  summary: string
  methodology: string | null
  key_findings: string[]
  strengths: string[]
  weaknesses: string[]
  relevance_to_query: string | null
  document_coverage: 'abstract' | 'fulltext' | string
  document_source: string | null
  document_error: string | null
  visual_pages_read: number
  model_completed: boolean
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  created_at: string
}

export interface FulltextStatus {
  available: boolean
  source: string | null
  file_size: number
  page_count?: number
  feature_count?: number
  feature_error?: string | null
}

export interface CompareResult {
  papers: Array<{
    paper_id: string
    title: string
  }>
  comparison: {
    methodology: string
    results: string
    strengths_weaknesses: string
    recommendation: string
  }
  created_at: string
}

export interface EvidenceSpan {
  id: string
  run_id: string
  paper_id: string
  claim: string
  evidence_text: string
  verdict: Verdict
  confidence: number
  page_number: number | null
  section: string | null
  context: string | null
  llm_model: string | null
  created_at: string
}

export interface EvidenceResponse {
  paper_id: string
  run_id: string
  evidence_spans: EvidenceSpan[]
}

export interface Recommendation {
  id: string
  paper: Paper
  score: number
  reason: string
}

export interface RecommendationResponse {
  recommendations: Recommendation[]
  has_more: boolean
}

export interface ModelTestResponse {
  success: boolean
  latency_ms: number | null
  model_info: {
    provider: string
    model: string
    context_window?: number
    dimensions?: number
    input_tokens?: number
  } | null
  error: string | null
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy'
  version: string
  timestamp: string
  services: {
    database: 'connected' | 'disconnected'
    redis: 'connected' | 'disconnected'
    llm: 'available' | 'unavailable'
  }
}

export interface ZoteroStatus {
  connected: boolean
  server_id: string | null
  zotero_version: string | null
  mode: 'local_read_only'
}

export interface ZoteroCollection {
  key: string
  name: string
  parent_collection: string | null
  version: number | null
}

export interface ZoteroImportResult {
  success: boolean
  created: number
  updated: number
  skipped: number
  total: number
  paper_ids: string[]
  read_only: boolean
}

export interface AgentMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AgentCitation {
  id: string
  source: 'knowledge' | 'paper' | 'zotero'
  title: string
  doi: string | null
  item_id: string | null
  url: string | null
  section: string | null
  page: number | null
  chunk_index: number | null
}

export interface AgentToolStep {
  tool: string
  status: 'completed' | 'skipped' | 'unavailable'
  count: number
  detail: string
}

export interface AgentModelAttempt {
  role: 'primary' | 'fallback'
  provider: string
  model: string
  status: 'completed' | 'unavailable'
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  requests: number
  request_attempts?: number
  responses_received?: number
  usage_reports?: number
  error_type: string | null
}

export interface AgentChatResponse {
  answer: string
  citations: AgentCitation[]
  tool_steps: AgentToolStep[]
  response_type: 'research' | 'product_help'
  provider: string | null
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  retrieval_tokens: number
  total_tokens: number
  retrieval_mode: 'bm25' | 'hybrid'
  inference_mode: 'model' | 'deterministic_fallback' | 'none'
  fallback_reason?: 'model_unavailable' | 'citation_verification' | 'invalid_help_response' | null
  verification_status: 'verified' | 'partial' | 'failed' | 'not_applicable'
  citation_coverage: number
  uncited_claim_count: number
  invalid_citation_ids: string[]
  fallback_used: boolean
  model_fallback_used: boolean
  model_route: 'primary' | 'fallback' | 'deterministic' | 'none'
  model_attempts: AgentModelAttempt[]
  grounded: boolean
  created_at: string
}

export interface SuccessResponse {
  success: boolean
  message: string
}

export interface TranslateResponse {
  translated: string
}

// =============================================================================
// 知识库类型
// =============================================================================

export interface KnowledgeItem {
  id: string
  title: string
  category: string
  content: string
  source_paper_id: string | null
  source_paper_title: string | null
  source_paper_doi: string | null
  research_points: string[]
  tags: string[]
  notes: string | null
  created_at: string
  updated_at: string
  polish_status?: 'skipped' | 'completed' | 'invalid_response' | 'model_unavailable' | null
  model_completed?: boolean | null
  provider?: string | null
  model?: string | null
  fallback_used?: boolean
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

export interface KnowledgeListResponse {
  items: KnowledgeItem[]
  total: number
  categories: { name: string; count: number }[]
}

export interface KnowledgeCreateRequest {
  title: string
  category: string
  content: string
  source_paper_id?: string | null
  source_paper_title?: string | null
  source_paper_doi?: string | null
  research_points?: string[]
  tags?: string[]
  notes?: string | null
  auto_polish?: boolean
  card_type?: string
  card_data?: Record<string, any>
}

export interface KnowledgeUpdateRequest extends Partial<KnowledgeCreateRequest> {}

export interface ResearchRoute {
  id: string
  title: string
  description: string
  knowledge_ids: string[]
  ai_analysis: string
  status: string
  created_at: string
}

export interface RouteCreateRequest {
  title: string
  description?: string
  knowledge_ids?: string[]
}

export interface AIAnalyzeRequest {
  knowledge_ids: string[]
}

export interface AIAnalyzeResponse {
  analysis: string
  knowledge_count: number
  created_at: string
  provider?: string | null
  model?: string | null
  model_completed: boolean
  fallback_used: boolean
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  research_directions?: string[]
  architecture_description?: string
  architecture_json?: { title?: string; layers?: { name: string; modules?: { name: string; desc?: string }[]; formula?: string }[] } | null
  suggested_routes?: string[]
  recommended_papers?: string[]
}

export interface RecommendRequest {
  knowledge_ids: string[]
  limit: number
}

export interface RecommendResponse {
  recommendations: string
  knowledge_count: number
  created_at: string
  provider?: string | null
  model?: string | null
  model_completed: boolean
  fallback_used: boolean
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

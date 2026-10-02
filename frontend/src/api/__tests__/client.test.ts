/**
 * API 客户端测试
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import axios from 'axios'
import { agentApi, searchApi, papersApi, modelApi, healthApi } from '../client'

// Mock axios
vi.mock('axios', () => {
  const mockAxios = {
    create: vi.fn(() => mockAxios),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    post: vi.fn(),
    get: vi.fn(),
  }
  return { default: mockAxios }
})

describe('API Client', () => {
  describe('searchApi', () => {
    it('should call POST /search with request body', async () => {
      const mockResponse = {
        data: {
          run_id: 'test-run-id',
          status: 'pending',
          message: 'Search started',
        },
      }
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.post.mockResolvedValue(mockResponse)

      const result = await searchApi.create({
        query: 'attention mechanism',
        max_results: 10,
      })

      expect(mockedAxios.post).toHaveBeenCalledWith('/search', {
        query: 'attention mechanism',
        max_results: 10,
      })
    })

    it('should call GET /search/{runId}', async () => {
      const mockResponse = {
        data: {
          run_id: 'test-run-id',
          status: 'completed',
          results: [],
        },
      }
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.get.mockResolvedValue(mockResponse)

      await searchApi.getRun('test-run-id')

      expect(mockedAxios.get).toHaveBeenCalledWith('/search/test-run-id')
    })
  })

  describe('papersApi', () => {
    it('should call GET /papers/{paperId}', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.get.mockResolvedValue({ data: {} })

      await papersApi.get('paper-123')

      expect(mockedAxios.get).toHaveBeenCalledWith('/papers/paper-123')
    })

    it('should call POST /papers/{paperId}/analyze', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.post.mockResolvedValue({ data: {} })

      await papersApi.analyze('paper-123', {
        query: 'summarize',
        analysis_type: 'full',
      })

      expect(mockedAxios.post).toHaveBeenCalledWith('/papers/paper-123/analyze', {
        query: 'summarize',
        analysis_type: 'full',
      }, { signal: undefined })
    })

    it('should call POST /papers/compare', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.post.mockResolvedValue({ data: {} })

      await papersApi.compare({
        paper_ids: ['p1', 'p2'],
        query: 'compare methods',
      })

      expect(mockedAxios.post).toHaveBeenCalledWith('/papers/compare', {
        paper_ids: ['p1', 'p2'],
        query: 'compare methods',
      })
    })
  })

  describe('modelApi', () => {
    it('should call GET /model/config', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.get.mockResolvedValue({ data: { provider: 'zhipu' } })

      await modelApi.getConfig()

      expect(mockedAxios.get).toHaveBeenCalledWith('/model/config')
    })

    it('should inspect task capability without a paid model call', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.get.mockResolvedValue({ data: { status: 'supported' } })

      await modelApi.getCapabilities('mimo', 'mimo-v2.5', 'vision')

      expect(mockedAxios.get).toHaveBeenCalledWith('/model/capabilities', {
        params: { provider: 'mimo', model_name: 'mimo-v2.5', task: 'vision' },
      })
    })

    it('should load a locally saved capability probe without calling the model', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.get.mockResolvedValue({ data: null })

      await modelApi.getCapabilityProbe('qwen', 'qwen-plus', 'query_planning')

      expect(mockedAxios.get).toHaveBeenCalledWith('/model/capabilities/probe', {
        params: { provider: 'qwen', model_name: 'qwen-plus', task: 'query_planning' },
      })
    })

    it('should run a real capability probe only through an explicit POST', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.post.mockResolvedValue({ data: { status: 'passed' } })

      await modelApi.probeCapability({
        provider: 'qwen',
        model_name: 'qwen-plus',
        task: 'query_planning',
      })

      expect(mockedAxios.post).toHaveBeenCalledWith('/model/capabilities/probe', {
        provider: 'qwen',
        model_name: 'qwen-plus',
        task: 'query_planning',
      })
    })

    it('should call POST /model/config', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.post.mockResolvedValue({ data: { success: true } })

      await modelApi.saveConfig({
        provider: 'openai',
        model_name: 'gpt-4o',
      })

      expect(mockedAxios.post).toHaveBeenCalledWith('/model/config', {
        provider: 'openai',
        model_name: 'gpt-4o',
      })
    })

    it('should call POST /model/test', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.post.mockResolvedValue({
        data: { success: true, latency_ms: 100 },
      })

      await modelApi.testConnection({
        provider: 'openai',
        model_name: 'gpt-4o',
      })

      expect(mockedAxios.post).toHaveBeenCalledWith('/model/test', {
        provider: 'openai',
        model_name: 'gpt-4o',
      })
    })
  })

  describe('healthApi', () => {
    it('should call GET /health', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.get.mockResolvedValue({
        data: { status: 'healthy', version: '1.0.0' },
      })

      await healthApi.check()

      expect(mockedAxios.get).toHaveBeenCalledWith('/health')
    })
  })

  describe('agentApi', () => {
    it('should call POST /agent/chat with selected local sources', async () => {
      const mockedAxios = vi.mocked(axios.create())
      mockedAxios.post.mockResolvedValue({ data: { answer: 'Grounded answer' } })

      await agentApi.chat({
        question: 'What are the research gaps?',
        use_knowledge: true,
        use_zotero: true,
      })

      expect(mockedAxios.post).toHaveBeenCalledWith('/agent/chat', {
        question: 'What are the research gaps?',
        use_knowledge: true,
        use_zotero: true,
      }, { signal: undefined })
    })

    it('forwards cancellation signals for model requests', async () => {
      const signal = new AbortController().signal
      const mockedAxios = vi.mocked(axios.create())
      await agentApi.chat({ question: 'Help' }, signal)
      expect(mockedAxios.post).toHaveBeenCalledWith('/agent/chat', { question: 'Help' }, { signal })
      await papersApi.analyze('p1', { query: 'Analyze', analysis_type: 'full' }, signal)
      expect(mockedAxios.post).toHaveBeenCalledWith('/papers/p1/analyze', { query: 'Analyze', analysis_type: 'full' }, { signal })
    })
  })
})

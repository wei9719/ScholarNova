import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelConfig as ModelConfigType } from '@/api/types'
import ModelConfig from '../ModelConfig/ModelConfig'

const mocks = vi.hoisted(() => ({ capabilities: vi.fn(), savedProbe: vi.fn(), probe: vi.fn() }))
vi.mock('@/api/client', () => ({
  modelApi: { getCapabilities: mocks.capabilities, getCapabilityProbe: mocks.savedProbe, probeCapability: mocks.probe },
}))
vi.mock('@/stores/localeStore', () => ({
  useLocaleStore: () => ({ locale: 'zh', t: (key: string) => key }),
}))

const localModel = 'Qwen2.5-1.5B-Instruct'
const localUrl = 'http://127.0.0.1:8766/v1'
const cloudProviders = ['openai', 'anthropic', 'ollama', 'mimo', 'deepseek', 'zhipu', 'qwen', 'siliconflow', 'moonshot', 'sensenova', 'custom']

function makeConfig(overrides: Partial<ModelConfigType> = {}): ModelConfigType {
  return {
    provider: 'zhipu', model_name: 'glm-5.2', api_key: 'test-cloud-key', base_url: 'https://cloud.example/v1',
    tasks: { vision: { provider: 'zhipu', model_name: 'glm-4.6v-flash' } },
    fallback: { enabled: true, provider: 'qwen', model_name: 'qwen-plus' },
    embedding: { enabled: true, provider: 'ollama', model_name: 'nomic-embed-text' },
    ...overrides,
  }
}

function renderConfig(initial = makeConfig()) {
  const onChange = vi.fn()
  function Harness() {
    const [config, setConfig] = useState(initial)
    return <ModelConfig config={config} testResult={null} isTesting={false} isSaving={false}
      onConfigChange={(partial) => { onChange(partial); setConfig((previous) => ({ ...previous, ...partial })) }}
      onTest={vi.fn()} onSave={vi.fn()} fallbackTestResult={null} isFallbackTesting={false}
      onFallbackTest={vi.fn()} embeddingTestResult={null} isEmbeddingTesting={false} onEmbeddingTest={vi.fn()} />
  }
  render(<Harness />)
  return onChange
}

async function openTask(label: string) {
  const toggle = screen.queryByRole('button', { name: '按任务类型配置不同模型' })
  if (toggle) fireEvent.click(toggle)
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }))
  })
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.capabilities.mockResolvedValue({ data: null })
  mocks.savedProbe.mockResolvedValue({ data: null })
  mocks.probe.mockResolvedValue({ data: {
    success: true, latency_ms: 1, total_tokens: 2, prompt_tokens: 1, completion_tokens: 1,
    detail_zh: '本机文字测试通过', tested_at: '2026-09-19T00:00:00Z',
  } })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('local text provider routing', () => {
  it('offers local only for the assistant while preserving every existing default provider', async () => {
    renderConfig()
    const globalProvider = screen.getByRole('combobox', { name: 'settings.provider' })
    expect(within(globalProvider).getAllByRole('option').map((item) => item.getAttribute('value'))).toEqual(cloudProviders)
    for (const task of ['论文分析', '查询规划', '翻译', '图表/架构分析', '论文推荐', '科研问答智能体', '图表生成']) {
      await openTask(task)
    }
    const localOptions = screen.getAllByRole('option', { name: '本机模型（离线文字）' })
    expect(localOptions).toHaveLength(1)
    expect(localOptions[0].parentElement).toBe(screen.getByRole('combobox', { name: '科研问答智能体 提供商' }))
    expect(within(globalProvider).queryByRole('option', { name: '本机模型（离线文字）' })).not.toBeInTheDocument()
  })

  it('selects local defaults only for the assistant and clears cloud credentials', async () => {
    const config = makeConfig({ tasks: {
      vision: { provider: 'zhipu', model_name: 'glm-4.6v-flash' },
      assistant: { provider: 'qwen', model_name: 'qwen-plus', api_key: 'test-task-cloud-key', api_key_configured: true },
    } })
    const onChange = renderConfig(config)
    await openTask('科研问答智能体')
    await act(async () => fireEvent.change(screen.getByRole('combobox', { name: '科研问答智能体 提供商' }), { target: { value: 'local' } }))

    expect(onChange).toHaveBeenLastCalledWith({ tasks: {
      ...config.tasks,
      assistant: { provider: 'local', model_name: localModel, base_url: localUrl, api_key: '', api_key_configured: false },
    } })
    expect(screen.getByRole('combobox', { name: 'settings.provider' })).toHaveValue('zhipu')
    expect(screen.getByDisplayValue(localModel)).toBeInTheDocument()
    expect(screen.getByDisplayValue(localUrl)).toBeInTheDocument()
    expect(screen.getByLabelText('本机服务凭证（不是云 API Key）')).toHaveValue('')
    expect(screen.getByText(/需要先配置本机推理服务；不支持视觉、生图、向量；选择本地模型不代表所有流程离线/)).toBeInTheDocument()
    expect(screen.getByText(/无云API Key，需要本机服务凭证/)).toBeInTheDocument()
  })

  it('loads saved local settings and probes with local defaults, never the cloud key or URL', async () => {
    renderConfig(makeConfig({ tasks: { assistant: { provider: 'local', api_key_configured: true } } }))
    await openTask('科研问答智能体')
    expect(screen.getByDisplayValue(localModel)).toBeInTheDocument()
    expect(screen.getByLabelText('本机服务凭证（不是云 API Key）')).toHaveAttribute('placeholder', '已安全保存在本机后端')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '真实测试此任务' })))
    expect(mocks.probe).toHaveBeenCalledWith({
      provider: 'local', model_name: localModel, base_url: localUrl, api_key: undefined, task: 'assistant',
    })
  })

  it('uses an explicitly entered service credential and clears it when switching back to a cloud provider', async () => {
    const onChange = renderConfig(makeConfig({ tasks: { assistant: { provider: 'local', model_name: localModel, base_url: localUrl } } }))
    await openTask('科研问答智能体')
    fireEvent.change(screen.getByLabelText('本机服务凭证（不是云 API Key）'), { target: { value: 'test-local-token' } })
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '真实测试此任务' })))
    expect(mocks.probe).toHaveBeenCalledWith(expect.objectContaining({ api_key: 'test-local-token', base_url: localUrl }))
    await act(async () => fireEvent.change(screen.getByRole('combobox', { name: '科研问答智能体 提供商' }), { target: { value: 'openai' } }))
    expect(onChange).toHaveBeenLastCalledWith({ tasks: { assistant: {
      provider: 'openai', model_name: 'gpt-4o', base_url: 'https://api.openai.com/v1', api_key: '', api_key_configured: false,
    } } })
    expect(screen.queryByLabelText('本机服务凭证（不是云 API Key）')).not.toBeInTheDocument()
  })

  it('shows an invalid saved global local provider without offering it as a new default or inheriting its credentials', async () => {
    renderConfig(makeConfig({ provider: 'local', tasks: { assistant: { provider: 'local' } } }))
    const globalProvider = screen.getByRole('combobox', { name: 'settings.provider' })
    expect(within(globalProvider).getByRole('option', { name: '本机模型（离线文字）' })).toBeDisabled()
    expect(screen.getByText(/本机模型不能作为默认模型/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'settings.testConnection' })).toBeDisabled()
    await openTask('科研问答智能体')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '真实测试此任务' })))
    expect(mocks.probe).toHaveBeenCalledWith(expect.objectContaining({ api_key: undefined, base_url: localUrl }))
  })

  it('does not allow probing an unsupported saved local task', async () => {
    renderConfig(makeConfig({ tasks: { vision: { provider: 'local', model_name: localModel } } }))
    await openTask('图表/架构分析')
    const provider = screen.getByRole('combobox', { name: '图表/架构分析 提供商' })
    expect(within(provider).getByRole('option', { name: '本机模型（离线文字）' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '真实测试此任务' })).toBeDisabled()
    expect(screen.getByText(/本机模型目前仅支持科研问答智能体/)).toBeInTheDocument()
    expect(mocks.probe).not.toHaveBeenCalled()
  })

  it('stops the desktop local service without changing model routing or cloud configuration', async () => {
    const bridge = {
      status: vi.fn().mockResolvedValue({ status: 'running' }),
      start: vi.fn(),
      stop: vi.fn().mockResolvedValue({ status: 'stopped' }),
    }
    vi.stubGlobal('scholarLocalModel', bridge)
    const onChange = renderConfig(makeConfig({ tasks: { assistant: { provider: 'local', model_name: localModel } } }))
    expect(screen.queryByRole('button', { name: '停止本机模型' })).not.toBeInTheDocument()
    await openTask('科研问答智能体')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '停止本机模型' })))
    expect(bridge.stop).toHaveBeenCalledOnce()
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('combobox', { name: '科研问答智能体 提供商' })).toHaveValue('local')
    expect(screen.getByRole('combobox', { name: 'settings.provider' })).toHaveValue('zhipu')
    expect(mocks.probe).not.toHaveBeenCalled()
  })
})

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import toast, { Toaster } from 'react-hot-toast'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useModelStore } from '@/stores/modelStore'
import Settings from '../Settings'

const mocks = vi.hoisted(() => ({ getConfig: vi.fn(), testConnection: vi.fn(), testEmbedding: vi.fn(), saveConfig: vi.fn() }))
vi.mock('@/api/client', () => ({ modelApi: mocks }))
vi.mock('@/stores/localeStore', () => ({ useLocaleStore: () => ({ locale: 'zh', t: (key: string) => key }) }))
// Keep the actual Settings and ModelConfig; unrelated integration panels do not run.
vi.mock('@/components/ZoteroIntegration', () => ({ default: () => null }))
vi.mock('@/components/NetworkConfig', () => ({ default: () => null }))
vi.mock('@/components/OpenSourceInfo', () => ({ default: () => null }))

const config = {
  provider: 'qwen' as const, model_name: 'qwen-plus', api_key: '',
  fallback: { enabled: true, provider: 'qwen' as const, model_name: 'qwen-turbo', api_key: '' },
  embedding: { enabled: true, provider: 'ollama' as const, model_name: 'bge-m3', api_key: '' },
}
const validationError = { response: { status: 422, data: { detail: [
  { msg: '模型参数不正确', input: 'PRIVATE_KEY', loc: ['body', 'api_key'] },
] } } }

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
  localStorage.clear()
  useModelStore.setState({ config, testResult: null, isTesting: false, isSaving: false })
  mocks.getConfig.mockResolvedValue({ data: config })
  mocks.testConnection.mockResolvedValue({ data: { success: true, latency_ms: 1, error: null } })
  mocks.testEmbedding.mockResolvedValue({ data: { success: true, latency_ms: 1, model_info: { dimensions: 10 }, error: null } })
  mocks.saveConfig.mockResolvedValue({ data: { success: true } })
})
afterEach(() => {
  cleanup()
  toast.remove()
  vi.unstubAllGlobals()
})

it.each([
  { button: 'settings.testConnection', method: 'testConnection' as const },
  { button: '测试备用模型', method: 'testConnection' as const },
  { button: '测试语义模型', method: 'testEmbedding' as const },
])('safely renders $button errors and recovers on retry with the actual ModelConfig', async ({ button, method }) => {
  mocks[method].mockRejectedValueOnce(validationError)
  render(<Settings />)
  fireEvent.click(screen.getByRole('button', { name: button }))
  expect(await screen.findByText('模型参数不正确')).toBeInTheDocument()
  expect(screen.queryByText(/PRIVATE_KEY/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: button })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: button }))
  await waitFor(() => expect(screen.queryByText('模型参数不正确')).not.toBeInTheDocument())
  expect(mocks[method]).toHaveBeenCalledTimes(2)
})

it('renders save validation errors in a real toast, without rendering the response object', async () => {
  mocks.saveConfig.mockRejectedValueOnce(validationError)
  render(<><Settings /><Toaster /></>)
  fireEvent.click(screen.getByRole('button', { name: 'settings.saveConfig' }))
  expect(await screen.findByText('模型参数不正确')).toBeInTheDocument()
  expect(screen.queryByText(/PRIVATE_KEY/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'settings.saveConfig' })).toBeEnabled()
})

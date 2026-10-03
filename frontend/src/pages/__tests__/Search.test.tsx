import { act, cleanup, render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useSearchStore } from '@/stores/searchStore'
import Search from '../Search'

const api = vi.hoisted(() => ({ create: vi.fn(), getRun: vi.fn(), get: vi.fn(), analyze: vi.fn() }))
const uploadCallback = vi.hoisted(() => ({ current: () => {} }))
const analyzeCallback = vi.hoisted(() => ({ current: () => {} }))
vi.mock('@/api/client', () => ({
  searchApi: { create: api.create, getRun: api.getRun },
  papersApi: { get: api.get, analyze: api.analyze },
  networkApi: {},
}))
vi.mock('@/components/QueryPlan/QueryPlan', () => ({ default: () => null }))
vi.mock('@/components/SearchInsights/SearchInsights', () => ({ default: () => null }))
vi.mock('@/components/ResultsList/ResultsList', () => ({
  default: ({ onPaperClick }: any) => <div>
    <button onClick={() => onPaperClick({ id: 'a', title: 'Paper A' })}>Paper A</button>
    <button onClick={() => onPaperClick({ id: 'b', title: 'Paper B' })}>Paper B</button>
  </div>,
}))
vi.mock('@/components/PaperDetail/PaperDetail', () => ({
  default: ({ paper, analysis, analysisLoading, onAnalyze, onFulltextUploaded, onClose }: any) => {
    uploadCallback.current = onFulltextUploaded
    analyzeCallback.current = onAnalyze
    return <div>
      <div data-testid="detail">{paper.id}</div>
      <div data-testid="analysis">{analysis?.summary || ''}</div>
      <div data-testid="busy">{String(analysisLoading)}</div>
      <button onClick={() => onAnalyze('Analyze')}>Analyze</button>
      <button onClick={onClose}>Close detail</button>
    </div>
  },
}))

beforeEach(() => {
  vi.resetAllMocks()
  sessionStorage.clear()
  useSearchStore.getState().clearSearch()
  api.create.mockResolvedValue({ data: { run_id: 'run' } })
  api.getRun.mockResolvedValue({ data: { run_id: 'run', status: 'completed', results: [{ id: 'a' }], query: 'traffic' } })
})

afterEach(cleanup)

it('sends automatic planning for an existing query URL without a mode', async () => {
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  await screen.findByText('Paper A')
  expect(screen.getByRole('combobox', { name: '检索规划' })).toHaveValue('auto')
  expect(api.create).toHaveBeenCalledWith({ query: 'traffic', planning_mode: 'auto' })
})

it('uses the real SearchBar to submit fast search and repeat the same query with AI planning', async () => {
  const user = userEvent.setup()
  render(<MemoryRouter initialEntries={['/search']}><Search /></MemoryRouter>)
  const mode = screen.getByRole('combobox', { name: '检索规划' })
  await act(async () => { await user.selectOptions(mode, 'rules') })
  expect(screen.getByText(/快速检索不调用规划模型/)).toBeInTheDocument()
  expect(api.create).not.toHaveBeenCalled()
  await act(async () => { await user.type(screen.getByRole('textbox'), 'traffic{Enter}') })
  await screen.findByText('Paper A')
  expect(api.create).toHaveBeenNthCalledWith(1, { query: 'traffic', planning_mode: 'rules' })

  await act(async () => { await user.selectOptions(mode, 'ai') })
  expect(api.create).toHaveBeenCalledTimes(1)
  expect(screen.getByText(/这不是整个搜索的总时限/)).toBeInTheDocument()
  await act(async () => { await user.click(screen.getByRole('button', { name: '搜索' })) })
  await screen.findByText('Paper A')
  expect(api.create).toHaveBeenNthCalledWith(2, { query: 'traffic', planning_mode: 'ai' })
  await act(async () => { await user.click(screen.getByRole('button', { name: '搜索' })) })
  await screen.findByText('Paper A')
  expect(api.create).toHaveBeenNthCalledWith(3, { query: 'traffic', planning_mode: 'ai' })
  expect(screen.getByRole('textbox')).toHaveValue('traffic')
})

it('locks planning mode while searching and makes it available after completion', async () => {
  let finish!: (value: any) => void
  api.create.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  const mode = screen.getByRole('combobox', { name: '检索规划' })
  expect(mode).toBeDisabled()
  fireEvent.change(mode, { target: { value: 'ai' } })
  expect(mode).toHaveValue('auto')
  expect(api.create).toHaveBeenCalledOnce()
  expect(api.create).toHaveBeenCalledWith({ query: 'traffic', planning_mode: 'auto' })
  await act(async () => { finish({ data: { run_id: 'run' } }) })
  await screen.findByText('Paper A')
  expect(mode).toBeEnabled()
})

it('restores an old recent query to the real SearchBar and can repeat it with another planning mode', async () => {
  sessionStorage.setItem('scholarnova-search-history', JSON.stringify([{ query: 'previous topic', at: 1 }]))
  render(<MemoryRouter initialEntries={['/search']}><Search /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'previous topic' }))
  await screen.findByText('Paper A')
  expect(api.create).toHaveBeenCalledWith({ query: 'previous topic', planning_mode: 'auto' })
  expect(screen.getByRole('textbox')).toHaveValue('previous topic')
  expect(screen.getByRole('button', { name: '搜索' })).toBeEnabled()
  fireEvent.change(screen.getByRole('combobox', { name: '检索规划' }), { target: { value: 'rules' } })
  fireEvent.click(screen.getByRole('button', { name: '搜索' }))
  await screen.findByText('Paper A')
  expect(api.create).toHaveBeenNthCalledWith(2, { query: 'previous topic', planning_mode: 'rules' })
})

it('shows validation messages safely and permits retry after search creation returns 422', async () => {
  api.create.mockRejectedValueOnce({ response: { status: 422, data: { detail: [
    { msg: '检索条件格式不正确', input: 'PRIVATE_INPUT' },
  ] } } })
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  expect(await screen.findByText('检索条件格式不正确')).toBeInTheDocument()
  expect(screen.queryByText(/PRIVATE_INPUT/)).not.toBeInTheDocument()
  expect(screen.getByRole('textbox')).toBeInTheDocument()
  expect(useSearchStore.getState().isLoading).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '搜索' }))
  await screen.findByText('Paper A')
  expect(api.create).toHaveBeenCalledTimes(2)
})

it('shows structured polling errors as text without losing the search controls', async () => {
  api.getRun.mockRejectedValueOnce({ response: { data: { detail: { code: 'queue_full', message: '服务繁忙，请稍后重新检索', retry_after: 2 } } } })
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  expect(await screen.findByText('服务繁忙，请稍后重新检索')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '搜索' })).toBeEnabled()
  expect(useSearchStore.getState().isLoading).toBe(false)
})

it('shows the actionable backend queue timeout instead of telling users to change keywords', async () => {
  api.getRun.mockResolvedValue({ data: {
    run_id: 'run', status: 'failed', results: [],
    progress: { current_phase: 'failed', message: '搜索排队超时，请稍后重新检索' },
  } })
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  expect(await screen.findByText('搜索排队超时，请稍后重新检索')).toBeInTheDocument()
  expect(useSearchStore.getState().isLoading).toBe(false)
})

it('labels a queued search separately from query planning', async () => {
  api.getRun.mockResolvedValue({ data: {
    run_id: 'run', status: 'pending', results: [], progress: { current_phase: 'queued' },
  } })
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  expect(await screen.findByText('正在等待检索席位，尚未开始调用数据源...')).toBeInTheDocument()
})

it('does not restore a search when its creation finishes after leaving the page', async () => {
  let finish!: (value: any) => void
  api.create.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const view = render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce())
  view.unmount()
  await act(async () => { finish({ data: { run_id: 'stale' } }) })
  expect(api.getRun).not.toHaveBeenCalled()
  expect(useSearchStore.getState().searchRun).toBeNull()
  expect(useSearchStore.getState().isLoading).toBe(false)
})

it('keeps the latest selected paper when detail responses arrive out of order', async () => {
  let finishA!: (value: any) => void
  api.get.mockImplementation((id: string) => id === 'a'
    ? new Promise(resolve => { finishA = resolve })
    : Promise.resolve({ data: { id: 'b' } }))
  const view = render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  fireEvent.click(screen.getByText('Paper B'))
  await waitFor(() => expect(screen.getByTestId('detail').textContent).toBe('b'))
  await act(async () => { finishA({ data: { id: 'a' } }) })
  expect(screen.getByTestId('detail').textContent).toBe('b')
  view.unmount()
})

it('ignores a pending PDF-upload callback after leaving its search session', async () => {
  api.get.mockResolvedValue({ data: { id: 'a' } })
  const view = render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  const finishUpload = uploadCallback.current
  view.unmount()
  act(() => finishUpload())
  expect(api.analyze).not.toHaveBeenCalled()
  expect(useSearchStore.getState().analysisLoading).toBe(false)
})

it.each([true, false])('supersedes an old analysis after PDF replacement (old finishes first: %s)', async (oldFirst) => {
  api.get.mockResolvedValue({ data: { id: 'a' } })
  let oldFinish!: (value: any) => void
  let newFinish!: (value: any) => void
  api.analyze.mockReturnValueOnce(new Promise(resolve => { oldFinish = resolve }))
    .mockReturnValueOnce(new Promise(resolve => { newFinish = resolve }))
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  act(() => { analyzeCallback.current(); analyzeCallback.current() })
  expect(api.analyze).toHaveBeenCalledOnce()
  const oldSignal = api.analyze.mock.calls[0][2] as AbortSignal
  act(() => uploadCallback.current())
  expect(api.analyze).toHaveBeenCalledTimes(2)
  expect(oldSignal.aborted).toBe(true)
  if (oldFirst) {
    await act(async () => { oldFinish({ data: { summary: '旧摘要' } }) })
    expect(screen.getByTestId('busy')).toHaveTextContent('true')
    expect(screen.getByTestId('analysis')).not.toHaveTextContent('旧摘要')
    await act(async () => { newFinish({ data: { summary: '新全文' } }) })
  } else {
    await act(async () => { newFinish({ data: { summary: '新全文' } }) })
    await act(async () => { oldFinish({ data: { summary: '旧摘要' } }) })
  }
  expect(screen.getByTestId('analysis')).toHaveTextContent('新全文')
  expect(screen.getByTestId('busy')).toHaveTextContent('false')
})

it.each([
  ['switch', true], ['switch', false], ['close', true], ['close', false],
] as const)('invalidates uploaded paper after %s (old analysis already cached: %s)', async (action, cached) => {
  api.get.mockImplementation((id: string) => Promise.resolve({ data: { id } }))
  let oldFinish!: (value: any) => void
  api.analyze.mockReturnValueOnce(new Promise(resolve => { oldFinish = resolve }))
    .mockResolvedValueOnce({ data: { summary: '新全文' } })
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  fireEvent.click(screen.getByText('Analyze'))
  const oldSignal = api.analyze.mock.calls[0][2] as AbortSignal
  const finishUpload = uploadCallback.current
  if (cached) await act(async () => { oldFinish({ data: { summary: '旧材料' } }) })
  if (action === 'switch') {
    fireEvent.click(screen.getByText('Paper B'))
    await waitFor(() => expect(screen.getByTestId('detail')).toHaveTextContent('b'))
  } else {
    fireEvent.click(screen.getByText('Close detail'))
    expect(screen.queryByTestId('detail')).not.toBeInTheDocument()
  }
  act(() => finishUpload())
  expect(api.analyze).toHaveBeenCalledOnce()
  if (!cached) {
    expect(oldSignal.aborted).toBe(true)
    await act(async () => { oldFinish({ data: { summary: '旧材料' } }) })
  }
  fireEvent.click(screen.getByText('Paper A'))
  await waitFor(() => expect(screen.getByTestId('detail')).toHaveTextContent('a'))
  expect(screen.getByTestId('analysis')).toBeEmptyDOMElement()
  expect(screen.getByTestId('busy')).toHaveTextContent('false')
  fireEvent.click(screen.getByText('Analyze'))
  await waitFor(() => expect(screen.getByTestId('analysis')).toHaveTextContent('新全文'))
  expect(api.analyze).toHaveBeenCalledTimes(2)
})

it('reanalyzes the original paper if it is selected again before its upload finishes', async () => {
  api.get.mockImplementation((id: string) => Promise.resolve({ data: { id } }))
  api.analyze.mockResolvedValueOnce({ data: { summary: '旧材料' } })
    .mockResolvedValueOnce({ data: { summary: '新全文' } })
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  fireEvent.click(screen.getByText('Analyze'))
  await waitFor(() => expect(screen.getByTestId('analysis')).toHaveTextContent('旧材料'))
  const finishUpload = uploadCallback.current
  fireEvent.click(screen.getByText('Paper B'))
  await waitFor(() => expect(screen.getByTestId('detail')).toHaveTextContent('b'))
  fireEvent.click(screen.getByText('Paper A'))
  await waitFor(() => expect(screen.getByTestId('detail')).toHaveTextContent('a'))
  act(() => finishUpload())
  await waitFor(() => expect(screen.getByTestId('analysis')).toHaveTextContent('新全文'))
  expect(api.analyze).toHaveBeenCalledTimes(2)
})

it('keeps analysis and busy state with each paper while other requests finish', async () => {
  api.get.mockImplementation((id: string) => Promise.resolve({ data: { id } }))
  let finishA!: (value: any) => void
  let finishB!: (value: any) => void
  api.analyze.mockImplementation((id: string) => new Promise(resolve => {
    if (id === 'a') finishA = resolve
    else finishB = resolve
  }))
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  fireEvent.click(screen.getByText('Analyze'))
  fireEvent.click(screen.getByText('Paper B'))
  await waitFor(() => expect(screen.getByTestId('detail')).toHaveTextContent('b'))
  expect(screen.getByTestId('busy')).toHaveTextContent('false')
  fireEvent.click(screen.getByText('Analyze'))
  await act(async () => { finishA({ data: { summary: '分析 A' } }) })
  expect(screen.getByTestId('busy')).toHaveTextContent('true')
  expect(screen.getByTestId('analysis')).not.toHaveTextContent('分析 A')
  await act(async () => { finishB({ data: { summary: '分析 B' } }) })
  let showA!: (value: any) => void
  api.get.mockReturnValueOnce(new Promise(resolve => { showA = resolve }))
  fireEvent.click(screen.getByText('Paper A'))
  expect(screen.getByTestId('detail')).toHaveTextContent('b')
  expect(screen.getByTestId('analysis')).toHaveTextContent('分析 B')
  await act(async () => { showA({ data: { id: 'a' } }) })
  expect(screen.getByTestId('analysis')).toHaveTextContent('分析 A')
  expect(api.analyze).toHaveBeenCalledTimes(2)
})

it('allows the same keyword again and cancels analyses from the old search', async () => {
  api.get.mockResolvedValue({ data: { id: 'a' } })
  let finish!: (value: any) => void
  api.analyze.mockReturnValue(new Promise(resolve => { finish = resolve }))
  render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  fireEvent.click(screen.getByText('Analyze'))
  const signal = api.analyze.mock.calls[0][2] as AbortSignal
  fireEvent.submit(screen.getByRole('textbox').closest('form')!)
  await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
  expect(signal.aborted).toBe(true)
  await act(async () => { finish({ data: { summary: '上一轮的分析' } }) })
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  expect(screen.getByTestId('analysis')).toBeEmptyDOMElement()
  expect(screen.getByTestId('busy')).toHaveTextContent('false')
})

it('cancels a pending analysis when leaving and ignores a late completion', async () => {
  api.get.mockResolvedValue({ data: { id: 'a' } })
  let finish!: (value: any) => void
  api.analyze.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const view = render(<MemoryRouter initialEntries={['/search?q=traffic']}><Search /></MemoryRouter>)
  fireEvent.click(await screen.findByText('Paper A'))
  await screen.findByTestId('detail')
  fireEvent.click(screen.getByText('Analyze'))
  const signal = api.analyze.mock.calls[0][2] as AbortSignal
  view.unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { finish({ data: { summary: '过期分析' } }) })
  expect(useSearchStore.getState().analysis).toBeNull()
  expect(useSearchStore.getState().analysisLoading).toBe(false)
})

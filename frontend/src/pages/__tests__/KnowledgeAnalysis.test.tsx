import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import KnowledgeAnalysis from '../KnowledgeAnalysis'

const mocks = vi.hoisted(() => ({
  getCategories: vi.fn(), list: vi.fn(), aiAnalyze: vi.fn(),
  createRoute: vi.fn(), create: vi.fn(), success: vi.fn(), error: vi.fn(),
}))
vi.mock('@/api/client', () => ({ knowledgeApi: mocks }))
vi.mock('react-hot-toast', () => ({ default: { success: mocks.success, error: mocks.error } }))
vi.mock('@/stores/localeStore', () => ({
  useLocaleStore: () => ({ locale: 'zh', t: (key: string) => key }),
}))
vi.mock('@/components/AnalysisViz', () => ({
  default: ({ analysis }: { analysis: string }) => <p data-testid="analysis">{analysis}</p>,
}))

const analysis = '实验设计与比较指标。'.repeat(35) + '结尾事实必须保留。'
const result = {
  analysis, knowledge_count: 1, model_completed: true, fallback_used: false,
  provider: 'synthetic', model: 'offline-test', total_tokens: 12,
  prompt_tokens: 8, completion_tokens: 4, created_at: '2026-10-03T00:00:00Z',
}
const notes = (start: number, count: number) => Array.from({ length: count }, (_, offset) => ({
  id: `note-${start + offset}`, title: `保鲜实验笔记 ${start + offset}`,
  category: '食品保鲜', content: '模拟材料', research_points: [], tags: [],
}))

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  mocks.getCategories.mockResolvedValue({ data: [{ name: '食品保鲜', count: 21 }] })
  mocks.list.mockImplementation(async (_category, options) => ({ data: {
    items: options?.page === 2 ? notes(21, 1) : notes(1, 20), total: 21,
  } }))
  mocks.aiAnalyze.mockResolvedValue({ data: result })
  mocks.createRoute.mockResolvedValue({ data: { id: 'saved-route' } })
})

function mount() {
  return render(<MemoryRouter initialEntries={['/knowledge/analysis']}>
    <Routes>
      <Route path="/knowledge/analysis" element={<KnowledgeAnalysis />} />
      <Route path="/knowledge/route/saved-route" element={<p>已进入保存的路线详情</p>} />
      <Route path="/knowledge" element={<p>研究路线列表</p>} />
    </Routes>
  </MemoryRouter>)
}

async function chooseNote() {
  fireEvent.click(await screen.findByRole('button', { name: /食品保鲜/ }))
  fireEvent.click(await screen.findByRole('button', { name: '保鲜实验笔记 1' }))
}

async function analyze() {
  await chooseNote()
  fireEvent.click(screen.getByRole('button', { name: 'AI 分析研究方向' }))
  await screen.findByTestId('analysis')
}

it('loads beyond 20, sends the actual requirement and saves full text only after review', async () => {
  const view = mount()
  fireEvent.click(await screen.findByRole('button', { name: /食品保鲜/ }))
  expect(screen.getByRole('button', { name: 'AI 分析研究方向' })).toBeDisabled()
  fireEvent.click(await screen.findByRole('button', { name: /加载更多/ }))
  fireEvent.click(await screen.findByRole('button', { name: '保鲜实验笔记 21' }))
  expect(mocks.list).toHaveBeenLastCalledWith('食品保鲜', { page: 2, page_size: 20 })
  const query = '只比较保鲜方法，不涉及模型训练'
  fireEvent.change(screen.getByLabelText('研究目标与约束（可选）'), { target: { value: query } })
  fireEvent.click(screen.getByRole('button', { name: 'AI 分析研究方向' }))
  await screen.findByTestId('analysis')
  expect(mocks.aiAnalyze).toHaveBeenCalledWith(['note-21'], query)
  expect(mocks.createRoute).not.toHaveBeenCalled()
  expect(mocks.create).not.toHaveBeenCalled()
  expect(mocks.success).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '保存为研究路线' }))
  await screen.findByRole('button', { name: '查看已保存的研究路线' })
  expect(mocks.createRoute).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: '查看已保存的研究路线' }))
  expect(await screen.findByText('已进入保存的路线详情')).toBeInTheDocument()
  expect(mocks.createRoute).toHaveBeenCalledWith({
    title: '食品保鲜', description: `研究要求：${query}\n\n${analysis}`, knowledge_ids: ['note-21'],
  })
  view.unmount()
  mount()
  await screen.findByRole('button', { name: '查看已保存的研究路线' })
  expect(mocks.createRoute).toHaveBeenCalledOnce()
})

it('does not save fallback output as a completed AI research route', async () => {
  mocks.aiAnalyze.mockResolvedValue({ data: { ...result, model_completed: false } })
  mount()
  await analyze()
  expect(screen.getByRole('button', { name: '保存为研究路线' })).toBeDisabled()
  expect(screen.getByText('模型未完成分析，请重新分析后再保存研究路线。')).toBeInTheDocument()
  expect(mocks.createRoute).not.toHaveBeenCalled()
  expect(mocks.success).not.toHaveBeenCalled()
})

it('retains analysis and reports uncertain save without claiming success', async () => {
  mocks.createRoute.mockRejectedValue(new Error('synthetic unavailable'))
  mount()
  await analyze()
  fireEvent.click(screen.getByRole('button', { name: '保存为研究路线' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('保存结果未确认')
  expect(screen.getByTestId('analysis')).toHaveTextContent('结尾事实必须保留。')
  expect(mocks.success).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: '保存为研究路线' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '已核对且未找到，允许重新保存' }))
  expect(screen.getByRole('button', { name: '保存为研究路线' })).toBeEnabled()
  expect(mocks.createRoute).toHaveBeenCalledOnce()
})

it('never guesses source links for a legacy cached result', async () => {
  localStorage.setItem('scholar-analysis-result', JSON.stringify(result))
  mount()
  expect(await screen.findByTestId('analysis')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存为研究路线' })).toBeDisabled()
  expect(screen.getByText('旧结果没有保存来源关联，请重新选择知识并分析。')).toBeInTheDocument()
})

it('shows structured validation errors without crashing or losing the selection', async () => {
  mocks.aiAnalyze.mockRejectedValueOnce({ response: { data: { detail: [
    { loc: ['body', 'query'], msg: '请精简研究目标', type: 'string_too_long' },
  ] } } })
  mount()
  await chooseNote()
  fireEvent.click(screen.getByRole('button', { name: 'AI 分析研究方向' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('请精简研究目标')
  expect(screen.getByRole('button', { name: 'AI 分析研究方向' })).toBeEnabled()
  expect(screen.queryByTestId('analysis')).not.toBeInTheDocument()
  expect(mocks.createRoute).not.toHaveBeenCalled()
})

it('recovers from a category read error without selecting hidden notes', async () => {
  mocks.list.mockRejectedValueOnce(new Error('unavailable'))
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /食品保鲜/ }))
  fireEvent.click(await screen.findByRole('button', { name: '暂无条目，点击重新加载' }))
  await screen.findByRole('button', { name: '保鲜实验笔记 1' })
  expect(screen.getByRole('button', { name: 'AI 分析研究方向' })).toBeDisabled()
})

it('does not erase selections when another category is expanded', async () => {
  mocks.getCategories.mockResolvedValue({ data: [
    { name: '食品保鲜', count: 21 }, { name: '方法比较', count: 1 },
  ] })
  mocks.list.mockImplementation(async (category) => ({ data: {
    items: category === '方法比较' ? notes(22, 1) : notes(1, 20),
    total: category === '方法比较' ? 1 : 21,
  } }))
  mount()
  await chooseNote()
  fireEvent.click(screen.getByRole('button', { name: /方法比较/ }))
  fireEvent.click(await screen.findByRole('button', { name: '保鲜实验笔记 22' }))
  fireEvent.click(screen.getByRole('button', { name: 'AI 分析研究方向' }))
  await screen.findByTestId('analysis')
  expect(mocks.aiAnalyze).toHaveBeenCalledWith(['note-1', 'note-22'], undefined)
})

it('ignores a late analysis reply after leaving the page', async () => {
  let finish!: (value: unknown) => void
  mocks.aiAnalyze.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const view = mount()
  await chooseNote()
  fireEvent.click(screen.getByRole('button', { name: 'AI 分析研究方向' }))
  await waitFor(() => expect(mocks.aiAnalyze).toHaveBeenCalledOnce())
  expect(screen.getByLabelText('研究目标与约束（可选）')).toBeDisabled()
  view.unmount()
  await act(async () => { finish({ data: result }) })
  expect(localStorage.getItem('scholar-analysis-result')).toBeNull()
  expect(mocks.createRoute).not.toHaveBeenCalled()
})

it('retains an uncertain save after leaving and never silently creates a duplicate', async () => {
  let finish!: (value: unknown) => void
  mocks.createRoute.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const view = mount()
  await analyze()
  fireEvent.click(screen.getByRole('button', { name: '保存为研究路线' }))
  await waitFor(() => expect(mocks.createRoute).toHaveBeenCalledOnce())
  expect(JSON.parse(localStorage.getItem('scholar-analysis-result')!).saveUncertain).toBe(true)
  view.unmount()
  mount()
  expect(await screen.findByRole('status')).toHaveTextContent('上次保存结果尚未确认')
  expect(screen.getByRole('button', { name: '保存为研究路线' })).toBeDisabled()
  await act(async () => { finish({ data: { id: 'saved-route' } }) })
  expect(mocks.success).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '查看已保存的研究路线' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '保存为研究路线' }))
  expect(mocks.createRoute).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: '查看研究路线列表' }))
  expect(await screen.findByText('研究路线列表')).toBeInTheDocument()
})

it('does not send a route write if the local uncertainty marker cannot be persisted', async () => {
  mount()
  await analyze()
  const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('synthetic quota exceeded')
  })
  try {
    fireEvent.click(screen.getByRole('button', { name: '保存为研究路线' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('本次尚未创建路线')
    expect(mocks.createRoute).not.toHaveBeenCalled()
    expect(mocks.success).not.toHaveBeenCalled()
  } finally {
    storage.mockRestore()
  }
})

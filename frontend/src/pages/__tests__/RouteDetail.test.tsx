import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import RouteDetail from '../RouteDetail'

const mocks = vi.hoisted(() => ({
  getRoute: vi.fn(), get: vi.fn(), list: vi.fn(), stream: vi.fn(), success: vi.fn(), error: vi.fn(),
}))
vi.mock('@/api/client', () => ({ knowledgeApi: {
  getRoute: mocks.getRoute,
  get: mocks.get,
  list: mocks.list,
  generateRouteAnalysisStream: mocks.stream,
} }))
vi.mock('react-hot-toast', () => ({ default: { success: mocks.success, error: mocks.error } }))
vi.mock('@/stores/localeStore', () => ({
  useLocaleStore: () => ({ locale: 'zh', t: (key: string) => key }),
}))
vi.mock('@/components/AnalysisViz', () => ({ default: () => null }))

beforeEach(() => {
  vi.resetAllMocks()
  mocks.getRoute.mockResolvedValue({ data: {
    id: 'route-1', title: '测试路线', description: '', knowledge_ids: [],
    ai_analysis: '', status: 'active',
  } })
})

async function generateRoute() {
  render(<MemoryRouter initialEntries={['/knowledge/routes/route-1']}>
    <Routes><Route path="/knowledge/routes/:id" element={<RouteDetail />} /></Routes>
  </MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: 'knowledge.routeGenerate' }))
  await waitFor(() => expect(mocks.stream).toHaveBeenCalledOnce())
}

it('does not report success after an SSE error event', async () => {
  mocks.stream.mockImplementation(async (_id, onEvent) => {
    onEvent({ event: 'error', message: '保存路线失败' })
  })
  await generateRoute()
  await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('保存路线失败'))
  expect(mocks.success).not.toHaveBeenCalled()
  expect(mocks.getRoute).toHaveBeenCalledOnce()
})

it('does not report success when the stream closes without a done event', async () => {
  mocks.stream.mockImplementation(async (_id, onEvent) => {
    onEvent({ event: 'stage', stage: 'analysis', progress: 30 })
  })
  await generateRoute()
  await waitFor(() => expect(mocks.error).toHaveBeenCalledOnce())
  expect(mocks.success).not.toHaveBeenCalled()
  expect(mocks.getRoute).toHaveBeenCalledOnce()
})

it('reloads the saved route and reports success after a done event', async () => {
  mocks.stream.mockImplementation(async (_id, onEvent) => {
    onEvent({ event: 'stage', stage: 'diagram', data: { image_url: '/architecture.png' } })
    onEvent({ event: 'stage', stage: 'roadmap', data: { roadmap_url: '/roadmap.png' } })
    onEvent({ event: 'done', progress: 100 })
  })
  await generateRoute()
  await waitFor(() => expect(mocks.success).toHaveBeenCalledWith('分析生成成功'))
  expect(mocks.error).not.toHaveBeenCalled()
  expect(mocks.getRoute).toHaveBeenCalledTimes(2)
})

it.each([[true, false], [false, true], [false, false]])(
  'reports partial completion when image generation fails (diagram: %s, roadmap: %s)',
  async (diagramOk, roadmapOk) => {
    mocks.stream.mockImplementation(async (_id, onEvent) => {
      onEvent({ event: 'stage', stage: 'diagram', data: { image_url: diagramOk ? '/architecture.png' : '' } })
      onEvent({ event: 'stage', stage: 'roadmap', data: { roadmap_url: roadmapOk ? '/roadmap.png' : '' } })
      onEvent({ event: 'done', progress: 100 })
    })
    await generateRoute()
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('分析已保存，部分图像未生成，请查看结果后重试'))
    expect(mocks.success).not.toHaveBeenCalled()
    expect(mocks.getRoute).toHaveBeenCalledTimes(2)
  },
)

it('labels generic planning fallback even when both images were generated', async () => {
  mocks.stream.mockImplementation(async (_id, onEvent) => {
    onEvent({ event: 'stage', stage: 'diagram', data: { image_url: '/architecture.png', plan_source: 'rule_fallback' } })
    onEvent({ event: 'stage', stage: 'roadmap', data: { roadmap_url: '/roadmap.png', plan_source: 'model' } })
    onEvent({ event: 'done', progress: 100 })
  })
  await generateRoute()
  await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('图像已生成，但部分规划为规则回退，尚非完整定制方案'))
  expect(mocks.success).not.toHaveBeenCalled()
})

it('loads every linked ID directly even when it is outside the first knowledge page', async () => {
  const ids = Array.from({ length: 21 }, (_, index) => `older-${index}`)
  mocks.getRoute.mockResolvedValue({ data: {
    id: 'route-1', title: '旧路线', knowledge_ids: [...ids, ids[0]], ai_analysis: '', status: 'active',
  } })
  let active = 0
  let peak = 0
  mocks.get.mockImplementation(async (id: string) => {
    active += 1
    peak = Math.max(peak, active)
    await Promise.resolve()
    active -= 1
    return { data: { id, title: `关联资料 ${id}`, category: '食品', tags: [] } }
  })
  render(<MemoryRouter initialEntries={['/knowledge/routes/route-1']}>
    <Routes><Route path="/knowledge/routes/:id" element={<RouteDetail />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByText('关联资料 older-20')).toBeInTheDocument()
  expect(mocks.get).toHaveBeenCalledTimes(21)
  expect(mocks.list).not.toHaveBeenCalled()
  expect(peak).toBe(4)
  expect(active).toBe(0)
})

it('keeps available linked evidence and reports missing or failed entries', async () => {
  mocks.getRoute.mockResolvedValue({ data: {
    id: 'route-1', title: '旧路线', knowledge_ids: ['good', 'deleted'], ai_analysis: '', status: 'active',
  } })
  mocks.get.mockImplementation(async (id: string) => {
    if (id === 'deleted') throw new Error('404')
    return { data: { id, title: '有效关联材料', category: '食品', tags: [] } }
  })
  render(<MemoryRouter initialEntries={['/knowledge/routes/route-1']}>
    <Routes><Route path="/knowledge/routes/:id" element={<RouteDetail />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByText('有效关联材料')).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('1 条关联知识已删除或暂时无法读取')
  expect(screen.getByRole('button', { name: 'common.retry' })).toBeInTheDocument()
})

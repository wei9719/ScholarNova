import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import Knowledge from '../Knowledge'

const mocks = vi.hoisted(() => ({ list: vi.fn(), listRoutes: vi.fn(), delete: vi.fn() }))
vi.mock('@/api/client', () => ({ knowledgeApi: mocks }))
vi.mock('@/stores/localeStore', () => ({
  useLocaleStore: () => ({ locale: 'zh', t: (key: string) => key }),
}))
vi.mock('@/components/KnowledgeDetail/KnowledgeDetail', () => ({ default: () => null }))
vi.mock('@/components/KnowledgeForm/KnowledgeForm', () => ({ default: () => null }))

const entries = Array.from({ length: 21 }, (_, index) => ({
  id: `knowledge-${index + 1}`, title: `研究条目${index + 1}`, content: '正文',
  category: '食品', tags: [], research_points: [], created_at: '2026-01-01',
}))
const categories = [{ name: '食品', count: 21 }, { name: '交通', count: 1 }]

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  useKnowledgeStore.setState({
    items: [], total: 0, categories: [], selectedCategory: null, searchQuery: '',
    routes: [], selectedItem: null, detailOpen: false, formOpen: false,
  })
  mocks.listRoutes.mockResolvedValue({ data: { items: [], total: 0 } })
  mocks.list.mockImplementation(async (_category, options) => {
    const matches = options.keyword ? entries.filter(item => item.title === options.keyword) : entries
    const offset = ((options.page || 1) - 1) * options.page_size
    return { data: { items: matches.slice(offset, offset + options.page_size), total: matches.length, categories } }
  })
})

afterEach(cleanup)

function showKnowledge() {
  return render(<MemoryRouter><Knowledge /></MemoryRouter>)
}

it('provides a second page instead of silently hiding the twenty-first entry', async () => {
  showKnowledge()
  expect(await screen.findByText('研究条目20')).toBeInTheDocument()
  expect(screen.queryByText('研究条目21')).not.toBeInTheDocument()
  expect(screen.getByText('共 21 条 · 第 1 / 2 页')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  expect(await screen.findByText('研究条目21')).toBeInTheDocument()
  expect(mocks.list).toHaveBeenLastCalledWith(undefined, { page: 2, page_size: 20 })
  expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '上一页' }))
  expect(await screen.findByText('研究条目1')).toBeInTheDocument()
})

it('does not reset fast pagination on mount or an unchanged trimmed keyword', async () => {
  vi.useFakeTimers()
  try {
    showKnowledge()
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('研究条目20')).toBeInTheDocument()
    // No time has elapsed: navigate before the old 250 ms mount timer fired.
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页' })) })
    expect(screen.getByText('研究条目21')).toBeInTheDocument()
    await act(async () => { vi.advanceTimersByTime(250) })
    expect(screen.getByText('研究条目21')).toBeInTheDocument()
    expect(mocks.list).toHaveBeenCalledTimes(2)

    fireEvent.change(screen.getByPlaceholderText('搜索全部知识的标题和正文'), { target: { value: '   ' } })
    await act(async () => { vi.advanceTimersByTime(250) })
    expect(screen.getByText('共 21 条 · 第 2 / 2 页')).toBeInTheDocument()
    expect(mocks.list).toHaveBeenCalledTimes(2)
  } finally {
    vi.useRealTimers()
  }
})

it('searches on the server across the library and resets the page', async () => {
  showKnowledge()
  await screen.findByText('研究条目20')
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByText('研究条目21')
  fireEvent.change(screen.getByPlaceholderText('搜索全部知识的标题和正文'), { target: { value: '研究条目21' } })
  await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith(undefined, {
    page: 1, page_size: 20, keyword: '研究条目21',
  }))
  expect(await screen.findByText('共 1 条 · 第 1 / 1 页')).toBeInTheDocument()
  expect(screen.getByText('研究条目21')).toBeInTheDocument()
})

it('resets pagination when choosing another category', async () => {
  showKnowledge()
  await screen.findByText('研究条目20')
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByText('研究条目21')
  fireEvent.click(screen.getByRole('button', { name: '交通 1' }))
  await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith('交通', { page: 1, page_size: 20 }))
})

it('does not expose the unsafe partial category deletion as an available action', async () => {
  showKnowledge()
  await screen.findByText('研究条目20')
  expect(screen.getByText('分类批量删除暂不可用，请在条目详情逐条确认删除。')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /删除分类/ })).not.toBeInTheDocument()
  expect(mocks.delete).not.toHaveBeenCalled()
})

it('ignores an old category response after the selected scope changes', async () => {
  let resolveOld!: (value: unknown) => void
  mocks.list.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
  useKnowledgeStore.setState({ categories })
  showKnowledge()
  fireEvent.click(screen.getByRole('button', { name: '交通 1' }))
  await screen.findByText('研究条目20')
  await act(async () => resolveOld({ data: {
    items: [{ ...entries[0], title: '过期响应' }], total: 1, categories,
  } }))
  expect(screen.queryByText('过期响应')).not.toBeInTheDocument()
  expect(useKnowledgeStore.getState().items).toHaveLength(20)
})

it('moves to the final valid page when entries are removed between reads', async () => {
  mocks.list.mockImplementation(async (_category, options) => ({ data: {
    items: options.page === 2 ? [] : entries.slice(0, 20),
    total: options.page === 2 ? 20 : 21, categories,
  } }))
  showKnowledge()
  await screen.findByText('研究条目20')
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(3))
  expect(mocks.list).toHaveBeenLastCalledWith(undefined, { page: 1, page_size: 20 })
})

it('provides an independent second page for the twenty-first research route', async () => {
  const routes = Array.from({ length: 21 }, (_, index) => ({ id: `r${index}`, title: `研究路线${index + 1}` }))
  mocks.listRoutes.mockImplementation(async ({ page, page_size }) => ({ data: {
    items: routes.slice((page - 1) * page_size, page * page_size), total: routes.length,
  } }))
  showKnowledge()
  expect(await screen.findByText('研究路线20')).toBeInTheDocument()
  expect(screen.queryByText('研究路线21')).not.toBeInTheDocument()
  expect(screen.getByText('共 21 条路线 · 第 1 / 2 页')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '下一页路线' }))
  expect(await screen.findByText('研究路线21')).toBeInTheDocument()
  expect(mocks.listRoutes).toHaveBeenLastCalledWith({ page: 2, page_size: 20 })
  expect(screen.getByRole('button', { name: '下一页路线' })).toBeDisabled()
  expect(mocks.list).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: '上一页路线' }))
  expect(await screen.findByText('研究路线1')).toBeInTheDocument()
})

it('reports route loading failure instead of a successful empty list and permits retry', async () => {
  mocks.listRoutes.mockRejectedValueOnce(new Error('offline'))
  mocks.listRoutes.mockResolvedValue({ data: { items: [{ id: 'r1', title: '恢复后的路线' }], total: 1 } })
  showKnowledge()
  expect(await screen.findByRole('alert')).toHaveTextContent('研究路线加载失败，尚无法确认列表。')
  expect(screen.queryByText('common.noData')).not.toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '研究路线分页' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '重试加载研究路线' }))
  expect(await screen.findByText('恢复后的路线')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(mocks.listRoutes).toHaveBeenCalledTimes(2)
})

it('ignores a route response that arrives after leaving the knowledge page', async () => {
  let finish!: (value: unknown) => void
  mocks.listRoutes.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const view = showKnowledge()
  await waitFor(() => expect(mocks.listRoutes).toHaveBeenCalledOnce())
  view.unmount()
  await act(async () => finish({ data: { items: [{ id: 'stale', title: '迟到路线' }], total: 1 } }))
  expect(useKnowledgeStore.getState().routes).toEqual([])
})

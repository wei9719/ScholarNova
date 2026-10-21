import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Knowledge from '../Knowledge'
import KnowledgeAnalysis from '../KnowledgeAnalysis'
import ResearchAssistant from '../ResearchAssistant'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import { useAssistantStore } from '@/stores/assistantStore'

const mocks = vi.hoisted(() => ({
  list: vi.fn(), listRoutes: vi.fn(), getCategories: vi.fn(), aiAnalyze: vi.fn(),
  createRoute: vi.fn(), chat: vi.fn(), status: vi.fn(),
}))
vi.mock('@/api/client', () => ({
  knowledgeApi: mocks, agentApi: { chat: mocks.chat }, zoteroApi: { status: mocks.status },
}))
vi.mock('@/stores/localeStore', () => ({ useLocaleStore: () => ({ locale: 'zh', t: (key: string) => key }) }))
vi.mock('@/components/KnowledgeForm/KnowledgeForm', () => ({ default: () => null }))
vi.mock('@/components/AnalysisViz', () => ({ default: () => null }))
vi.mock('@/components/PaperReadingWorkspace/PaperReadingWorkspace', () => ({
  default: ({ paperId, initialCategory }: { paperId: string; initialCategory: string }) =>
    <p>本地原文：{paperId}；研究分类：{initialCategory}</p>,
}))

const note = {
  id: 'traffic-note', title: '交通流实验与个人笔记', category: '交通修复', content: '已保存的研究材料',
  source_paper_id: 'paper/traffic', source_paper_title: '交通流论文', source_paper_doi: null,
  research_points: [], tags: [], notes: '', created_at: '2026-10-17', updated_at: '2026-10-17',
}
const categories = [{ name: '交通修复', count: 1 }, { name: '食品研究', count: 1 }]

beforeEach(() => {
  vi.clearAllMocks()
  useAssistantStore.persist.clearStorage()
  localStorage.clear()
  HTMLElement.prototype.scrollIntoView = vi.fn()
  useKnowledgeStore.setState({ items: [], total: 0, categories: [], selectedCategory: '交通修复',
    searchQuery: '', selectedItem: null, detailOpen: false, formOpen: false, editingItem: null, formPrefill: null, routes: [] })
  useAssistantStore.setState({ folders: [], activeConversationId: 'food-chat', conversations: [{
    id: 'food-chat', folderId: null, title: '食品旧对话', knowledgeCategory: '食品研究', scopeRevision: 0,
    messages: [{ id: 'food-question', role: 'user', content: '食品保鲜问题', knowledgeCategory: '食品研究', scopeRevision: 0 }],
    createdAt: 1, updatedAt: 1,
  }] })
  mocks.getCategories.mockResolvedValue({ data: categories })
  mocks.list.mockResolvedValue({ data: { items: [note], total: 1, categories } })
  mocks.listRoutes.mockResolvedValue({ data: { items: [], total: 0 } })
  mocks.status.mockResolvedValue({ data: { connected: true } })
})
afterEach(cleanup)

function Location() {
  const location = useLocation()
  return <><output aria-label="当前地址">{location.pathname}{location.search}</output>
    <output aria-label="入口状态">{JSON.stringify(location.state)}</output></>
}

function mount() {
  render(<MemoryRouter initialEntries={['/knowledge']}>
    <Location />
    <Routes>
      <Route path="/knowledge" element={<Knowledge />} />
      <Route path="/assistant" element={<ResearchAssistant />} />
      <Route path="/knowledge/analysis" element={<KnowledgeAnalysis />} />
    </Routes>
  </MemoryRouter>)
}

it('continues a saved note in its local paper reader with the same category, without a model call', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /交通流实验与个人笔记/ }))
  fireEvent.click(screen.getByRole('button', { name: '继续阅读原文' }))
  expect(await screen.findByText('本地原文：paper/traffic；研究分类：交通修复')).toBeInTheDocument()
  expect(mocks.chat).not.toHaveBeenCalled()
  expect(mocks.aiAnalyze).not.toHaveBeenCalled()
})

it('starts a fresh category-scoped conversation without changing old-direction history or auto-sending', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /交通流实验与个人笔记/ }))
  fireEvent.click(screen.getByRole('button', { name: '在此分类中提问' }))
  expect(await screen.findByRole('combobox', { name: '知识库资料分类' })).toHaveValue('交通修复')
  const state = useAssistantStore.getState()
  expect(state.activeConversationId).not.toBe('food-chat')
  expect(state.conversations.find(item => item.id === state.activeConversationId)).toMatchObject({
    knowledgeCategory: '交通修复', messages: [],
  })
  expect(state.conversations.find(item => item.id === 'food-chat')?.messages[0].content).toBe('食品保鲜问题')
  expect(screen.queryByText('食品保鲜问题')).not.toBeInTheDocument()
  expect(mocks.chat).not.toHaveBeenCalled()
})

it('opens the current research category for planning instead of showing an unrelated cached result', async () => {
  const old = JSON.stringify({ result: { analysis: '旧食品方向分析', model_completed: true }, knowledgeIds: ['food-note'] })
  localStorage.setItem('scholar-analysis-result', old)
  mount()
  await screen.findByText(note.title)
  fireEvent.click(screen.getByRole('button', { name: 'knowledge.aiAnalysis' }))
  await waitFor(() => expect(mocks.list).toHaveBeenCalledWith('交通修复', { page: 1, page_size: 20 }))
  expect(screen.queryByText('旧食品方向分析')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /交通流实验与个人笔记/ })).toBeInTheDocument()
  expect(localStorage.getItem('scholar-analysis-result')).toBe(old)
  expect(mocks.aiAnalyze).not.toHaveBeenCalled()
  expect(mocks.createRoute).not.toHaveBeenCalled()
})

it.each(['在此分类中提问', 'knowledge.aiAnalysis'])('keeps the stored category identity for %s', async (action) => {
  const category = ' 交通修复 '
  const spacedNote = { ...note, category }
  mocks.list.mockResolvedValue({ data: { items: [spacedNote], total: 1, categories: [{ name: category, count: 1 }] } })
  mocks.getCategories.mockResolvedValue({ data: [{ name: category, count: 1 }] })
  useKnowledgeStore.setState({ selectedCategory: category })
  mount()
  await screen.findByText(note.title)
  if (action === '在此分类中提问') {
    fireEvent.click(screen.getByRole('button', { name: /交通流实验与个人笔记/ }))
    fireEvent.click(screen.getByRole('button', { name: action }))
    expect(await screen.findByRole('combobox', { name: '知识库资料分类' })).toHaveValue(category)
  } else {
    fireEvent.click(screen.getByRole('button', { name: action }))
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith(category, { page: 1, page_size: 20 }))
    expect(JSON.parse(screen.getByLabelText('入口状态').textContent || 'null')).toMatchObject({ category })
  }
})

it('retains the category handoff across refresh before a new analysis exists', async () => {
  const old = JSON.stringify({ result: { analysis: '旧食品方向分析', model_completed: true }, knowledgeIds: ['food-note'] })
  localStorage.setItem('scholar-analysis-result', old)
  mount()
  await screen.findByText(note.title)
  fireEvent.click(screen.getByRole('button', { name: 'knowledge.aiAnalysis' }))
  await screen.findByText(/从研究分类/)
  const entry = JSON.parse(screen.getByLabelText('入口状态').textContent || 'null')
  expect(entry).toEqual({ startNewAnalysis: true, category: '交通修复' })
  cleanup()
  render(<MemoryRouter initialEntries={[{ pathname: '/knowledge/analysis', state: entry }]}><KnowledgeAnalysis /></MemoryRouter>)
  await screen.findByText(/从研究分类/)
  expect(screen.queryByText('旧食品方向分析')).not.toBeInTheDocument()
  expect(localStorage.getItem('scholar-analysis-result')).toBe(old)
  expect(mocks.aiAnalyze).not.toHaveBeenCalled()
})

it('consumes the handoff only after a replacement analysis is persisted', async () => {
  localStorage.setItem('scholar-analysis-result', JSON.stringify({ result: { analysis: '旧食品方向分析', model_completed: true } }))
  mocks.aiAnalyze.mockResolvedValue({ data: { analysis: '新的交通流实验计划', model_completed: true, knowledge_count: 1 } })
  mount()
  await screen.findByText(note.title)
  fireEvent.click(screen.getByRole('button', { name: 'knowledge.aiAnalysis' }))
  fireEvent.click(await screen.findByRole('button', { name: note.title }))
  fireEvent.click(screen.getByRole('button', { name: 'AI 分析研究方向' }))
  await waitFor(() => expect(screen.getByLabelText('入口状态')).toHaveTextContent('null'))
  expect(JSON.parse(localStorage.getItem('scholar-analysis-result') || 'null')).toMatchObject({
    result: { analysis: '新的交通流实验计划' }, knowledgeIds: [note.id],
  })
  expect(mocks.createRoute).not.toHaveBeenCalled()
})

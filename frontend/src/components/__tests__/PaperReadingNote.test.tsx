import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { KnowledgeItem, PaperDetail as Paper } from '@/api/types'
import { useLocaleStore } from '@/stores/localeStore'
import { readingNote } from '@/utils/studyNote'
import PaperDetail from '../PaperDetail/PaperDetail'

const api = vi.hoisted(() => ({
  fulltextStatus: vi.fn(), getCategories: vi.fn(), list: vi.fn(), create: vi.fn(), update: vi.fn(),
  form: vi.fn(), toastError: vi.fn(),
}))
vi.mock('@/api/client', () => ({ papersApi: api, knowledgeApi: api, zoteroApi: {} }))
vi.mock('react-hot-toast', () => ({ default: { error: api.toastError, success: vi.fn() } }))
vi.mock('@/components/KnowledgeForm/KnowledgeForm', () => ({
  default: (props: { open: boolean; editingItem: KnowledgeItem | null; prefill: Partial<KnowledgeItem> | null; onClose: () => void }) => {
    api.form(props)
    return props.open ? <div role="dialog" aria-label="阅读笔记草稿"><button onClick={props.onClose}>关闭草稿</button></div> : null
  },
}))

const paper: Paper = {
  id: 'paper-A', title: '论文 A', authors: ['Researcher'], abstract: '摘要原文，非模型分析。', year: 2026,
  venue: null, citation_count: 0, doi: '10.test/paper-A', url: null, pdf_url: null,
  source: 'crossref', relevance_score: null, is_open_access: false, references: [], citations: [],
  fields_of_study: [], keywords: ['笔记'], publication_date: null, volume: null, issue: null, pages: null,
}
const callbacks = { onClose: vi.fn(), onAnalyze: vi.fn(), onFulltextUploaded: vi.fn() }
function panel(category = '研究 A', selectedPaper = paper) {
  return <PaperDetail paper={selectedPaper} analysis={null} analysisLoading={false} evidenceSpans={[]}
    evidenceLoading={false} researchCategory={category} sourceQuery="原始研究问题" {...callbacks} />
}
function savedNote(category = '研究 A'): KnowledgeItem {
  const data = readingNote()
  data.sections.math = { paper_notes: '$x^2$', ai_suggestions: '既有建议', user_notes: '不能被覆盖的推导' }
  return {
    id: `note-${category}`, title: '已保存的阅读笔记', category, content: '既有正文', card_type: 'reading_note', card_data: data,
    source_paper_id: paper.id, source_paper_title: paper.title, source_paper_doi: paper.doi, research_points: [], tags: [],
    notes: '既有个人备注', created_at: '2026-01-01', updated_at: '2026-01-02T00:00:00.123456',
  }
}
const formProps = () => api.form.mock.calls[api.form.mock.calls.length - 1][0]
const openNotes = () => fireEvent.click(screen.getByRole('button', { name: '阅读笔记 / 继续编辑', exact: true }))

beforeEach(() => {
  vi.resetAllMocks()
  useLocaleStore.getState().setLocale('zh')
  api.fulltextStatus.mockResolvedValue({ data: { available: false, source: null, file_size: 0 } })
  api.getCategories.mockResolvedValue({ data: [{ name: '研究 A', count: 1 }, { name: '研究 B', count: 1 }] })
  api.list.mockResolvedValue({ data: { items: [], total: 0, categories: [] } })
})
afterEach(() => {
  cleanup()
  useLocaleStore.persist.clearStorage()
  expect(api.create).not.toHaveBeenCalled()
  expect(api.update).not.toHaveBeenCalled()
})

it('opens a manually editable reading note without requiring analysis or a model call', async () => {
  render(panel())
  openNotes()
  await screen.findByRole('dialog', { name: '阅读笔记草稿' })
  expect(api.list).toHaveBeenCalledWith('研究 A', { card_type: 'reading_note', source_paper_id: paper.id, page_size: 1 })
  expect(formProps().editingItem).toBeNull()
  expect(formProps().prefill).toMatchObject({
    category: '研究 A', card_type: 'reading_note', source_paper_id: paper.id,
    card_data: { status: 'reading', source_query: '原始研究问题', sections: { overview: {
      paper_notes: paper.abstract, ai_suggestions: '', user_notes: '',
    } } },
  })
  expect(callbacks.onAnalyze).not.toHaveBeenCalled()
})

it('reopens the existing paper-and-category note for editing without replacing its saved work', async () => {
  const existing = savedNote()
  api.list.mockResolvedValue({ data: { items: [existing], total: 1, categories: [] } })
  render(panel())
  openNotes()
  await screen.findByRole('dialog')
  expect(formProps().editingItem).toEqual(existing)
  expect(formProps().editingItem.card_data.sections.math.user_notes).toBe('不能被覆盖的推导')
  expect(formProps().editingItem.updated_at).toBe(existing.updated_at)
})

it('checks a different category before opening a new draft for the same paper', async () => {
  const existing = savedNote()
  api.list.mockImplementation(async (category) => ({ data: { items: category === '研究 A' ? [existing] : [], total: category === '研究 A' ? 1 : 0 } }))
  const view = render(panel())
  openNotes()
  await screen.findByRole('dialog')
  fireEvent.click(screen.getByRole('button', { name: '关闭草稿' }))
  view.rerender(panel('研究 B'))
  openNotes()
  await screen.findByRole('dialog')
  expect(api.list).toHaveBeenLastCalledWith('研究 B', { card_type: 'reading_note', source_paper_id: paper.id, page_size: 1 })
  expect(formProps().editingItem).toBeNull()
  expect(formProps().prefill.category).toBe('研究 B')
})

it.each(['list', 'getCategories'] as const)('does not treat failed %s reads as absence and create a replacement', async (method) => {
  api[method].mockRejectedValueOnce(new Error('offline'))
  render(panel())
  openNotes()
  await waitFor(() => expect(api.toastError).toHaveBeenCalledOnce())
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(formProps().open).toBe(false)
  expect(screen.getByRole('button', { name: '阅读笔记 / 继续编辑', exact: true })).toBeEnabled()
})

it.each(['', '   '])('uses the exact uncategorized scope for empty research category %j', async (category) => {
  render(panel(category))
  openNotes()
  await screen.findByRole('dialog')
  expect(api.list).toHaveBeenCalledWith('未分类', { card_type: 'reading_note', source_paper_id: paper.id, page_size: 1 })
  expect(formProps().prefill.category).toBe('未分类')
})

it('discards a late notebook lookup after switching to another paper', async () => {
  let finish!: (value: unknown) => void
  api.list.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const view = render(panel())
  openNotes()
  expect(api.list).toHaveBeenCalledOnce()
  const second = { ...paper, id: 'paper-B', title: '论文 B' }
  view.rerender(panel('研究 A', second))
  await act(async () => finish({ data: { items: [savedNote()], total: 1 } }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(formProps().open).toBe(false)
  openNotes()
  await screen.findByRole('dialog')
  expect(formProps().prefill.source_paper_id).toBe('paper-B')
  expect(formProps().editingItem).toBeNull()
})

it('ignores a note lookup after the paper detail has unmounted', async () => {
  let finish!: (value: unknown) => void
  api.list.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const view = render(panel())
  openNotes()
  view.unmount()
  const calls = api.form.mock.calls.length
  await act(async () => finish({ data: { items: [savedNote()], total: 1 } }))
  expect(api.form).toHaveBeenCalledTimes(calls)
})

it('keeps the new category notebook when a same-paper old-category lookup finishes late', async () => {
  let finishOld!: (value: unknown) => void
  api.list.mockReturnValueOnce(new Promise(resolve => { finishOld = resolve }))
    .mockResolvedValueOnce({ data: { items: [savedNote('研究 B')], total: 1 } })
  const view = render(panel('研究 A'))
  openNotes()
  view.rerender(panel('研究 B'))
  expect(screen.getByRole('button', { name: '阅读笔记 / 继续编辑', exact: true })).toBeEnabled()
  openNotes()
  await screen.findByRole('dialog')
  expect(formProps().editingItem.category).toBe('研究 B')
  await act(async () => finishOld({ data: { items: [savedNote('研究 A')], total: 1 } }))
  expect(formProps().editingItem.category).toBe('研究 B')
})

it('rejects an old request even after navigating away and back to the same paper and category', async () => {
  let finishOld!: (value: unknown) => void
  api.list.mockReturnValueOnce(new Promise(resolve => { finishOld = resolve }))
    .mockResolvedValueOnce({ data: { items: [{ ...savedNote(), title: '重新读取的新版本' }], total: 1 } })
  const view = render(panel())
  openNotes()
  view.rerender(panel('研究 B'))
  view.rerender(panel())
  openNotes()
  await screen.findByRole('dialog')
  await act(async () => finishOld({ data: { items: [savedNote()], total: 1 } }))
  expect(formProps().editingItem.title).toBe('重新读取的新版本')
})

it('does not leave the previous paper notebook open when the selected paper changes', async () => {
  const view = render(panel())
  openNotes()
  await screen.findByRole('dialog')
  await act(async () => {
    view.rerender(panel('研究 A', { ...paper, id: 'paper-B', title: '论文 B' }))
  })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(formProps().open).toBe(false)
})

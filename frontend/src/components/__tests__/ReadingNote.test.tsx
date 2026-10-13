import { StrictMode, useState } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import KnowledgeForm from '../KnowledgeForm/KnowledgeForm'
import StudyNoteEditor from '../KnowledgeForm/StudyNoteEditor'
import { useLocaleStore } from '@/stores/localeStore'
import { readingNote } from '@/utils/studyNote'
import type { KnowledgeItem } from '@/api/types'

const api = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), draft: vi.fn() }))
vi.mock('@/api/client', () => ({ knowledgeApi: api, studyApi: api }))
const note: KnowledgeItem = { id: 'note', title: '交通流修复', category: '交通', content: '原始内容', card_type: 'reading_note', card_data: readingNote(), source_paper_id: 'paper', source_paper_title: 'Paper', source_paper_doi: null, research_points: [], tags: [], notes: null, created_at: '2026-10-13T01:00:00', updated_at: '2026-10-13T01:00:00.123456' }
const props = { open: true, editingItem: null, prefill: note, categories: [{ name: '交通', count: 1 }], onClose: vi.fn(), onSuccess: vi.fn() }
beforeEach(() => { vi.resetAllMocks(); useLocaleStore.getState().setLocale('zh'); api.create.mockResolvedValue({ data: note }); api.update.mockResolvedValue({ data: note }) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('preserves chapter edits across tabs and same-paper prefill rerenders, saving without condensation', async () => {
  const { rerender } = render(<KnowledgeForm {...props} />)
  fireEvent.change(screen.getByLabelText('我的想法'), { target: { value: '我的实验假设' } })
  fireEvent.click(screen.getByRole('button', { name: /公式与推导/ }))
  fireEvent.change(screen.getByLabelText('论文材料摘录'), { target: { value: '$x^2$\n'.repeat(1000) } })
  rerender(<KnowledgeForm {...props} prefill={{ ...note }} />)
  fireEvent.click(screen.getByRole('button', { name: /论文思路/ }))
  expect(screen.getByLabelText('我的想法')).toHaveValue('我的实验假设')
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce())
  expect(api.create.mock.calls[0][0]).toMatchObject({ auto_polish: false, card_type: 'reading_note', card_data: { sections: { overview: { user_notes: '我的实验假设' }, math: { paper_notes: '$x^2$\n'.repeat(1000) } } } })
})

it('warns only for changed fields in StrictMode, and keeps edits after a 409 conflict', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const close = vi.fn()
  render(<StrictMode><KnowledgeForm {...props} editingItem={note} onClose={close} /></StrictMode>)
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(confirm).not.toHaveBeenCalled(); expect(close).toHaveBeenCalledOnce()
  fireEvent.change(screen.getByLabelText('我的想法'), { target: { value: '保留我的修改' } })
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(confirm).toHaveBeenCalledOnce(); expect(close).toHaveBeenCalledOnce()
  api.update.mockRejectedValue({ response: { status: 409, data: { detail: '笔记已在其他窗口更新' } } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.update).toHaveBeenCalledOnce())
  expect(api.update.mock.calls[0][1].expected_updated_at).toBe(note.updated_at)
  expect(screen.getByLabelText('我的想法')).toHaveValue('保留我的修改')
  expect(props.onSuccess).not.toHaveBeenCalled()
})

it('saves ordinary long-form notes unchanged unless condensation is explicitly chosen', async () => {
  render(<KnowledgeForm {...props} prefill={{ ...note, card_type: null, card_data: null }} />)
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce())
  expect(api.create.mock.calls[0][0]).toMatchObject({ content: '原始内容', auto_polish: false })
})

it('retains prose written while temporarily switching out of the structured notebook', async () => {
  render(<KnowledgeForm {...props} />)
  fireEvent.change(screen.getByLabelText('我的想法'), { target: { value: '已有记录' } })
  fireEvent.change(screen.getByLabelText('资料类型'), { target: { value: '' } })
  fireEvent.change(screen.getByLabelText('正文'), { target: { value: '切换后的补充内容' } })
  fireEvent.change(screen.getByLabelText('资料类型'), { target: { value: 'reading_note' } })
  const value = (screen.getByLabelText('我的想法') as HTMLTextAreaElement).value
  expect(value).toContain('已有记录'); expect(value).toContain('切换后的补充内容')
})

function Editor() { const [value, setValue] = useState(readingNote()); return <StudyNoteEditor value={value} onChange={setValue} paperId="paper" chinese /> }
const draft = { section: 'overview', content: '依据材料的建议 [S1]', material_scope: 'abstract', sources: [{ label: 'S1', id: 'source', content_hash: 'hash', section: '摘要', page: null, excerpt: '材料原文' }], model_completed: true, provider: 'test', model: 'test', total_tokens: 10, warnings: [] }
it('does not append model suggestions before explicit confirmation and preserves source snapshots', async () => {
  api.draft.mockResolvedValue({ data: draft })
  render(<Editor />)
  fireEvent.click(screen.getByRole('button', { name: '生成本节草稿' }))
  await screen.findByLabelText('待确认草稿')
  expect(screen.getByLabelText('AI 建议')).toHaveValue('')
  fireEvent.click(screen.getByRole('button', { name: '确认追加到 AI 建议栏' }))
  expect((screen.getByLabelText('AI 建议') as HTMLTextAreaElement).value).toContain('材料原文')
  expect(screen.getByLabelText('我的想法')).toHaveValue('')
})
it('does not mislabel failed model calls as local extraction, or let them be appended', async () => {
  api.draft.mockResolvedValue({ data: { ...draft, model_completed: false, warnings: ['模型超时'] } })
  render(<Editor />)
  fireEvent.click(screen.getByRole('button', { name: '生成本节草稿' }))
  await screen.findByText('模型超时')
  expect(screen.queryByText(/本地提取，无模型调用/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认追加到 AI 建议栏' })).toBeDisabled()
})
it('ignores a late response after switching notebook chapters', async () => {
  let finish!: (value: unknown) => void
  api.draft.mockReturnValue(new Promise(resolve => { finish = resolve }))
  render(<Editor />)
  fireEvent.click(screen.getByRole('button', { name: '生成本节草稿' }))
  fireEvent.click(screen.getByRole('button', { name: '实验复现' }))
  await act(async () => { finish({ data: draft }) })
  expect(screen.queryByLabelText('待确认草稿')).not.toBeInTheDocument()
  expect(screen.getByLabelText('AI 建议')).toHaveValue('')
})
it('places deterministic resource extraction in material notes, not AI suggestions', async () => {
  api.draft.mockResolvedValue({ data: { ...draft, section: 'resources', model_completed: false, total_tokens: 0 } })
  render(<Editor />)
  fireEvent.click(screen.getByRole('button', { name: '源码、数据与联系' }))
  fireEvent.click(screen.getByRole('button', { name: '提取材料中的资源' }))
  await screen.findByLabelText('待确认草稿')
  fireEvent.click(screen.getByRole('button', { name: '确认追加到论文材料栏' }))
  expect((screen.getByLabelText('论文材料摘录') as HTMLTextAreaElement).value).toContain('材料原文')
  expect(screen.getByLabelText('AI 建议')).toHaveValue('')
})

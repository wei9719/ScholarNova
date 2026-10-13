import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { KnowledgeItem } from '@/api/types'
import { readingNote, studySections } from '@/utils/studyNote'
import KnowledgeDetail from '../KnowledgeDetail/KnowledgeDetail'

const mocks = vi.hoisted(() => ({ delete: vi.fn(), download: vi.fn(), create: vi.fn(), update: vi.fn() }))
vi.mock('@/api/client', () => ({ knowledgeApi: mocks }))
vi.mock('@/stores/localeStore', () => ({ useLocaleStore: () => ({ locale: 'zh', t: (key: string) => key }) }))
vi.mock('@/utils/studyNote', async (original) => ({
  ...await original<typeof import('@/utils/studyNote')>(), downloadKnowledge: mocks.download,
}))

function item(overrides: Partial<KnowledgeItem> = {}): KnowledgeItem {
  const note = readingNote()
  note.status = 'reading'
  note.source_query = '原始检索问题'
  for (const { key } of studySections) note.sections[key] = {
    paper_notes: `${key} 原文材料`, ai_suggestions: `${key} 模型建议`, user_notes: `${key} 个人想法`,
  }
  return {
    id: 'note-1', title: '个人阅读笔记', category: '科研主题', content: '服务器保存的完整正文',
    source_paper_id: 'paper-1', source_paper_title: '来源论文', source_paper_doi: '10.test/paper',
    research_points: [], tags: ['阅读'], notes: '个人补充备注', created_at: '2026-01-01', updated_at: '2026-01-02',
    card_type: 'reading_note', card_data: note, ...overrides,
  }
}

function show(note = item(), onDerive = vi.fn()) {
  const callbacks = { onClose: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn(), onDerive }
  render(<KnowledgeDetail item={note} {...callbacks} />)
  return callbacks
}

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

it('shows all nine sections with separate provenance labels, source and progress', () => {
  show()
  expect(screen.getByText('阅读进度：阅读中')).toBeInTheDocument()
  expect(screen.getByText('来源检索问题：原始检索问题')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '10.test/paper' })).toHaveAttribute('href', 'https://doi.org/10.test/paper')
  for (const section of studySections) {
    const region = screen.getByRole('region', { name: section.zh })
    expect(within(region).getByText('论文材料摘录/用户整理，未独立核验')).toBeInTheDocument()
    expect(within(region).getByText('AI建议，非论文事实')).toBeInTheDocument()
    expect(within(region).getByText('个人想法，非论文事实')).toBeInTheDocument()
    expect(within(region).getByText(`${section.key} 原文材料`)).toBeInTheDocument()
  }
  expect(screen.queryByText('服务器保存的完整正文')).not.toBeInTheDocument()
  expect(screen.getByText(/公式和代码按原文展示/)).toBeInTheDocument()
})

it('shows empty sections safely and cannot pretend to extract nonexistent material', () => {
  const note = readingNote()
  note.sections.math = { paper_notes: '', ai_suggestions: '', user_notes: '' }
  show(item({ card_data: note, source_paper_id: null }))
  expect(screen.getAllByText('尚未记录')).toHaveLength(9)
  expect(screen.getByText('未关联本地论文记录，可继续整理手写笔记。')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '提炼基础知识' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '提炼技能卡' })).toBeDisabled()
})

it.each([
  ['提炼基础知识', 'foundation', ['math', 'prerequisites'], ['experiments', 'figures', 'writing', 'formatting', 'overview', 'resources', 'translation']],
  ['提炼技能卡', 'skill', ['experiments', 'figures', 'writing', 'formatting'], ['math', 'prerequisites', 'overview', 'resources', 'translation']],
])('opens %s as a source-labelled editable draft without model calls or saving', (button, type, included, excluded) => {
  const original = item()
  const callbacks = show(original)
  fireEvent.click(screen.getByRole('button', { name: button as string }))
  expect(callbacks.onDerive).toHaveBeenCalledOnce()
  const draft = callbacks.onDerive.mock.calls[0][0]
  expect(draft).toMatchObject({ card_type: type, category: original.category, source_paper_id: original.source_paper_id, source_paper_doi: original.source_paper_doi })
  for (const name of included) {
    expect(draft.content).toContain(`${name} 原文材料`)
    expect(draft.content).toContain(`${name} 模型建议`)
    expect(draft.content).toContain(`${name} 个人想法`)
  }
  for (const name of excluded) expect(draft.content).not.toContain(`${name} 原文材料`)
  expect(draft.content).toContain('非论文事实')
  expect(draft.notes).toContain(original.id)
  expect(draft.id).toBeUndefined()
  expect(mocks.create).not.toHaveBeenCalled()
  expect(mocks.update).not.toHaveBeenCalled()
  expect(callbacks.onEdit).not.toHaveBeenCalled()
})

it('uses the shared Markdown exporter without altering saved content', () => {
  const original = item()
  show(original)
  fireEvent.click(screen.getByRole('button', { name: '导出 Markdown' }))
  expect(mocks.download).toHaveBeenCalledOnce()
  expect(mocks.download).toHaveBeenCalledWith(original)
  expect(mocks.update).not.toHaveBeenCalled()
})

it('preserves formula and code characters as text without executing embedded HTML', () => {
  const note = readingNote()
  const content = '$$L=x_1^2$$\n  for i in range(10):\n    run(i)\n<img src=x onerror=alert(1)>'
  note.sections.math = { paper_notes: content, ai_suggestions: '', user_notes: '' }
  show(item({ card_data: note }))
  const region = screen.getByRole('region', { name: '公式与推导' })
  expect(region.querySelector('.study-note-text')?.textContent).toBe(content)
  expect(region.querySelector('img')).toBeNull()
})

it('keeps legacy plain details and editing available without reading-only actions', () => {
  const original = item({ card_type: 'skill', card_data: null })
  const callbacks = show(original)
  expect(screen.getByText('服务器保存的完整正文')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '提炼基础知识' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'knowledge.edit' }))
  expect(callbacks.onEdit).toHaveBeenCalledWith(original)
})

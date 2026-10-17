import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Link, MemoryRouter, useLocation } from 'react-router-dom'
import PaperReadingWorkspace from '../PaperReadingWorkspace/PaperReadingWorkspace'
import ResearchAssistant from '@/pages/ResearchAssistant'
import type { KnowledgeItem } from '@/api/types'
import { useKnowledgeStore } from '@/stores/knowledgeStore'
import {
  readerAssetUrl,
  type ReadingDocument,
  type ReadingPage,
  type ReadingResponse,
} from '@/api/readerClient'

const mocks = vi.hoisted(() => ({
  document: vi.fn(),
  page: vi.fn(),
  assist: vi.fn(),
  paper: vi.fn(),
  categories: vi.fn(),
  list: vi.fn(),
  upload: vi.fn(),
  form: vi.fn(),
}))
vi.mock('@/api/readerClient', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/readerClient')>()),
  readerApi: {
    document: mocks.document,
    page: mocks.page,
    assist: mocks.assist,
  },
}))
vi.mock('@/api/client', () => ({
  papersApi: { get: mocks.paper, uploadFulltext: mocks.upload },
  knowledgeApi: { getCategories: mocks.categories, list: mocks.list },
}))
vi.mock('@/stores/localeStore', () => ({
  useLocaleStore: () => ({ locale: 'zh' }),
}))
vi.mock('../KnowledgeForm/KnowledgeForm', () => ({
  default: (props: unknown) => {
    mocks.form(props)
    return null
  },
}))
vi.mock('../PaperDetail/SimilarPapers', () => ({
  default: () => <p>真实论文检索区</p>,
}))

const doc: ReadingDocument = {
  paper_id: 'p1',
  title: 'Markov chains for traffic repair',
  available: true,
  page_count: 3,
  content_hash: 'hash-one',
  pdf_url: '/api/v1/reader/p1/pdf',
  source: 'uploaded',
  warnings: [],
}
const makePage = (n: number, id = 'p1', hash = 'hash-one'): ReadingPage => ({
  paper_id: id,
  page_number: n,
  page_count: 3,
  content_hash: hash,
  text: `Page ${n}. A Markov chain has states and transitions.`,
  text_truncated: false,
  text_verified: true,
  image_url: `/api/v1/reader/${id}/pages/${n}/image`,
  coverage: 'current_page_text',
  warnings: [],
})
const response: ReadingResponse = {
  answer: '先看状态，再看转移。',
  source_notes: '本页定义状态。',
  teaching_notes: '用晴天和雨天举例。',
  exercise: '请手算两次转移。',
  sources: [
    {
      label: 'P1',
      page_number: 1,
      content_hash: 'hash-one',
      excerpt: 'A Markov chain has states and transitions.',
    },
  ],
  coverage: 'selected_text',
  warnings: [],
  model_completed: true,
  provider: 'siliconflow',
  model: 'Qwen/test',
  total_tokens: 100,
  fallback_used: false,
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}
function mount(id = 'p1', category = '交通') {
  return render(
    <MemoryRouter>
      <PaperReadingWorkspace key={id} paperId={id} initialCategory={category} />
    </MemoryRouter>,
  )
}
async function ready() {
  await screen.findByText('Page 1. A Markov chain has states and transitions.')
}
function lastForm() {
  return mocks.form.mock.calls.at(-1)![0]
}
const reviewNote = () =>
  fireEvent.click(
    screen.getByRole('button', {
      name: /核对并加入阅读笔记|已整理，继续编辑/,
    }),
  )
async function confirmNote(overrides: Partial<KnowledgeItem> = {}) {
  const props = lastForm()
  const saved = {
    ...(props.editingItem || props.prefill),
    id: 'saved-note',
    updated_at: '2026-10-17T12:00:00',
    ...overrides,
  }
  mocks.list.mockResolvedValue({ data: { items: [saved] } })
  await act(async () => {
    props.onSuccess(saved)
    props.onClose()
  })
  return saved
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.document.mockImplementation((id: string) =>
    Promise.resolve({
      data: {
        ...doc,
        paper_id: id,
        title: id === 'p1' ? doc.title : 'Second paper',
        content_hash: id === 'p1' ? 'hash-one' : 'hash-two',
      },
    }),
  )
  mocks.page.mockImplementation((id: string, n: number, hash: string) =>
    Promise.resolve({ data: makePage(n, id, hash) }),
  )
  mocks.assist.mockResolvedValue({ data: response })
  mocks.paper.mockResolvedValue({
    data: {
      id: 'p1',
      title: doc.title,
      doi: '10.test/p1',
      venue: 'Test Journal',
    },
  })
  mocks.categories.mockResolvedValue({ data: [{ name: '交通', count: 2 }] })
  mocks.list.mockResolvedValue({ data: { items: [] } })
  mocks.upload.mockResolvedValue({ data: { available: true } })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.getSelection()?.removeAllRanges()
  useKnowledgeStore.persist.clearStorage()
})

it('loads the exact page without invoking AI and captures a real text selection', async () => {
  mount()
  await ready()
  expect(mocks.assist).not.toHaveBeenCalled()
  const original = screen.getByLabelText('本页可选原文')
  const range = document.createRange()
  range.setStart(original.firstChild!, 8)
  range.setEnd(original.firstChild!, 22)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
  fireEvent.mouseUp(original)
  expect(screen.getByLabelText('选中原文（PDF 第 1 页）')).toHaveValue(
    'A Markov chain',
  )
  fireEvent.click(screen.getByRole('button', { name: '翻译原文' }))
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  expect(mocks.assist).toHaveBeenCalledWith(
    'p1',
    expect.objectContaining({
      mode: 'translate',
      page_number: 1,
      content_hash: 'hash-one',
      selection: 'A Markov chain',
    }),
    expect.any(AbortSignal),
  )
  expect(screen.getByText(/siliconflow\/Qwen\/test/)).toBeInTheDocument()
})

it('translates the current page without a selection and sends visual requests with their explicit page', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '翻译原文' }))
  expect(screen.getByText(/未选文字时翻译本页/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  expect(mocks.assist).toHaveBeenCalledWith(
    'p1',
    expect.objectContaining({
      mode: 'translate',
      page_number: 1,
      selection: '',
    }),
    expect.any(AbortSignal),
  )
  fireEvent.click(screen.getByRole('button', { name: '读图表' }))
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByAltText('论文 PDF 第 2 页')
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  expect(mocks.assist).toHaveBeenCalledWith(
    'p1',
    expect.objectContaining({
      mode: 'figure',
      page_number: 2,
      selection: '',
      content_hash: 'hash-one',
    }),
    expect.any(AbortSignal),
  )
})

it('aborts a pending model request when changing pages and ignores its late answer', async () => {
  const pending = deferred<{ data: ReadingResponse }>()
  mocks.assist.mockReturnValue(pending.promise)
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  const signal = mocks.assist.mock.calls[0][2] as AbortSignal
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByText('Page 2. A Markov chain has states and transitions.')
  expect(signal.aborted).toBe(true)
  await act(async () => pending.resolve({ data: response }))
  expect(screen.queryByText(response.teaching_notes)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始本次伴读' })).toBeEnabled()
})

it('does not let late material or model responses leak into a different paper', async () => {
  const pending = deferred<{ data: ReadingResponse }>()
  mocks.assist.mockReturnValue(pending.promise)
  const view = mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  const signal = mocks.assist.mock.calls[0][2] as AbortSignal
  view.rerender(
    <MemoryRouter>
      <PaperReadingWorkspace key="p2" paperId="p2" initialCategory="交通" />
    </MemoryRouter>,
  )
  await screen.findByRole('heading', { name: 'Second paper' })
  await act(async () => pending.resolve({ data: response }))
  expect(signal.aborted).toBe(true)
  expect(screen.queryByText(response.teaching_notes)).not.toBeInTheDocument()
})

it('keeps earlier page answers and their navigation when reading the next page', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByText('Page 2. A Markov chain has states and transitions.')
  expect(screen.getByText(response.teaching_notes)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '回原文页' }))
  await ready()
  expect(screen.getByLabelText('PDF 页码')).toHaveValue(1)
})

it('keeps manual notes available without a PDF or AI call', async () => {
  mocks.document.mockResolvedValue({
    data: {
      ...doc,
      available: false,
      page_count: 0,
      content_hash: '',
      pdf_url: null,
    },
  })
  mount()
  await screen.findByRole('heading', { name: '先准备可阅读的全文' })
  expect(screen.getByRole('button', { name: '开始本次伴读' })).toBeDisabled()
  fireEvent.change(
    screen.getByLabelText('我的理解 / 纸笔推导记录（不会自动发给 AI）'),
    { target: { value: '我需要先复习条件概率。' } },
  )
  fireEvent.click(screen.getByRole('button', { name: '打开 / 保存阅读笔记' }))
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(lastForm().prefill).toEqual(
    expect.objectContaining({
      source_paper_id: 'p1',
      category: '交通',
      card_type: 'reading_note',
    }),
  )
  expect(lastForm().prefill.card_data.sections.overview.user_notes).toBe(
    '我需要先复习条件概率。',
  )
  expect(mocks.assist).not.toHaveBeenCalled()
})

it('merges confirmed suggestions into an existing note without overwriting personal records or paper facts', async () => {
  mocks.list.mockResolvedValue({
    data: {
      items: [
        {
          id: 'n1',
          title: '已有笔记',
          card_type: 'reading_note',
          updated_at: 'now',
          card_data: {
            sections: {
              prerequisites: {
                paper_notes: '已有摘录',
                ai_suggestions: '已有建议',
                user_notes: '已有个人理解',
              },
            },
          },
        },
      ],
    },
  })
  mount()
  await ready()
  fireEvent.change(screen.getByLabelText('选中原文（PDF 第 1 页）'), {
    target: { value: 'A Markov chain has states and transitions.' },
  })
  fireEvent.change(
    screen.getByLabelText('我的理解 / 纸笔推导记录（不会自动发给 AI）'),
    { target: { value: '我已经手算一次。' } },
  )
  fireEvent.click(screen.getByRole('button', { name: '补基础与练习' }))
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  fireEvent.click(screen.getByRole('button', { name: '核对并加入阅读笔记' }))
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(mocks.list).toHaveBeenCalledWith('交通', {
    source_paper_id: 'p1',
    card_type: 'reading_note',
    page_size: 1,
  })
  const entry = lastForm().editingItem.card_data.sections.prerequisites
  expect(entry.paper_notes).toContain('已有摘录')
  expect(entry.paper_notes).toContain('文件版本 hash-one')
  expect(entry.paper_notes).not.toContain(response.teaching_notes)
  expect(entry.ai_suggestions).toContain(response.teaching_notes)
  expect(entry.user_notes).toBe('已有个人理解\n\n我已经手算一次。')
})

it('adds an answer once after successful save and preserves source snapshots while appending fresh personal notes', async () => {
  mocks.list.mockResolvedValue({
    data: {
      items: [
        {
          id: 'n1',
          card_type: 'reading_note',
          updated_at: 'old',
          source_paper_id: 'p1',
          category: '交通',
          card_data: {
            sections: {
              overview: {
                paper_notes:
                  '已有原文\n来源快照：旧 PDF 第 3 页，文件版本 old-hash。',
                ai_suggestions: '已有建议\n旧来源 [S1] old-hash：旧摘录。',
                user_notes: '我的原有想法',
              },
            },
          },
        },
      ],
    },
  })
  mount()
  await ready()
  fireEvent.change(screen.getByLabelText('选中原文（PDF 第 1 页）'), {
    target: { value: 'A Markov chain has states and transitions.' },
  })
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  const first = await confirmNote()
  fireEvent.change(
    screen.getByLabelText('我的理解 / 纸笔推导记录（不会自动发给 AI）'),
    {
      target: { value: '我的原有想法' },
    },
  )
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  const merged = lastForm().editingItem.card_data.sections.overview
  expect(merged.paper_notes).toBe(first.card_data.sections.overview.paper_notes)
  expect(merged.ai_suggestions).toBe(
    first.card_data.sections.overview.ai_suggestions,
  )
  expect(screen.getByRole('button', { name: '已整理，继续编辑' })).toBeEnabled()
  expect(merged.ai_suggestions).toContain('旧来源 [S1] old-hash：旧摘录。')
  expect(merged.ai_suggestions).toContain('[P1]')
  expect(merged.ai_suggestions).toContain('文件版本 hash-one')
  expect(merged.user_notes).toBe('我的原有想法\n\n我的原有想法')
  expect(lastForm().editingItem.updated_at).toBe(first.updated_at)
})

it('does not count a cancelled draft as saved and only marks a successful reading-note save', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  const first = lastForm().prefill.card_data
  act(() => lastForm().onClose())
  expect(
    screen.queryByRole('button', { name: '已整理，继续编辑' }),
  ).not.toBeInTheDocument()
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(lastForm().prefill.card_data).toEqual(first)
  await confirmNote()
  expect(
    screen.getByRole('button', { name: '已整理，继续编辑' }),
  ).toBeInTheDocument()
})

it('does not skip an answer when the existing notebook lookup returns a different note id', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  const saved = await confirmNote({ id: 'note-A' })
  mocks.list.mockResolvedValue({
    data: {
      items: [
        {
          ...saved,
          id: 'note-B',
          card_data: {
            ...saved.card_data,
            sections: {
              overview: {
                paper_notes: '笔记B自己的原文',
                ai_suggestions: '笔记B已有建议',
                user_notes: '',
              },
            },
          },
        },
      ],
    },
  })
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(lastForm().editingItem.id).toBe('note-B')
  expect(
    lastForm().editingItem.card_data.sections.overview.ai_suggestions,
  ).toContain(response.teaching_notes)
  expect(
    lastForm().editingItem.card_data.sections.overview.ai_suggestions,
  ).toContain('笔记B已有建议')
  await confirmNote({ id: 'note-B' })
  mocks.list.mockResolvedValue({ data: { items: [saved] } })
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(lastForm().editingItem.id).toBe('note-A')
  expect(
    lastForm().editingItem.card_data.sections.overview.ai_suggestions,
  ).toBe(saved.card_data.sections.overview.ai_suggestions)
})

it('uses an older answer question as its card title after asking a different question on another page', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '补基础与练习' }))
  fireEvent.change(screen.getByLabelText('我想弄懂什么（可选）'), {
    target: { value: '旧问题：怎样理解马尔科夫假设？' },
  })
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByText('Page 2. A Markov chain has states and transitions.')
  fireEvent.change(screen.getByLabelText('我想弄懂什么（可选）'), {
    target: { value: '新问题：怎样理解损失函数？' },
  })
  mocks.assist.mockResolvedValue({
    data: { ...response, teaching_notes: '损失函数的新回答。' },
  })
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText('损失函数的新回答。')
  const oldAnswer = screen
    .getByText(response.teaching_notes)
    .closest('article')!
  fireEvent.click(
    within(oldAnswer).getByRole('button', { name: '整理为基础知识卡' }),
  )
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(lastForm().prefill.title).toContain('旧问题：怎样理解马尔科夫假设？')
  expect(lastForm().prefill.title).not.toContain('新问题')
})

it('does not clear personal text typed after the note draft was opened', async () => {
  mount()
  await ready()
  const personal = screen.getByLabelText(
    '我的理解 / 纸笔推导记录（不会自动发给 AI）',
  )
  fireEvent.change(personal, { target: { value: '已带入编辑器的想法。' } })
  fireEvent.click(screen.getByRole('button', { name: '打开 / 保存阅读笔记' }))
  await waitFor(() => expect(lastForm().open).toBe(true))
  fireEvent.change(personal, {
    target: { value: '打开编辑器后新写的另一条想法。' },
  })
  await confirmNote()
  expect(personal).toHaveValue('打开编辑器后新写的另一条想法。')
})

it('keeps separate answers distinct even when they finish at the same millisecond', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(123456)
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  await confirmNote()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await waitFor(() =>
    expect(screen.getAllByText(response.teaching_notes)).toHaveLength(2),
  )
  expect(
    screen.getAllByRole('button', { name: '已整理，继续编辑' }),
  ).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '核对并加入阅读笔记' }))
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(
    lastForm().editingItem.card_data.sections.overview.ai_suggestions.split(
      response.teaching_notes,
    ),
  ).toHaveLength(3)
})

it('uses the answer original selection and each source hash rather than a different page selected later', async () => {
  mocks.assist.mockResolvedValue({
    data: {
      ...response,
      sources: [
        {
          ...response.sources[0],
          content_hash: 'source-snapshot-hash',
        },
      ],
    },
  })
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByText('Page 2. A Markov chain has states and transitions.')
  fireEvent.change(screen.getByLabelText('选中原文（PDF 第 2 页）'), {
    target: { value: 'This is a different page selection.' },
  })
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  const entry = lastForm().prefill.card_data.sections.overview
  expect(entry.paper_notes).toBe('')
  expect(entry.ai_suggestions).not.toContain(
    'This is a different page selection.',
  )
  expect(entry.ai_suggestions).toContain(
    'PDF 第 1 页；文件版本 source-snapshot-hash',
  )
  expect(Object.keys(entry).sort()).toEqual([
    'ai_suggestions',
    'paper_notes',
    'user_notes',
  ])
})

it.each([
  ['补基础与练习', '整理为基础知识卡'],
  ['整理写作样例', '整理为技能 / 写作卡'],
])(
  'does not mark a %s card save as an answer added to a reading note',
  async (mode, button) => {
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: mode }))
    fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
    await screen.findByText(response.teaching_notes)
    fireEvent.click(screen.getByRole('button', { name: button }))
    await waitFor(() => expect(lastForm().open).toBe(true))
    await confirmNote()
    expect(
      screen.queryByRole('button', { name: '已整理，继续编辑' }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '核对并加入阅读笔记' }),
    ).toBeEnabled()
  },
)

it.each(['page', 'version', 'paper'] as const)(
  'does not swallow a new answer after changing %s',
  async (change) => {
    const view = mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
    await screen.findByText(response.teaching_notes)
    reviewNote()
    await waitFor(() => expect(lastForm().open).toBe(true))
    await confirmNote()
    if (change === 'page') {
      fireEvent.click(screen.getByRole('button', { name: '下一页' }))
      await screen.findByText(
        'Page 2. A Markov chain has states and transitions.',
      )
    } else if (change === 'version') {
      mocks.document.mockResolvedValueOnce({
        data: { ...doc, content_hash: 'new-version' },
      })
      fireEvent.click(screen.getByRole('button', { name: '重新载入' }))
      await waitFor(() =>
        expect(mocks.page).toHaveBeenLastCalledWith(
          'p1',
          1,
          'new-version',
          expect.any(AbortSignal),
        ),
      )
      await ready()
    } else {
      mocks.list.mockResolvedValue({ data: { items: [] } })
      view.rerender(
        <MemoryRouter>
          <PaperReadingWorkspace paperId="p2" initialCategory="交通" />
        </MemoryRouter>,
      )
      await screen.findByRole('heading', { name: 'Second paper' })
      await ready()
    }
    const newSource = {
      ...response.sources[0],
      page_number: change === 'page' ? 2 : 1,
      content_hash:
        change === 'version'
          ? 'new-version'
          : change === 'paper'
            ? 'hash-two'
            : 'hash-one',
    }
    mocks.assist.mockResolvedValue({
      data: {
        ...response,
        teaching_notes: '新的伴读回答。',
        sources: [newSource],
      },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
    await screen.findByText('新的伴读回答。')
    fireEvent.click(screen.getByRole('button', { name: '核对并加入阅读笔记' }))
    await waitFor(() => expect(lastForm().open).toBe(true))
    const item = lastForm().editingItem || lastForm().prefill
    expect(item.card_data.sections.overview.ai_suggestions).toContain(
      '新的伴读回答。',
    )
    expect(item.card_data.sections.overview.ai_suggestions).toContain(
      `PDF 第 ${newSource.page_number} 页；文件版本 ${newSource.content_hash}`,
    )
    if (change === 'paper') expect(item.source_paper_id).toBe('p2')
  },
)

it('keeps an unconfirmed note draft open and unmarked until its successful save callback', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  const original = lastForm()
  // KnowledgeForm's real 409 preservation is covered by ReadingNote.test.tsx.
  // Merely opening/reviewing a draft must not act like its successful callback.
  expect(lastForm().open).toBe(true)
  expect(lastForm().prefill).toBe(original.prefill)
  expect(
    screen.queryByRole('button', { name: '已整理，继续编辑' }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: '查看已保存笔记' }),
  ).not.toBeInTheDocument()
  await confirmNote()
  expect(
    screen.getByRole('button', { name: '已整理，继续编辑' }),
  ).toBeInTheDocument()
})

it('links to the actual saved category only on explicit navigation, preserving the leave confirmation', async () => {
  function Location() {
    return <p data-testid="location">{useLocation().pathname}</p>
  }
  useKnowledgeStore.setState({
    selectedCategory: '旧研究分类',
    searchQuery: '旧筛选',
  })
  render(
    <MemoryRouter initialEntries={['/assistant?paper=p1']}>
      <PaperReadingWorkspace paperId="p1" initialCategory="交通" />
      <Location />
    </MemoryRouter>,
  )
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  await confirmNote({ category: '实际保存的新分类' })
  expect(screen.getByText('已保存到 实际保存的新分类')).toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: '已整理，继续编辑' }),
  ).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('保存到研究分类'), {
    target: { value: '实际保存的新分类' },
  })
  expect(
    screen.getByRole('button', { name: '已整理，继续编辑' }),
  ).toBeInTheDocument()
  expect(screen.getByTestId('location')).toHaveTextContent('/assistant')
  expect(useKnowledgeStore.getState().selectedCategory).toBe('旧研究分类')
  fireEvent.change(
    screen.getByLabelText('我的理解 / 纸笔推导记录（不会自动发给 AI）'),
    { target: { value: '保存之后新写、还没保存的想法。' } },
  )
  vi.mocked(window.confirm).mockReturnValue(false)
  fireEvent.click(screen.getByRole('button', { name: '查看已保存笔记' }))
  expect(screen.getByTestId('location')).toHaveTextContent('/assistant')
  expect(useKnowledgeStore.getState().selectedCategory).toBe('旧研究分类')
  vi.mocked(window.confirm).mockReturnValue(true)
  fireEvent.click(screen.getByRole('button', { name: '查看已保存笔记' }))
  expect(screen.getByTestId('location')).toHaveTextContent('/knowledge')
  expect(useKnowledgeStore.getState().selectedCategory).toBe('实际保存的新分类')
  expect(useKnowledgeStore.getState().searchQuery).toBe('')
})

it('opens the saved notebook without another leave prompt when every answer and personal note was saved', async () => {
  useKnowledgeStore.setState({
    selectedCategory: '旧分类',
    searchQuery: '旧筛选',
  })
  mount()
  await ready()
  fireEvent.change(screen.getByLabelText('选中原文（PDF 第 1 页）'), {
    target: { value: 'A Markov chain has states and transitions.' },
  })
  fireEvent.change(
    screen.getByLabelText('我的理解 / 纸笔推导记录（不会自动发给 AI）'),
    {
      target: { value: '已随笔记保存的个人理解。' },
    },
  )
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  reviewNote()
  await waitFor(() => expect(lastForm().open).toBe(true))
  await confirmNote()
  expect(
    screen.getByLabelText('我的理解 / 纸笔推导记录（不会自动发给 AI）'),
  ).toHaveValue('')
  vi.mocked(window.confirm).mockClear().mockReturnValue(false)
  fireEvent.click(screen.getByRole('button', { name: '查看已保存笔记' }))
  expect(window.confirm).not.toHaveBeenCalled()
  expect(useKnowledgeStore.getState().selectedCategory).toBe('交通')
  expect(useKnowledgeStore.getState().searchQuery).toBe('')
})

it.each([
  'cancelled-draft',
  'unconfirmed-draft',
  'new-answer',
  'changed-category',
] as const)(
  'still confirms leaving when there is %s work after a saved response',
  async (remaining) => {
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
    await screen.findByText(response.teaching_notes)
    reviewNote()
    await waitFor(() => expect(lastForm().open).toBe(true))
    if (remaining === 'cancelled-draft') act(() => lastForm().onClose())
    else if (remaining !== 'unconfirmed-draft') {
      await confirmNote()
      if (remaining === 'changed-category')
        fireEvent.change(screen.getByLabelText('保存到研究分类'), {
          target: { value: '尚未整理到的新分类' },
        })
      else {
        fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
        await waitFor(() =>
          expect(screen.getAllByText(response.teaching_notes)).toHaveLength(2),
        )
      }
    }
    vi.mocked(window.confirm).mockClear().mockReturnValue(false)
    fireEvent.click(screen.getByRole('link', { name: '返回科研问答' }))
    expect(window.confirm).toHaveBeenCalledOnce()
    expect(screen.getAllByText(response.teaching_notes).length).toBeGreaterThan(
      0,
    )
  },
)

it('prepares a writing sample in an existing skill-card form with journal and provenance, without auto-saving', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '整理写作样例' }))
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  fireEvent.click(screen.getByRole('button', { name: '整理为技能 / 写作卡' }))
  await waitFor(() => expect(lastForm().open).toBe(true))
  expect(lastForm().prefill).toEqual(
    expect.objectContaining({
      card_type: 'skill',
      source_paper_id: 'p1',
      tags: ['写作样例', '期刊/发表源：Test Journal'],
    }),
  )
  expect(lastForm().prefill.content).toContain('AI 学习建议（请核对）')
  expect(lastForm().prefill.content).toContain('PDF 第 1 页；文件版本 hash-one')
})

it('displays actionable vision configuration errors without inventing a completed answer', async () => {
  mocks.assist.mockRejectedValue({
    response: { data: { detail: '请先在设置中配置视觉模型。' } },
  })
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '解释公式' }))
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    '请先在设置中配置视觉模型。',
  )
  expect(screen.queryByText(response.teaching_notes)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始本次伴读' })).toBeEnabled()
})

it('only loads real related-paper recommendations after explicit user action', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '阅读拓展' }))
  expect(screen.queryByText('真实论文检索区')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '检索真实论文推荐' }))
  expect(screen.getByText('真实论文检索区')).toBeInTheDocument()
  expect(mocks.assist).not.toHaveBeenCalled()
})

it('keeps current responses if the user cancels reloading', async () => {
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  vi.mocked(window.confirm).mockReturnValue(false)
  fireEvent.click(screen.getByRole('button', { name: '重新载入' }))
  expect(mocks.document).toHaveBeenCalledTimes(1)
  expect(screen.getByText(response.teaching_notes)).toBeInTheDocument()
})

it('rejects external, unrelated, credentialed and script asset URLs', () => {
  expect(readerAssetUrl('/api/v1/reader/p1/pdf?content_hash=one', 'p1')).toBe(
    '/api/v1/reader/p1/pdf?content_hash=one',
  )
  expect(readerAssetUrl('https://other.example/paper.pdf', 'p1')).toBeNull()
  expect(readerAssetUrl('/api/v1/reader/p2/pdf', 'p1')).toBeNull()
  expect(readerAssetUrl('javascript:alert(1)', 'p1')).toBeNull()
  expect(
    readerAssetUrl(
      `http://user:password@${window.location.host}/api/v1/reader/p1/pdf`,
      'p1',
    ),
  ).toBeNull()
})

it('opens a cited PDF page and clamps an oversized page to the last real page', async () => {
  const view = render(
    <MemoryRouter>
      <PaperReadingWorkspace key="start-three" paperId="p1" initialPage={3} />
    </MemoryRouter>,
  )
  await screen.findByText('Page 3. A Markov chain has states and transitions.')
  expect(screen.getByLabelText('PDF 页码')).toHaveValue(3)
  view.rerender(
    <MemoryRouter>
      <PaperReadingWorkspace
        key="start-too-large"
        paperId="p1"
        initialPage={99}
      />
    </MemoryRouter>,
  )
  await screen.findByText('Page 3. A Markov chain has states and transitions.')
  expect(screen.getByLabelText('PDF 页码')).toHaveValue(3)
})

it('renders structured reading output once instead of repeating the combined answer', async () => {
  mocks.assist.mockResolvedValue({
    data: {
      ...response,
      answer: `### 原文说明\n${response.source_notes}\n### 教学补充\n${response.teaching_notes}\n### 练习\n${response.exercise}`,
    },
  })
  mount()
  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始本次伴读' }))
  await screen.findByText(response.teaching_notes)
  expect(screen.getAllByText(response.teaching_notes)).toHaveLength(1)
  expect(screen.getAllByText(response.exercise)).toHaveLength(1)
  expect(screen.queryByText(/### 教学补充/)).not.toBeInTheDocument()
})

it('follows citation page query changes for the same paper and then another paper', async () => {
  render(
    <MemoryRouter initialEntries={['/assistant?paper=p1&page=2']}>
      <ResearchAssistant />
      <Link to="/assistant?paper=p1&page=3">同篇第三页</Link>
      <Link to="/assistant?paper=p2&page=2">另一篇第二页</Link>
    </MemoryRouter>,
  )
  await screen.findByText('Page 2. A Markov chain has states and transitions.')
  fireEvent.click(screen.getByRole('link', { name: '同篇第三页' }))
  await screen.findByText('Page 3. A Markov chain has states and transitions.')
  fireEvent.click(screen.getByRole('link', { name: '另一篇第二页' }))
  await screen.findByRole('heading', { name: 'Second paper' })
  await screen.findByText('Page 2. A Markov chain has states and transitions.')
  expect(mocks.page).toHaveBeenLastCalledWith(
    'p2',
    2,
    'hash-two',
    expect.any(AbortSignal),
  )
})

it('warns when the cited document version has changed instead of silently treating the new PDF as the old source', async () => {
  render(
    <MemoryRouter>
      <PaperReadingWorkspace
        paperId="p1"
        initialPage={2}
        initialHash="older-version"
      />
    </MemoryRouter>,
  )
  await screen.findByText('Page 2. A Markov chain has states and transitions.')
  expect(screen.getByText(/来源对应的 PDF 版本已变化/)).toBeInTheDocument()
})

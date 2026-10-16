import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WritingSamplesPanel from '../PaperReadingWorkspace/WritingSamplesPanel'
import type { ReadingResponse } from '@/api/readerClient'

const mocks = vi.hoisted(() => ({
  library: vi.fn(),
  document: vi.fn(),
  compare: vi.fn(),
  review: vi.fn(),
  read: vi.fn(),
}))
vi.mock('@/api/readerClient', () => ({
  readerApi: {
    library: mocks.library,
    document: mocks.document,
    writingSamples: mocks.compare,
  },
}))
const papers = Array.from({ length: 7 }, (_, index) => ({
  paper_id: `p${index + 1}`,
  title: `样本论文 ${index + 1}`,
  venue: index < 3 ? 'Traffic Journal' : 'Other Journal',
  year: 2025,
  source: 'uploaded',
}))
const result: ReadingResponse = {
  answer: '总览',
  source_notes: '两篇摘要均先提出问题 [S1][S2]。',
  teaching_notes: '可以自己填写问题、方法和结果，但不要照搬原句。',
  exercise: '先写一版自己的摘要。',
  sources: [
    {
      label: 'S1',
      paper_id: 'p1',
      paper_title: '样本论文 1',
      venue: 'Traffic Journal',
      page_number: 1,
      content_hash: 'hash-p1',
      excerpt: 'Abstract one.',
    },
    {
      label: 'S2',
      paper_id: 'p2',
      paper_title: '样本论文 2',
      venue: 'Traffic Journal',
      page_number: 2,
      content_hash: 'hash-p2',
      excerpt: 'Abstract two.',
    },
  ],
  coverage: '每篇首、次、末页摘录',
  warnings: ['样本观察，不等于期刊规范。'],
  model_completed: true,
  provider: 'zhipu',
  model: 'glm-test',
  total_tokens: 400,
  fallback_used: false,
}
function mount() {
  return render(
    <WritingSamplesPanel
      currentPaperId="p1"
      category="交通修复"
      chinese
      onReview={mocks.review}
      onReadSource={mocks.read}
    />,
  )
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}
async function chooseSecond() {
  fireEvent.click(await screen.findByLabelText(/样本论文 2/))
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.library.mockResolvedValue({ data: { papers, limit: 50, warnings: [] } })
  mocks.document.mockImplementation((id: string) =>
    Promise.resolve({
      data: { paper_id: id, available: true, content_hash: `hash-${id}` },
    }),
  )
  mocks.compare.mockResolvedValue({ data: result })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it('requires 2–5 local papers and prevents selecting a sixth sample', async () => {
  mount()
  await screen.findByLabelText(/样本论文 1/)
  expect(screen.getByRole('button', { name: '比较所选样例' })).toBeDisabled()
  await chooseSecond()
  expect(screen.getByRole('button', { name: '比较所选样例' })).toBeEnabled()
  for (const n of [3, 4, 5])
    fireEvent.click(screen.getByLabelText(new RegExp(`样本论文 ${n}`)))
  expect(screen.getByLabelText(/样本论文 6/)).toBeDisabled()
  fireEvent.click(screen.getByLabelText(/样本论文 4/))
  expect(screen.getByLabelText(/样本论文 6/)).toBeEnabled()
  expect(mocks.compare).not.toHaveBeenCalled()
})

it('freezes PDF versions before comparing and keeps personal observations out of the AI request', async () => {
  mount()
  await chooseSecond()
  fireEvent.change(screen.getByLabelText('我的写作观察（不自动发给 AI）'), {
    target: { value: '我想记录的私人观察。' },
  })
  fireEvent.change(screen.getByLabelText('比较重点（可选）'), {
    target: { value: '比较摘要衔接' },
  })
  fireEvent.click(screen.getByRole('button', { name: '比较所选样例' }))
  await screen.findByText(result.teaching_notes)
  expect(mocks.compare).toHaveBeenCalledWith(
    {
      paper_ids: ['p1', 'p2'],
      question: '比较摘要衔接',
      content_hashes: { p1: 'hash-p1', p2: 'hash-p2' },
    },
    expect.any(AbortSignal),
  )
  expect(mocks.review).not.toHaveBeenCalled()
  expect(screen.getAllByText(result.teaching_notes)).toHaveLength(1)
})

it('ignores a late comparison after the sample selection changes', async () => {
  const pending = deferred<{ data: ReadingResponse }>()
  mocks.compare.mockReturnValue(pending.promise)
  mount()
  await chooseSecond()
  fireEvent.click(screen.getByRole('button', { name: '比较所选样例' }))
  await waitFor(() => expect(mocks.compare).toHaveBeenCalledTimes(1))
  const signal = mocks.compare.mock.calls[0][1] as AbortSignal
  fireEvent.click(screen.getByLabelText(/样本论文 3/))
  expect(signal.aborted).toBe(true)
  await act(async () => pending.resolve({ data: result }))
  expect(screen.queryByText(result.teaching_notes)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '比较所选样例' })).toBeEnabled()
})

it('prepares a skill-card draft only after confirmation, preserving all source snapshots', async () => {
  mount()
  await chooseSecond()
  fireEvent.change(screen.getByLabelText('我的写作观察（不自动发给 AI）'), {
    target: { value: '我准备先写研究问题。' },
  })
  fireEvent.click(screen.getByRole('button', { name: '比较所选样例' }))
  await screen.findByText(result.teaching_notes)
  fireEvent.click(
    screen.getByRole('button', { name: '核对后整理为技能 / 写作卡' }),
  )
  expect(mocks.review).toHaveBeenCalledTimes(1)
  const draft = mocks.review.mock.calls[0][0]
  expect(draft).toEqual(
    expect.objectContaining({
      category: '交通修复',
      card_type: 'skill',
      source_paper_id: null,
      notes: '我准备先写研究问题。',
    }),
  )
  expect(draft.tags).toEqual([
    '写作样例',
    '多篇对照',
    '期刊/发表源：Traffic Journal',
  ])
  expect(draft.content).toContain('[S1] 样本论文 1')
  expect(draft.content).toContain('[S2] 样本论文 2')
  expect(draft.content).toContain('/assistant?paper=p2&page=2')
  expect(draft.content).not.toContain('我准备先写研究问题。')
})

it('does not compare missing PDFs or accept mismatched source versions', async () => {
  mocks.document.mockResolvedValue({
    data: { paper_id: 'p1', available: false, content_hash: null },
  })
  mount()
  await chooseSecond()
  fireEvent.click(screen.getByRole('button', { name: '比较所选样例' }))
  await screen.findByRole('alert')
  expect(mocks.compare).not.toHaveBeenCalled()
  mocks.document.mockImplementation((id: string) =>
    Promise.resolve({
      data: { paper_id: id, available: true, content_hash: `hash-${id}` },
    }),
  )
  mocks.compare.mockResolvedValue({
    data: {
      ...result,
      sources: [{ ...result.sources[0], content_hash: 'outdated' }],
    },
  })
  fireEvent.click(screen.getByRole('button', { name: '比较所选样例' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    '样例来源与选定论文版本不一致',
  )
  expect(
    screen.getByRole('button', { name: '核对后整理为技能 / 写作卡' }),
  ).toBeDisabled()
})

it('opens the exact paper page from a verified source', async () => {
  mount()
  await chooseSecond()
  fireEvent.click(screen.getByRole('button', { name: '比较所选样例' }))
  await screen.findByText(result.teaching_notes)
  fireEvent.click(screen.getByText('来源快照（2）'))
  fireEvent.click(
    screen.getByRole('button', { name: '[S2] 样本论文 2 · PDF 2' }),
  )
  expect(mocks.read).toHaveBeenCalledWith('p2', 2, 'hash-p2')
})

it('reads PDF version snapshots serially to respect the bounded parsing worker', async () => {
  const first = deferred<{
    data: { paper_id: string; available: boolean; content_hash: string }
  }>()
  mocks.document.mockImplementation((id: string) =>
    id === 'p1'
      ? first.promise
      : Promise.resolve({
          data: { paper_id: id, available: true, content_hash: `hash-${id}` },
        }),
  )
  mount()
  await chooseSecond()
  fireEvent.click(screen.getByRole('button', { name: '比较所选样例' }))
  await waitFor(() => expect(mocks.document).toHaveBeenCalledTimes(1))
  expect(mocks.compare).not.toHaveBeenCalled()
  await act(async () =>
    first.resolve({
      data: { paper_id: 'p1', available: true, content_hash: 'hash-p1' },
    }),
  )
  await screen.findByText(result.teaching_notes)
  expect(mocks.document).toHaveBeenCalledTimes(2)
})

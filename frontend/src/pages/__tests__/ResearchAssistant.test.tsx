import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AgentChatResponse } from '@/api/types'
import { useAssistantStore } from '@/stores/assistantStore'
import ResearchAssistant from '../ResearchAssistant'

const mocks = vi.hoisted(() => ({ chat: vi.fn(), zoteroStatus: vi.fn() }))
vi.mock('@/api/client', () => ({
  agentApi: { chat: mocks.chat },
  zoteroApi: { status: mocks.zoteroStatus },
}))
vi.mock('@/stores/localeStore', () => ({ useLocaleStore: () => ({ locale: 'zh' }) }))

const productHelp: AgentChatResponse = {
  answer: '我可以帮助你检索论文、阅读全文和整理知识库。',
  response_type: 'product_help',
  citations: [],
  tool_steps: [{ tool: 'product_help', status: 'completed', count: 1, detail: '内置使用指南' }],
  provider: null,
  model: null,
  prompt_tokens: 0,
  completion_tokens: 0,
  retrieval_tokens: 0,
  total_tokens: 0,
  retrieval_mode: 'bm25',
  inference_mode: 'none',
  verification_status: 'not_applicable',
  citation_coverage: 0,
  uncited_claim_count: 0,
  invalid_citation_ids: [],
  fallback_used: false,
  model_fallback_used: false,
  model_route: 'none',
  model_attempts: [],
  grounded: false,
  created_at: '2026-09-09T00:00:00Z',
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.chat.mockResolvedValue({ data: productHelp })
  mocks.zoteroStatus.mockResolvedValue({ data: { connected: true } })
  HTMLElement.prototype.scrollIntoView = vi.fn()
  window.scrollTo = vi.fn()
  localStorage.clear()
  useAssistantStore.setState({
    folders: [],
    conversations: [{
      id: 'guide-chat', folderId: null, title: '新对话', messages: [], createdAt: 1, updatedAt: 1,
    }],
    activeConversationId: 'guide-chat',
  })
})

afterEach(cleanup)

function expectNoResearchWarnings() {
  expect(screen.queryByText('材料不足')).not.toBeInTheDocument()
  expect(screen.queryByText(/BM25/)).not.toBeInTheDocument()
  expect(screen.queryByText('本次没有可引用材料')).not.toBeInTheDocument()
  expect(screen.queryByText(/引用覆盖:/)).not.toBeInTheDocument()
  expect(screen.queryByText('模型离线 · 证据回退')).not.toBeInTheDocument()
}

function expectProductGuide() {
  expect(screen.getByText('产品使用指南')).toBeInTheDocument()
  expect(screen.getByText('本回答来自 ScholarNova 内置使用指南，无需论文引用。')).toBeInTheDocument()
  expectNoResearchWarnings()
}

it('renders a capability answer as product help without research-evidence warnings', async () => {
  render(<ResearchAssistant />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '你可以做什么' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))

  expect(await screen.findByText(productHelp.answer)).toBeInTheDocument()
  expect(mocks.chat).toHaveBeenCalledWith(expect.objectContaining({ question: '你可以做什么' }), expect.any(AbortSignal))
  expectProductGuide()
})

it('preserves product-help type and display when persisted history is rehydrated', async () => {
  useAssistantStore.getState().appendMessage('guide-chat', {
    id: 'guide-answer', role: 'assistant', content: productHelp.answer, result: productHelp,
  })
  const storageKey = 'scholarnova-assistant-workspace-v2'
  const persisted = localStorage.getItem(storageKey)!
  expect(JSON.parse(persisted).state.conversations[0].messages[0].result.response_type).toBe('product_help')

  useAssistantStore.setState({ conversations: [], activeConversationId: '' })
  localStorage.setItem(storageKey, persisted)
  await useAssistantStore.persist.rehydrate()

  expect(useAssistantStore.getState().conversations[0].messages[0].result).toEqual(productHelp)
  render(<ResearchAssistant />)
  expect(await screen.findByText(productHelp.answer)).toBeInTheDocument()
  expectProductGuide()
  expect(mocks.chat).not.toHaveBeenCalled()
})

const primaryAttempt = {
  role: 'primary' as const, provider: 'guide-provider', model: 'guide-model', status: 'completed' as const,
  prompt_tokens: 100, completion_tokens: 23, total_tokens: 123, requests: 1, error_type: null,
  request_attempts: 1, responses_received: 1, usage_reports: 1,
}

const modelHelp: AgentChatResponse = {
  ...productHelp,
  inference_mode: 'model', model_route: 'primary', provider: 'guide-provider', model: 'guide-model',
  prompt_tokens: 100, completion_tokens: 23, total_tokens: 123, model_attempts: [primaryAttempt],
}

async function renderSavedAnswer(result: AgentChatResponse) {
  useAssistantStore.getState().appendMessage('guide-chat', {
    id: 'guide-answer', role: 'assistant', content: result.answer, result,
  })
  render(<ResearchAssistant />)
  expect(await screen.findByText(result.answer)).toBeInTheDocument()
}

it('labels model-generated help and displays its model and real token usage', async () => {
  await renderSavedAnswer(modelHelp)

  expect(screen.getByText('AI 使用指导')).toBeInTheDocument()
  expect(screen.getByText('模型: guide-provider/guide-model')).toBeInTheDocument()
  expect(screen.getByText('Token: 123（已返回的用量）')).toBeInTheDocument()
  expect(screen.getByText('请求尝试: 1 · 收到响应: 1 · 用量报告: 1')).toBeInTheDocument()
  expect(screen.getByText('模型尝试 1 次')).toBeInTheDocument()
  expect(screen.getByText(/本回答由已配置模型结合 ScholarNova 产品说明生成/)).toBeInTheDocument()
  expect(screen.queryByText(/本回答来自 ScholarNova 内置使用指南/)).not.toBeInTheDocument()
  expectNoResearchWarnings()
})

it('distinguishes fallback-model help from deterministic built-in help', async () => {
  await renderSavedAnswer({
    ...modelHelp,
    model_route: 'fallback', model_fallback_used: true, provider: 'backup-provider', model: 'backup-model',
    total_tokens: 175,
    model_attempts: [
      { ...primaryAttempt, status: 'unavailable', total_tokens: 52, error_type: 'TimeoutError' },
      { ...primaryAttempt, role: 'fallback', provider: 'backup-provider', model: 'backup-model' },
    ],
  })

  expect(screen.getByText('AI 使用指导 · 备用模型')).toBeInTheDocument()
  expect(screen.getByText('模型: backup-provider/backup-model')).toBeInTheDocument()
  expect(screen.getByText('Token: 175（已返回的用量）')).toBeInTheDocument()
  expect(screen.getByText('模型尝试 2 次')).toBeInTheDocument()
  expect(screen.queryByText('AI 指导未完成 · 本地状态提示')).not.toBeInTheDocument()
  expectNoResearchWarnings()
})

it('reports built-in fallback without hiding tokens consumed by failed model attempts', async () => {
  await renderSavedAnswer({
    ...modelHelp,
    inference_mode: 'deterministic_fallback', model_route: 'deterministic', fallback_used: true,
    total_tokens: 17,
    model_attempts: [{ ...primaryAttempt, status: 'unavailable', total_tokens: 17, error_type: 'TimeoutError' }],
  })

  expect(screen.getByText('AI 指导未完成 · 本地状态提示')).toBeInTheDocument()
  expect(screen.getByText(/模型未完成本次指导，当前显示本地状态提示/)).toBeInTheDocument()
  expect(screen.getByText('Token: 17（已返回的用量）')).toBeInTheDocument()
  expect(screen.getByText('主模型: guide-provider/guide-model · 未完成 · 失败类型: TimeoutError')).toBeInTheDocument()
  expect(screen.getByText('模型尝试 1 次')).toBeInTheDocument()
  expect(screen.queryByText('模型: guide-provider/guide-model')).not.toBeInTheDocument()
  expect(screen.queryByText('AI 使用指导')).not.toBeInTheDocument()
  expectNoResearchWarnings()
})

it('makes a failed help request and missing provider usage visible without claiming zero usage', async () => {
  await renderSavedAnswer({
    ...productHelp,
    inference_mode: 'deterministic_fallback', model_route: 'deterministic', fallback_used: true,
    fallback_reason: 'model_unavailable',
    model_attempts: [{
      ...primaryAttempt, status: 'unavailable', error_type: 'TimeoutError',
      prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, requests: 0,
      request_attempts: 1, responses_received: 0, usage_reports: 0,
    }],
    tool_steps: [{ tool: 'product_help', status: 'completed', count: 1, detail: '助手模型超时，请检查服务商连接后重试。' }],
  })

  expect(screen.getByText('主模型: guide-provider/guide-model · 未完成 · 失败类型: TimeoutError')).toBeInTheDocument()
  expect(screen.getByText('请求尝试: 1 · 收到响应: 0 · 用量报告: 0')).toBeInTheDocument()
  expect(screen.getByText('Token: 未知（未收到或未记录服务商用量）')).toBeInTheDocument()
  expect(screen.getByText(/已尝试模型请求，但服务商未返回用量；这不代表未调用或免费/)).toBeInTheDocument()
  expect(screen.getByText('助手模型超时，请检查服务商连接后重试。')).toBeInTheDocument()
  expect(screen.queryByText('Token: 0')).not.toBeInTheDocument()
  expect(screen.queryByText(/^未调用模型/)).not.toBeInTheDocument()
  expectNoResearchWarnings()
})

it('distinguishes missing model configuration from an attempted request', async () => {
  await renderSavedAnswer({
    ...productHelp,
    inference_mode: 'deterministic_fallback', model_route: 'deterministic', fallback_used: true,
    tool_steps: [{ tool: 'product_help', status: 'completed', count: 1, detail: '未配置可用的助手模型，请先完成模型设置。' }],
  })

  expect(screen.getByText('未调用模型：没有模型尝试记录，请检查模型配置。')).toBeInTheDocument()
  expect(screen.getByText('未配置可用的助手模型，请先完成模型设置。')).toBeInTheDocument()
  expect(screen.getByText('Token: 无用量记录')).toBeInTheDocument()
  expect(screen.queryByText(/已尝试模型请求/)).not.toBeInTheDocument()
})

it('does not infer that an old zero-token record made no model call', async () => {
  await renderSavedAnswer({
    ...modelHelp, total_tokens: 0,
    model_attempts: [{
      ...primaryAttempt, total_tokens: 0, requests: 0,
      request_attempts: undefined, responses_received: undefined, usage_reports: undefined,
    }],
  })

  expect(screen.getByText('历史记录未保存请求状态，不能据此判断是否调用。')).toBeInTheDocument()
  expect(screen.getByText('Token: 未知（未收到或未记录服务商用量）')).toBeInTheDocument()
  expect(screen.queryByText(/^未调用模型/)).not.toBeInTheDocument()
})

it('displays a provider-reported zero as known usage', async () => {
  await renderSavedAnswer({
    ...modelHelp, total_tokens: 0,
    model_attempts: [{ ...primaryAttempt, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }],
  })

  expect(screen.getByText('Token: 0（已返回的用量）')).toBeInTheDocument()
  expect(screen.queryByText(/Token: 未知/)).not.toBeInTheDocument()
})

it('ignores IME confirmation and prevents duplicate sends while a request is pending', async () => {
  let resolveChat!: (response: { data: AgentChatResponse }) => void
  mocks.chat.mockReturnValue(new Promise((resolve) => { resolveChat = resolve }))
  render(<ResearchAssistant />)
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: '你可以做什么' } })
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
  fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
  expect(mocks.chat).not.toHaveBeenCalled()

  fireEvent.keyDown(input, { key: 'Enter' })
  fireEvent.change(input, { target: { value: '再试一次' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(mocks.chat).toHaveBeenCalledOnce()
  expect(screen.getByText(/模型生成较慢时可能需要约 60 秒/)).toBeInTheDocument()
  expectNoResearchWarnings()

  const clearButton = screen.getByRole('button', { name: '清空对话' })
  expect(clearButton).toBeDisabled()
  fireEvent.click(clearButton)
  expect(useAssistantStore.getState().conversations[0].messages).toHaveLength(1)
  await act(async () => { resolveChat({ data: productHelp }) })
  expect(screen.getAllByText(productHelp.answer)).toHaveLength(1)
  expect(useAssistantStore.getState().conversations[0].messages.map((message) => message.role)).toEqual(['user', 'assistant'])
  expect(clearButton).toBeEnabled()
})

it('keeps the pending reply and clear protection with its original conversation', async () => {
  const otherChat = useAssistantStore.getState().createConversation()
  useAssistantStore.getState().appendMessage(otherChat, { id: 'other', role: 'user', content: '另一个会话的内容' })
  useAssistantStore.getState().setActiveConversation('guide-chat')
  let resolveChat!: (response: { data: AgentChatResponse }) => void
  mocks.chat.mockReturnValue(new Promise((resolve) => { resolveChat = resolve }))
  render(<ResearchAssistant />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '你可以做什么' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))

  fireEvent.click(screen.getByRole('button', { name: '另一个会话的内容' }))
  expect(screen.queryByText(/模型生成较慢时可能需要约 60 秒/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '清空对话' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: '清空对话' }))
  await act(async () => { resolveChat({ data: productHelp }) })

  const state = useAssistantStore.getState()
  expect(state.conversations.find((chat) => chat.id === otherChat)?.messages).toHaveLength(0)
  expect(state.conversations.find((chat) => chat.id === 'guide-chat')?.messages.map((message) => message.role)).toEqual(['user', 'assistant'])
  expect(screen.queryByText(productHelp.answer)).not.toBeInTheDocument()
})

it('sends only the active conversation\'s latest six messages for a follow-up', async () => {
  const previousMessages = Array.from({ length: 8 }, (_, index) => ({
    id: `message-${index}`, role: index % 2 ? 'assistant' as const : 'user' as const,
    content: `当前会话消息 ${index}`, ...(index % 2 ? { result: modelHelp } : {}),
  }))
  useAssistantStore.getState().replaceMessages('guide-chat', previousMessages)
  const otherChat = useAssistantStore.getState().createConversation()
  useAssistantStore.getState().appendMessage(otherChat, { id: 'other', role: 'user', content: '另一个会话的私有内容' })
  useAssistantStore.getState().setActiveConversation('guide-chat')
  render(<ResearchAssistant />)

  fireEvent.change(screen.getByRole('textbox'), { target: { value: '那怎样导入 PDF？' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(await screen.findByText(productHelp.answer)).toBeInTheDocument()

  expect(mocks.chat).toHaveBeenCalledOnce()
  expect(mocks.chat).toHaveBeenCalledWith({
    question: '那怎样导入 PDF？',
    history: previousMessages.slice(-6).map(({ role, content }) => ({ role, content })),
    use_knowledge: true,
    use_zotero: true,
  }, expect.any(AbortSignal))
})

const failedHelp: AgentChatResponse = {
  ...productHelp,
  answer: '本次 AI 使用指导未完成：助手模型超时。',
  inference_mode: 'deterministic_fallback', model_route: 'deterministic', fallback_used: true,
  fallback_reason: 'model_unavailable',
  model_attempts: [{
    ...primaryAttempt, status: 'unavailable', error_type: 'TimeoutError',
    prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, requests: 0,
    request_attempts: 1, responses_received: 0, usage_reports: 0,
  }],
}

function appendFailedTurn() {
  useAssistantStore.getState().appendMessage('guide-chat', {
    id: 'retry-question', role: 'user', content: '我该如何使用你？',
  })
  useAssistantStore.getState().appendMessage('guide-chat', {
    id: 'retry-answer', role: 'assistant', content: failedHelp.answer, result: failedHelp,
  })
}

it('retries one turn without duplicate context and retains every previous usage record', async () => {
  const previousMessages = Array.from({ length: 8 }, (_, index) => ({
    id: `before-${index}`, role: index % 2 ? 'assistant' as const : 'user' as const,
    content: `之前的消息 ${index}`,
  }))
  useAssistantStore.getState().replaceMessages('guide-chat', previousMessages)
  appendFailedTurn()
  const reportedFailure: AgentChatResponse = {
    ...failedHelp, answer: '模型返回的指导无效，请重试。', total_tokens: 17,
    model_attempts: [{ ...primaryAttempt, total_tokens: 17 }],
  }
  let resolveChat!: (response: { data: AgentChatResponse }) => void
  mocks.chat.mockReturnValueOnce(new Promise((resolve) => { resolveChat = resolve }))
  const view = render(<ResearchAssistant />)
  const retry = screen.getByRole('button', { name: '再次调用 AI' })
  fireEvent.click(retry)
  fireEvent.click(retry)
  expect(mocks.chat).toHaveBeenCalledOnce()
  expect(retry).toBeDisabled()
  expect(mocks.chat).toHaveBeenCalledWith({
    question: '我该如何使用你？',
    history: previousMessages.slice(-6).map(({ role, content }) => ({ role, content })),
    use_knowledge: true, use_zotero: true,
  }, expect.any(AbortSignal))
  await act(async () => { resolveChat({ data: reportedFailure }) })

  mocks.chat.mockResolvedValueOnce({ data: modelHelp })
  fireEvent.click(screen.getByRole('button', { name: '再次调用 AI' }))
  expect(await screen.findByText(modelHelp.answer)).toBeInTheDocument()
  expect(mocks.chat).toHaveBeenCalledTimes(2)
  expect(mocks.chat.mock.calls[1][0].history).toEqual(mocks.chat.mock.calls[0][0].history)
  const messages = useAssistantStore.getState().conversations[0].messages
  expect(messages).toHaveLength(previousMessages.length + 2)
  expect(messages.filter((message) => message.id === 'retry-question')).toHaveLength(1)
  expect(messages[messages.length - 1]).toEqual({
    id: 'retry-answer', role: 'assistant', content: modelHelp.answer, result: modelHelp,
    priorResults: [failedHelp, reportedFailure],
  })
  expect(screen.getByText('Token: 123（已返回的用量）')).toBeInTheDocument()
  expect(screen.getByText('此前 2 次尝试 · 已知 Token: 17 · 含用量未知的请求')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '再次调用 AI' })).not.toBeInTheDocument()

  const storageKey = 'scholarnova-assistant-workspace-v2'
  const persisted = localStorage.getItem(storageKey)!
  view.unmount()
  useAssistantStore.setState({ conversations: [], activeConversationId: '' })
  localStorage.setItem(storageKey, persisted)
  await useAssistantStore.persist.rehydrate()
  expect(useAssistantStore.getState().conversations[0].messages.at(-1)?.priorResults).toEqual([failedHelp, reportedFailure])
})

it('keeps a retry attached to its original conversation when the user switches chats', async () => {
  appendFailedTurn()
  const otherChat = useAssistantStore.getState().createConversation()
  useAssistantStore.getState().appendMessage(otherChat, { id: 'other-question', role: 'user', content: '另一个会话' })
  useAssistantStore.getState().setActiveConversation('guide-chat')
  let resolveChat!: (response: { data: AgentChatResponse }) => void
  mocks.chat.mockReturnValueOnce(new Promise((resolve) => { resolveChat = resolve }))
  render(<ResearchAssistant />)
  fireEvent.click(screen.getByRole('button', { name: '再次调用 AI' }))
  fireEvent.click(screen.getByRole('button', { name: '另一个会话' }))
  await act(async () => { resolveChat({ data: modelHelp }) })

  const state = useAssistantStore.getState()
  expect(state.activeConversationId).toBe(otherChat)
  expect(state.conversations.find((chat) => chat.id === otherChat)?.messages).toHaveLength(1)
  const original = state.conversations.find((chat) => chat.id === 'guide-chat')!
  expect(original.messages).toHaveLength(2)
  expect(original.messages[1].result).toEqual(modelHelp)
  expect(original.messages[1].priorResults).toEqual([failedHelp])
  expect(screen.queryByText(modelHelp.answer)).not.toBeInTheDocument()
})

it('does not offer an in-place retry after later messages depend on the failed turn', async () => {
  appendFailedTurn()
  useAssistantStore.getState().appendMessage('guide-chat', { id: 'later-question', role: 'user', content: '我刚刚打开了设置。' })
  render(<ResearchAssistant />)
  expect(await screen.findByText(failedHelp.answer)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '再次调用 AI' })).not.toBeInTheDocument()
  expect(mocks.chat).not.toHaveBeenCalled()
})

it('retains the failed answer when a retry transport request rejects', async () => {
  appendFailedTurn()
  mocks.chat.mockRejectedValueOnce(new Error('network unavailable'))
  render(<ResearchAssistant />)
  fireEvent.click(screen.getByRole('button', { name: '再次调用 AI' }))
  expect(await screen.findByText('智能体暂时无法回答，请检查模型和 Zotero 设置。')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '再次调用 AI' })).toBeEnabled()
  expect(useAssistantStore.getState().conversations[0].messages).toHaveLength(2)
  expect(useAssistantStore.getState().conversations[0].messages[1].result).toEqual(failedHelp)
})

it('aborts on leaving and never restores an old reply after returning and clearing', async () => {
  let finish!: (value: any) => void
  mocks.chat.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const view = render(<ResearchAssistant />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '旧问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  const signal = mocks.chat.mock.calls[0][1] as AbortSignal
  view.unmount()
  expect(signal.aborted).toBe(true)
  render(<ResearchAssistant />)
  fireEvent.click(screen.getByRole('button', { name: '清空对话' }))
  await act(async () => { finish({ data: productHelp }) })
  expect(useAssistantStore.getState().conversations[0].messages).toHaveLength(0)
  expect(screen.queryByText(productHelp.answer)).not.toBeInTheDocument()
})

it('does not let an unmounted request append out of order after a new question', async () => {
  let finish!: (value: any) => void
  mocks.chat.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const view = render(<ResearchAssistant />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '旧问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  view.unmount()
  render(<ResearchAssistant />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '新问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText(productHelp.answer)
  await act(async () => { finish({ data: { ...productHelp, answer: '过期回答' } }) })
  expect(screen.queryByText('过期回答')).not.toBeInTheDocument()
  expect(useAssistantStore.getState().conversations[0].messages.map(message => message.content))
    .toEqual(['旧问题', '新问题', productHelp.answer])
})

it.each(['clear', 'delete'])('does not append when its conversation is %s in shared state', async (operation) => {
  let finish!: (value: any) => void
  mocks.chat.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  render(<ResearchAssistant />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '待完成问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  act(() => {
    const store = useAssistantStore.getState()
    if (operation === 'clear') store.clearConversation('guide-chat')
    else store.deleteConversation('guide-chat')
  })
  await act(async () => { finish({ data: productHelp }) })
  expect(useAssistantStore.getState().conversations.every(conversation => conversation.messages.length === 0)).toBe(true)
  expect(screen.queryByText(productHelp.answer)).not.toBeInTheDocument()
})

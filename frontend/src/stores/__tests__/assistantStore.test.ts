import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAssistantStore, isAssistantMessage, useAssistantStore } from '@/stores/assistantStore'

const storageKey = 'scholarnova-assistant-workspace-v2'

describe('assistant workspace', () => {
  afterEach(() => vi.restoreAllMocks())
  beforeEach(async () => {
    useAssistantStore.persist.clearStorage()
    localStorage.clear()
    await useAssistantStore.persist.rehydrate()
    useAssistantStore.setState({
      folders: [],
      conversations: [{
        id: 'chat-a', folderId: null, title: '新对话', messages: [], createdAt: 1, updatedAt: 1,
      }],
      activeConversationId: 'chat-a',
    })
  })

  it('keeps conversation context isolated by research folder', () => {
    const folderId = useAssistantStore.getState().createFolder('交通预测')
    const chatB = useAssistantStore.getState().createConversation(folderId)
    useAssistantStore.getState().appendMessage('chat-a', { id: 'm1', role: 'user', content: '问题 A' })
    useAssistantStore.getState().appendMessage(chatB, { id: 'm2', role: 'user', content: '问题 B' })

    const state = useAssistantStore.getState()
    expect(state.conversations.find((chat) => chat.id === 'chat-a')?.messages[0].content).toBe('问题 A')
    expect(state.conversations.find((chat) => chat.id === chatB)?.messages[0].content).toBe('问题 B')
    expect(state.conversations.find((chat) => chat.id === chatB)?.folderId).toBe(folderId)
  })

  it('moves chats to unfiled instead of deleting them with a folder', () => {
    const folderId = useAssistantStore.getState().createFolder('材料')
    const chatId = useAssistantStore.getState().createConversation(folderId)

    useAssistantStore.getState().deleteFolder(folderId)

    expect(useAssistantStore.getState().conversations.find((chat) => chat.id === chatId)?.folderId).toBeNull()
  })

  it('keeps evidence categories independent of folders and other conversations', () => {
    const store = useAssistantStore.getState()
    const foodFolder = store.createFolder('食品')
    const secondChat = store.createConversation(foodFolder)
    expect(useAssistantStore.getState().conversations.find((chat) => chat.id === secondChat)?.knowledgeCategory).toBeNull()

    store.setKnowledgeCategory('chat-a', '交通')
    store.setKnowledgeCategory(secondChat, '食品')
    store.moveConversation('chat-a', foodFolder)
    store.deleteFolder(foodFolder)

    const conversations = useAssistantStore.getState().conversations
    expect(conversations.find((chat) => chat.id === 'chat-a')).toMatchObject({ knowledgeCategory: '交通', scopeRevision: 1, folderId: null })
    expect(conversations.find((chat) => chat.id === secondChat)).toMatchObject({ knowledgeCategory: '食品', scopeRevision: 1, folderId: null })
  })

  it('advances the context revision only on an actual scope change without deleting history', () => {
    const store = useAssistantStore.getState()
    store.appendMessage('chat-a', { id: 'legacy', role: 'user', content: '旧问题' })
    store.setKnowledgeCategory('chat-a', '食品')
    store.setKnowledgeCategory('chat-a', '食品')
    expect(useAssistantStore.getState().conversations[0].scopeRevision).toBe(1)
    store.setKnowledgeCategory('chat-a', '交通')
    store.setKnowledgeCategory('chat-a', '食品')
    expect(useAssistantStore.getState().conversations[0]).toMatchObject({ knowledgeCategory: '食品', scopeRevision: 3 })
    expect(useAssistantStore.getState().conversations[0].messages).toEqual([{ id: 'legacy', role: 'user', content: '旧问题' }])
    store.clearConversation('chat-a')
    expect(useAssistantStore.getState().conversations[0]).toMatchObject({ knowledgeCategory: '食品', scopeRevision: 3, messages: [] })
  })

  it('persists and rehydrates per-conversation scopes and message scope markers', async () => {
    const store = useAssistantStore.getState()
    store.setKnowledgeCategory('chat-a', '食品')
    store.appendMessage('chat-a', { id: 'food', role: 'user', content: '食品问题', knowledgeCategory: '食品', scopeRevision: 1 })
    const other = store.createConversation()
    store.setKnowledgeCategory(other, '交通')
    const key = 'scholarnova-assistant-workspace-v2'
    const saved = localStorage.getItem(key)!
    useAssistantStore.setState({ conversations: [], activeConversationId: '' })
    localStorage.setItem(key, saved)
    await useAssistantStore.persist.rehydrate()

    const state = useAssistantStore.getState()
    expect(state.activeConversationId).toBe(other)
    expect(state.conversations.find((chat) => chat.id === other)).toMatchObject({ knowledgeCategory: '交通', scopeRevision: 1 })
    expect(state.conversations.find((chat) => chat.id === 'chat-a')).toMatchObject({
      knowledgeCategory: '食品', scopeRevision: 1,
      messages: [{ id: 'food', role: 'user', content: '食品问题', knowledgeCategory: '食品', scopeRevision: 1 }],
    })
  })

  it('restores v2 records with missing scope fields as the compatible default', async () => {
    const key = 'scholarnova-assistant-workspace-v2'
    const saved = localStorage.getItem(key)!
    useAssistantStore.setState({ conversations: [], activeConversationId: '' })
    localStorage.setItem(key, saved)
    await useAssistantStore.persist.rehydrate()
    const legacy = useAssistantStore.getState().conversations[0]
    expect(legacy.knowledgeCategory ?? null).toBeNull()
    expect(legacy.scopeRevision ?? 0).toBe(0)
    useAssistantStore.getState().setKnowledgeCategory('chat-a', null)
    expect(useAssistantStore.getState().conversations[0]).toEqual(legacy)
  })

  it('keeps more than 80 conversations and 40 messages after a fresh store loads', () => {
    const store = useAssistantStore.getState()
    const folderId = store.createFolder('完整研究历史')
    for (let index = 0; index < 85; index++) store.createConversation(folderId)
    for (let index = 0; index < 47; index++) {
      store.appendMessage('chat-a', { id: `message-${index}`, role: 'user', content: `问题 ${index}` })
    }

    const restored = createAssistantStore().getState()
    expect(restored.conversations).toHaveLength(86)
    expect(restored.folders).toHaveLength(1)
    expect(restored.conversations.find((chat) => chat.id === 'chat-a')?.messages).toHaveLength(47)
    expect(restored.conversations.find((chat) => chat.id === 'chat-a')?.messages[0].content).toBe('问题 0')
    expect(restored.storageIssue).toBeNull()
  })

  it('does not trim replacement message history', () => {
    const messages = Array.from({ length: 53 }, (_, index) => ({ id: `m-${index}`, role: 'user' as const, content: `${index}` }))
    useAssistantStore.getState().replaceMessages('chat-a', messages)
    expect(createAssistantStore().getState().conversations[0].messages).toEqual(messages)
  })

  it('keeps memory additions and the previous disk record on quota errors, then safely retries', () => {
    const store = createAssistantStore()
    const id = store.getState().activeConversationId
    store.getState().appendMessage(id, { id: 'saved', role: 'user', content: '已保存的问题' })
    const previous = localStorage.getItem(storageKey)
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Quota reached', 'QuotaExceededError')
    })

    expect(() => store.getState().appendMessage(id, { id: 'pending', role: 'user', content: '仍要保留的新问题' })).not.toThrow()
    expect(() => store.getState().appendMessage(id, { id: 'reply', role: 'assistant', content: '请求完成后的新回答' })).not.toThrow()
    expect(store.getState().conversations.find((chat) => chat.id === id)?.messages).toHaveLength(3)
    expect(store.getState()).toMatchObject({ hasUnsavedChanges: true, storageIssue: { kind: 'quota' } })
    expect(localStorage.getItem(storageKey)).toBe(previous)
    const backup = JSON.parse(store.getState().exportWorkspace())
    expect(backup.state.conversations.find((chat: { id: string }) => chat.id === id).messages).toHaveLength(3)
    expect(backup.recovery.storedRecord).toBe(previous)

    write.mockRestore()
    expect(store.getState().retryPersistence()).toBe(true)
    expect(store.getState()).toMatchObject({ hasUnsavedChanges: false, storageIssue: null })
    expect(createAssistantStore().getState().conversations.find((chat) => chat.id === id)?.messages).toHaveLength(3)
  })

  it('also reports non-quota write failures without interrupting message creation', () => {
    const store = createAssistantStore()
    const previous = localStorage.getItem(storageKey)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage unavailable') })
    expect(() => store.getState().createConversation()).not.toThrow()
    expect(store.getState()).toMatchObject({ hasUnsavedChanges: true, storageIssue: { kind: 'write' } })
    expect(localStorage.getItem(storageKey)).toBe(previous)
    expect(store.getState().retryPersistence()).toBe(false)
  })

  it.each(['retry', 'append'] as const)('does not overwrite another window after quota failure on %s', (operation) => {
    const first = createAssistantStore()
    const firstId = first.getState().activeConversationId
    first.getState().appendMessage(firstId, { id: 'original', role: 'user', content: '原有记录' })
    const second = createAssistantStore()
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Full', 'QuotaExceededError')
    })
    first.getState().appendMessage(firstId, { id: 'pending-a', role: 'user', content: '窗口 A 未保存内容' })
    write.mockRestore()
    const secondId = second.getState().createConversation()
    second.getState().appendMessage(secondId, { id: 'saved-b', role: 'user', content: '窗口 B 已保存内容' })
    const secondDisk = localStorage.getItem(storageKey)!

    if (operation === 'retry') expect(first.getState().retryPersistence()).toBe(false)
    else expect(() => first.getState().appendMessage(firstId, { id: 'later-a', role: 'assistant', content: 'A 的后续回答' })).not.toThrow()
    expect(first.getState()).toMatchObject({ hasUnsavedChanges: true, storageIssue: { kind: 'read' } })
    expect(localStorage.getItem(storageKey)).toBe(secondDisk)
    expect(first.getState().conversations.find((chat) => chat.id === firstId)?.messages.some((message) => message.id === 'pending-a')).toBe(true)
    expect(first.getState().retryPersistence()).toBe(false)
    expect(localStorage.getItem(storageKey)).toBe(secondDisk)
    const backup = JSON.parse(first.getState().exportWorkspace())
    expect(backup.state.conversations.some((chat: { messages: { id: string }[] }) => chat.messages.some((message) => message.id === 'pending-a'))).toBe(true)
    expect(JSON.parse(backup.recovery.storedRecord).state.conversations.some((chat: { id: string }) => chat.id === secondId)).toBe(true)
  })

  it('checks the disk baseline on ordinary writes even without an earlier quota error', () => {
    const first = createAssistantStore()
    const second = createAssistantStore()
    const secondId = second.getState().createConversation()
    const secondDisk = localStorage.getItem(storageKey)!
    const firstId = first.getState().createConversation()
    expect(localStorage.getItem(storageKey)).toBe(secondDisk)
    expect(first.getState()).toMatchObject({ hasUnsavedChanges: true, storageIssue: { kind: 'read' } })
    expect(first.getState().conversations.some((chat) => chat.id === firstId)).toBe(true)
    expect(JSON.parse(first.getState().exportWorkspace()).recovery.storedRecord).toBe(secondDisk)
    expect(createAssistantStore().getState().conversations.some((chat) => chat.id === secondId)).toBe(true)
  })

  it.each(['{broken JSON', 'null', '{}', '{"version":3,"state":{}}', '{"version":2,"state":{"folders":[],"conversations":[{}],"activeConversationId":"x"}}'])(
    'protects an unreadable or invalid stored record: %s', (raw) => {
      localStorage.setItem(storageKey, raw)
      const store = createAssistantStore()
      expect(store.getState()).toMatchObject({ storageIssue: { kind: 'invalid' }, hasUnsavedChanges: false })
      const id = store.getState().activeConversationId
      expect(() => store.getState().appendMessage(id, { id: 'new', role: 'user', content: '内存中的新内容' })).not.toThrow()
      expect(store.getState().retryPersistence()).toBe(false)
      expect(localStorage.getItem(storageKey)).toBe(raw)
      const backup = JSON.parse(store.getState().exportWorkspace())
      expect(backup.recovery.storedRecord).toBe(raw)
      expect(backup.state.conversations[0].messages[0].content).toBe('内存中的新内容')
      expect(store.getState().hasUnsavedChanges).toBe(true)
    }
  )

  it('restores a repaired record and retains independent work created while reading was blocked', () => {
    const previous = localStorage.getItem(storageKey)!
    localStorage.setItem(storageKey, '{broken')
    const store = createAssistantStore()
    const id = store.getState().activeConversationId
    store.getState().appendMessage(id, { id: 'new', role: 'user', content: '恢复前的新内容' })

    localStorage.setItem(storageKey, previous)
    expect(store.getState().retryPersistence()).toBe(true)
    const restored = createAssistantStore().getState()
    expect(restored.conversations.some((chat) => chat.id === 'chat-a')).toBe(true)
    expect(restored.conversations.find((chat) => chat.id === id)?.messages[0].content).toBe('恢复前的新内容')
    expect(store.getState()).toMatchObject({ hasUnsavedChanges: false, storageIssue: null })
  })

  it('restores repaired disk data without persisting an empty initial placeholder over it', () => {
    const previous = localStorage.getItem(storageKey)!
    localStorage.setItem(storageKey, '{broken')
    const store = createAssistantStore()
    localStorage.setItem(storageKey, previous)
    expect(store.getState().retryPersistence()).toBe(true)
    expect(store.getState().conversations).toHaveLength(1)
    expect(store.getState().activeConversationId).toBe('chat-a')
  })

  it('blocks writes after a read exception and recovers without losing the original disk record', () => {
    const previous = localStorage.getItem(storageKey)!
    const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Access denied', 'SecurityError') })
    const write = vi.spyOn(Storage.prototype, 'setItem')
    const store = createAssistantStore()
    expect(store.getState().storageIssue?.kind).toBe('read')
    const id = store.getState().createConversation()
    store.getState().appendMessage(id, { id: 'pending', role: 'user', content: '读取失败时的新内容' })
    expect(write).not.toHaveBeenCalled()
    expect(JSON.parse(store.getState().exportWorkspace()).recovery.readUnavailable).toBe(true)
    expect(store.getState().retryPersistence()).toBe(false)

    read.mockRestore()
    expect(localStorage.getItem(storageKey)).toBe(previous)
    expect(store.getState().retryPersistence()).toBe(true)
    expect(store.getState().conversations.some((chat) => chat.id === 'chat-a')).toBe(true)
    expect(createAssistantStore().getState().conversations.find((chat) => chat.id === id)?.messages).toHaveLength(1)
  })

  it('refuses to guess between conflicting versions during recovery and exports both copies', async () => {
    const store = createAssistantStore()
    const previous = localStorage.getItem(storageKey)!
    localStorage.setItem(storageKey, '{broken')
    await store.persist.rehydrate()
    store.getState().appendMessage('chat-a', { id: 'pending', role: 'user', content: '仅在当前窗口的新问题' })
    localStorage.setItem(storageKey, previous)

    expect(store.getState().retryPersistence()).toBe(false)
    expect(store.getState().storageIssue?.message).toContain('不同版本')
    expect(localStorage.getItem(storageKey)).toBe(previous)
    const backup = JSON.parse(store.getState().exportWorkspace())
    expect(backup.state.conversations[0].messages).toHaveLength(1)
    expect(JSON.parse(backup.recovery.storedRecord).state.conversations[0].messages).toHaveLength(0)
    expect(store.getState().hasUnsavedChanges).toBe(true)
  })

  it('exports every folder and conversation but no unrelated model configuration', () => {
    localStorage.setItem('unrelated-model-configuration', 'test-secret-not-for-export')
    useAssistantStore.getState().createFolder('学习资料')
    useAssistantStore.getState().appendMessage('chat-a', { id: 'export', role: 'user', content: '导出问题' })
    const exported = useAssistantStore.getState().exportWorkspace()
    const backup = JSON.parse(exported)
    expect(backup.version).toBe(2)
    expect(backup.state.folders[0].name).toBe('学习资料')
    expect(backup.state.conversations[0].messages[0].content).toBe('导出问题')
    expect(backup.state.activeConversationId).toBe('chat-a')
    expect(exported).not.toContain('test-secret-not-for-export')
    expect(exported).not.toContain('retryPersistence')
  })

  const oldResult = {
    answer: '旧版回答', citations: [{ id: 'S1', source: 'paper', title: '原文', page: 8 }],
    tool_steps: [{ tool: 'paper_fulltext_search', status: 'completed', count: 1 }], total_tokens: 123,
  }

  it('keeps historical v2 result records without requiring newer optional diagnostics', () => {
    const previous = JSON.parse(localStorage.getItem(storageKey)!)
    previous.state.conversations[0].messages = [{ id: 'old-answer', role: 'assistant', content: '旧版回答', result: oldResult, priorResults: [oldResult] }]
    localStorage.setItem(storageKey, JSON.stringify(previous))
    const restored = createAssistantStore().getState()
    expect(restored.storageIssue).toBeNull()
    expect(restored.conversations[0].messages[0].result).toEqual(oldResult)
    expect(restored.conversations[0].messages[0].priorResults).toEqual([oldResult])
    expect(restored.conversations[0].messages[0].result).not.toHaveProperty('model_attempts')
  })

  it('does not allow extra persisted properties to replace store actions', () => {
    const previous = JSON.parse(localStorage.getItem(storageKey)!)
    previous.state.appendMessage = 'not a function'
    previous.state.retryPersistence = null
    localStorage.setItem(storageKey, JSON.stringify(previous))
    const restored = createAssistantStore()
    expect(typeof restored.getState().appendMessage).toBe('function')
    expect(typeof restored.getState().retryPersistence).toBe('function')
    expect(() => restored.getState().appendMessage('chat-a', { id: 'safe', role: 'user', content: '正文仍保留' })).not.toThrow()
  })

  it.each([
    { result: {} },
    { priorResults: [{}] },
    { result: { ...oldResult, citations: null } },
    { result: { ...oldResult, citations: [null] } },
    { result: { ...oldResult, citations: [{ id: 'S1', title: { unsafe: 'object' }, source: 'paper' }] } },
    { result: { ...oldResult, tool_steps: {} } },
    { result: { ...oldResult, tool_steps: [null] } },
    { result: { ...oldResult, tool_steps: [{ tool: 'product_help', status: 'completed', detail: { unsafe: 'object' } }] } },
    { result: { ...oldResult, model_attempts: {} } },
    { result: { ...oldResult, model_attempts: [null] } },
    { result: { ...oldResult, model_attempts: [{ provider: 'test', model: { unsafe: 'object' }, status: 'completed' }] } },
    { result: { ...oldResult, invalid_citation_ids: 'S1' } },
    { result: { ...oldResult, total_tokens: { unsafe: 'object' } } },
    { priorResults: {} },
  ])('protects records with malformed answer traces rather than loading a crashing message: %j', (trace) => {
    const previous = JSON.parse(localStorage.getItem(storageKey)!)
    const message = { id: 'broken-answer', role: 'assistant', content: '必须保留的历史正文', ...trace }
    previous.state.conversations[0].messages = [message]
    const raw = JSON.stringify(previous)
    localStorage.setItem(storageKey, raw)
    const restored = createAssistantStore()
    expect(restored.getState().storageIssue?.kind).toBe('invalid')
    expect(restored.getState().conversations[0].messages).toEqual([])
    expect(restored.getState().retryPersistence()).toBe(false)
    expect(localStorage.getItem(storageKey)).toBe(raw)
    expect(JSON.parse(restored.getState().exportWorkspace()).recovery.storedRecord).toBe(raw)
    expect(isAssistantMessage(message)).toBe(false)
  })

  it.each(['folders', 'conversations', 'messages'] as const)('protects duplicate identifiers at the %s level', (level) => {
    const previous = JSON.parse(localStorage.getItem(storageKey)!)
    if (level === 'folders') previous.state.folders = [{ id: 'same', name: '一', createdAt: 1 }, { id: 'same', name: '二', createdAt: 2 }]
    else if (level === 'conversations') previous.state.conversations.push({ ...previous.state.conversations[0], title: '同 ID 的不同内容' })
    else previous.state.conversations[0].messages = [{ id: 'same', role: 'user', content: '一' }, { id: 'same', role: 'user', content: '二' }]
    const raw = JSON.stringify(previous)
    localStorage.setItem(storageKey, raw)
    const restored = createAssistantStore()
    expect(restored.getState().storageIssue?.kind).toBe('invalid')
    expect(restored.getState().retryPersistence()).toBe(false)
    expect(localStorage.getItem(storageKey)).toBe(raw)
  })
})

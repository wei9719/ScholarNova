import { beforeEach, describe, expect, it } from 'vitest'
import { useAssistantStore } from '@/stores/assistantStore'

describe('assistant workspace', () => {
  beforeEach(() => {
    localStorage.clear()
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
})

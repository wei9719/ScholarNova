import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { AgentChatResponse } from '@/api/types'

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  knowledgeCategory?: string | null
  scopeRevision?: number
  result?: AgentChatResponse
  priorResults?: AgentChatResponse[]
}

export interface ResearchFolder {
  id: string
  name: string
  createdAt: number
}

export interface ResearchConversation {
  id: string
  folderId: string | null
  title: string
  messages: AssistantMessage[]
  knowledgeCategory?: string | null
  scopeRevision?: number
  createdAt: number
  updatedAt: number
}

interface AssistantState {
  folders: ResearchFolder[]
  conversations: ResearchConversation[]
  activeConversationId: string
  createFolder: (name: string) => string
  deleteFolder: (id: string) => void
  createConversation: (folderId?: string | null) => string
  deleteConversation: (id: string) => void
  setActiveConversation: (id: string) => void
  moveConversation: (id: string, folderId: string | null) => void
  setKnowledgeCategory: (id: string, category: string | null) => void
  appendMessage: (conversationId: string, message: AssistantMessage) => void
  replaceMessage: (conversationId: string, messageId: string, message: AssistantMessage) => void
  replaceMessages: (conversationId: string, messages: AssistantMessage[]) => void
  clearConversation: (id: string) => void
}

function newId() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`
}

function newConversation(folderId: string | null = null): ResearchConversation {
  const now = Date.now()
  return { id: newId(), folderId, title: '新对话', messages: [], knowledgeCategory: null, scopeRevision: 0, createdAt: now, updatedAt: now }
}

const initialConversation = newConversation()

export const useAssistantStore = create<AssistantState>()(
  persist(
    (set) => ({
      folders: [],
      conversations: [initialConversation],
      activeConversationId: initialConversation.id,

      createFolder: (name) => {
        const id = newId()
        set((state) => ({
          folders: [...state.folders, { id, name: name.trim().slice(0, 40), createdAt: Date.now() }],
        }))
        return id
      },

      deleteFolder: (id) => set((state) => ({
        folders: state.folders.filter((folder) => folder.id !== id),
        conversations: state.conversations.map((conversation) =>
          conversation.folderId === id ? { ...conversation, folderId: null } : conversation
        ),
      })),

      createConversation: (folderId = null) => {
        const conversation = newConversation(folderId)
        set((state) => ({
          conversations: [conversation, ...state.conversations].slice(0, 80),
          activeConversationId: conversation.id,
        }))
        return conversation.id
      },

      deleteConversation: (id) => set((state) => {
        const remaining = state.conversations.filter((conversation) => conversation.id !== id)
        if (remaining.length > 0) {
          return {
            conversations: remaining,
            activeConversationId: state.activeConversationId === id ? remaining[0].id : state.activeConversationId,
          }
        }
        const conversation = newConversation()
        return { conversations: [conversation], activeConversationId: conversation.id }
      }),

      setActiveConversation: (activeConversationId) => set({ activeConversationId }),

      moveConversation: (id, folderId) => set((state) => ({
        conversations: state.conversations.map((conversation) =>
          conversation.id === id ? { ...conversation, folderId, updatedAt: Date.now() } : conversation
        ),
      })),

      setKnowledgeCategory: (id, category) => set((state) => ({
        conversations: state.conversations.map((conversation) =>
          conversation.id === id && (conversation.knowledgeCategory ?? null) !== category
            ? {
                ...conversation,
                knowledgeCategory: category,
                scopeRevision: (conversation.scopeRevision ?? 0) + 1,
                updatedAt: Date.now(),
              }
            : conversation
        ),
      })),

      appendMessage: (conversationId, message) => set((state) => ({
        conversations: state.conversations.map((conversation) => {
          if (conversation.id !== conversationId) return conversation
          const firstQuestion = message.role === 'user' && conversation.messages.length === 0
          return {
            ...conversation,
            title: firstQuestion ? message.content.trim().slice(0, 34) : conversation.title,
            messages: [...conversation.messages, message].slice(-40),
            updatedAt: Date.now(),
          }
        }),
      })),

      replaceMessage: (conversationId, messageId, message) => set((state) => ({
        conversations: state.conversations.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                messages: conversation.messages.map((current) =>
                  current.id === messageId ? message : current
                ),
                updatedAt: Date.now(),
              }
            : conversation
        ),
      })),

      replaceMessages: (conversationId, messages) => set((state) => ({
        conversations: state.conversations.map((conversation) =>
          conversation.id === conversationId
            ? { ...conversation, messages: messages.slice(-40), updatedAt: Date.now() }
            : conversation
        ),
      })),

      clearConversation: (id) => set((state) => ({
        conversations: state.conversations.map((conversation) =>
          conversation.id === id
            ? { ...conversation, title: '新对话', messages: [], updatedAt: Date.now() }
            : conversation
        ),
      })),
    }),
    {
      name: 'scholarnova-assistant-workspace-v2',
      version: 2,
      partialize: (state) => ({
        folders: state.folders,
        conversations: state.conversations,
        activeConversationId: state.activeConversationId,
      }),
    }
  )
)

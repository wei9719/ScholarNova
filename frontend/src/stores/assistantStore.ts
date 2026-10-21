import { create } from 'zustand'
import { persist, type PersistStorage, type StorageValue } from 'zustand/middleware'
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

export interface AssistantStorageIssue {
  kind: 'quota' | 'write' | 'read' | 'invalid'
  message: string
}

interface AssistantState {
  folders: ResearchFolder[]
  conversations: ResearchConversation[]
  activeConversationId: string
  storageIssue: AssistantStorageIssue | null
  hasUnsavedChanges: boolean
  retryPersistence: () => boolean
  exportWorkspace: () => string
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

type AssistantWorkspace = Pick<AssistantState, 'folders' | 'conversations' | 'activeConversationId'>
const storageKey = 'scholarnova-assistant-workspace-v2'

function workspaceSnapshot(state: AssistantWorkspace): AssistantWorkspace {
  return { folders: state.folders, conversations: state.conversations, activeConversationId: state.activeConversationId }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function optionalFields(value: Record<string, unknown>, keys: string[], valid: (field: unknown) => boolean) {
  return keys.every((key) => value[key] === undefined || valid(value[key]))
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function nullableString(value: unknown) {
  return value === null || typeof value === 'string'
}

function validResult(value: unknown): boolean {
  if (!isRecord(value) || typeof value.answer !== 'string' || !Array.isArray(value.citations)
    || !Array.isArray(value.tool_steps)) return false
  const citationsValid = value.citations.every((citation: unknown) => isRecord(citation)
    && typeof citation.id === 'string' && typeof citation.title === 'string'
    && typeof citation.source === 'string' && ['paper', 'knowledge', 'zotero'].includes(citation.source)
    && optionalFields(citation, ['doi', 'item_id', 'url', 'section'], nullableString)
    && optionalFields(citation, ['page', 'chunk_index'], (field) => field === null || isNumber(field)))
  const toolsValid = value.tool_steps.every((step: unknown) => isRecord(step) && typeof step.tool === 'string'
    && typeof step.status === 'string' && ['completed', 'skipped', 'unavailable'].includes(step.status)
    && optionalFields(step, ['count'], isNumber)
    && optionalFields(step, ['detail'], (field) => typeof field === 'string'))
  const attemptsValid = value.model_attempts === undefined || value.model_attempts === null
    || (Array.isArray(value.model_attempts) && value.model_attempts.every((attempt: unknown) => isRecord(attempt)
      && typeof attempt.provider === 'string' && typeof attempt.model === 'string'
      && typeof attempt.status === 'string' && ['completed', 'unavailable'].includes(attempt.status)
      && optionalFields(attempt, ['role'], (field) => field === 'primary' || field === 'fallback')
      && optionalFields(attempt, ['error_type'], nullableString)
      && optionalFields(attempt, ['prompt_tokens', 'completion_tokens', 'total_tokens', 'requests', 'request_attempts', 'responses_received', 'usage_reports'], isNumber)))
  return citationsValid && toolsValid && attemptsValid
    && optionalFields(value, ['provider', 'model', 'fallback_reason'], nullableString)
    && optionalFields(value, ['response_type', 'retrieval_mode', 'inference_mode', 'verification_status', 'model_route', 'created_at'], (field) => typeof field === 'string')
    && optionalFields(value, ['prompt_tokens', 'completion_tokens', 'retrieval_tokens', 'total_tokens', 'citation_coverage', 'uncited_claim_count'], isNumber)
    && optionalFields(value, ['fallback_used', 'model_fallback_used', 'grounded'], (field) => typeof field === 'boolean')
    && (value.invalid_citation_ids === undefined || value.invalid_citation_ids === null
      || (Array.isArray(value.invalid_citation_ids) && value.invalid_citation_ids.every((id: unknown) => typeof id === 'string')))
}

export function isAssistantMessage(value: unknown): value is AssistantMessage {
  return isRecord(value) && typeof value.id === 'string' && (value.role === 'user' || value.role === 'assistant')
    && typeof value.content === 'string'
    && optionalFields(value, ['knowledgeCategory'], nullableString)
    && optionalFields(value, ['scopeRevision'], isNumber)
    && (value.result === undefined || value.result === null || validResult(value.result))
    && (value.priorResults === undefined || value.priorResults === null
      || (Array.isArray(value.priorResults) && value.priorResults.every(validResult)))
}

function uniqueIds(values: { id: string }[]) {
  return new Set(values.map((value) => value.id)).size === values.length
}

function validWorkspace(value: unknown): value is AssistantWorkspace {
  if (!isRecord(value) || !Array.isArray(value.folders) || !Array.isArray(value.conversations)
    || typeof value.activeConversationId !== 'string') return false
  return value.folders.every((folder: unknown) => isRecord(folder) && typeof folder.id === 'string'
    && typeof folder.name === 'string' && isNumber(folder.createdAt))
    && value.conversations.every((chat: unknown) => isRecord(chat) && typeof chat.id === 'string'
      && (chat.folderId === null || typeof chat.folderId === 'string') && typeof chat.title === 'string'
      && isNumber(chat.createdAt) && isNumber(chat.updatedAt) && Array.isArray(chat.messages)
      && optionalFields(chat, ['knowledgeCategory'], nullableString)
      && optionalFields(chat, ['scopeRevision'], isNumber)
      && chat.messages.every(isAssistantMessage) && uniqueIds(chat.messages))
    && uniqueIds(value.folders) && uniqueIds(value.conversations)
}

// A read failure can leave two independent workspaces. Only merge disjoint or identical records;
// conflicting copies require an export instead of guessing which user's fields to discard.
function mergeRecoveredWorkspace(saved: AssistantWorkspace, current: AssistantWorkspace): AssistantWorkspace | null {
  const merge = <T extends { id: string }>(older: T[], newer: T[]): T[] | null => {
    const records = new Map(older.map((record) => [record.id, record]))
    for (const record of newer) {
      const previous = records.get(record.id)
      if (previous && JSON.stringify(previous) !== JSON.stringify(record)) return null
      records.set(record.id, record)
    }
    return [...records.values()]
  }
  const folders = merge(saved.folders, current.folders)
  const conversations = merge(saved.conversations, current.conversations)
  if (!folders || !conversations) return null
  return { folders, conversations, activeConversationId: current.activeConversationId || saved.activeConversationId }
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

export function createAssistantStore() {
  const initialConversation = newConversation()
  let readBlocked = false
  let lastKnownRaw: string | null | undefined
  let pending = false
  let issue: AssistantStorageIssue | null = null
  let reportStatus = (_issue: AssistantStorageIssue | null, _pending: boolean) => {}
  const report = (nextIssue: AssistantStorageIssue | null, nextPending = pending) => {
    issue = nextIssue
    pending = nextPending
    reportStatus(issue, pending)
  }
  const storage: PersistStorage<AssistantWorkspace> = {
    getItem: (name) => {
      let raw: string | null
      try {
        raw = localStorage.getItem(name)
      } catch (error) {
        readBlocked = true
        report({ kind: 'read', message: '无法读取本地对话。原有记录未被覆盖，请先导出备份，再重试保存。' })
        throw error
      }
      let saved: unknown
      try {
        saved = raw === null ? null : JSON.parse(raw)
        if (raw !== null && (!isRecord(saved) || (saved.version !== undefined && saved.version !== 2)
          || !validWorkspace(saved.state))) throw new Error('Invalid assistant workspace')
      } catch (error) {
        readBlocked = true
        report({ kind: 'invalid', message: '本地对话记录无法解析，已暂停覆盖原记录。请导出备份，修复后再重试保存。' })
        throw error
      }
      if (pending && lastKnownRaw !== undefined && raw !== lastKnownRaw) {
        rejectConflict()
        throw new Error('Assistant workspace changed in another window')
      }
      lastKnownRaw = raw
      readBlocked = false
      if (!pending) report(null)
      return saved === null ? null : {
        state: workspaceSnapshot((saved as StorageValue<AssistantWorkspace>).state), version: 2,
      }
    },
    setItem: (name, value) => {
      if (readBlocked) {
        report(issue, true)
        return
      }
      let currentRaw: string | null
      try {
        currentRaw = localStorage.getItem(name)
      } catch {
        readBlocked = true
        report({ kind: 'read', message: '无法核对磁盘中的对话，新内容仅保留在当前窗口。原有记录未被覆盖，请先导出备份。' }, true)
        return
      }
      if (currentRaw !== lastKnownRaw) {
        rejectConflict()
        return
      }
      try {
        const raw = JSON.stringify(value)
        localStorage.setItem(name, raw)
        lastKnownRaw = raw
        report(null, false)
      } catch (error) {
        const quota = error instanceof DOMException && (error.name === 'QuotaExceededError' || error.name === 'NS_ERROR_DOM_QUOTA_REACHED')
        report({
          kind: quota ? 'quota' : 'write',
          message: quota
            ? '本地存储空间不足，新内容仅保留在当前窗口。历史记录未被删除，请先导出备份，再重试保存。'
            : '本地对话保存失败，新内容仅保留在当前窗口。历史记录未被删除，请先导出备份，再重试保存。',
        }, true)
      }
    },
    removeItem: (name) => {
      try {
        localStorage.removeItem(name)
        lastKnownRaw = null
        readBlocked = false
        report(null, false)
      } catch {
        report({ kind: 'write', message: '无法清理本地对话存储，原有记录仍被保留。' })
      }
    },
  }
  const rejectConflict = () => {
    readBlocked = true
    report({ kind: 'read', message: '磁盘记录与当前窗口存在不同版本，未自动覆盖任何一份。请先导出完整备份，再处理冲突。' }, true)
  }
  const initializer = persist<AssistantState, [], [], AssistantWorkspace>(
    (set, get) => ({
      folders: [],
      conversations: [initialConversation],
      activeConversationId: initialConversation.id,
      storageIssue: null,
      hasUnsavedChanges: false,
      retryPersistence: () => {
        if (readBlocked) {
          try {
            const saved = storage.getItem(storageKey) as StorageValue<AssistantWorkspace> | null
            if (saved) {
              const restored = pending ? mergeRecoveredWorkspace(saved.state, get()) : saved.state
              if (!restored) { rejectConflict(); return false }
              set(restored)
              return issue === null
            }
          } catch { return false }
        }
        storage.setItem(storageKey, { state: workspaceSnapshot(get()), version: 2 })
        return issue === null
      },
      exportWorkspace: () => {
        let recovery: { storedRecord?: string | null; readUnavailable?: boolean } | undefined
        if (issue || pending) {
          try { recovery = { storedRecord: localStorage.getItem(storageKey) } } catch {
            recovery = { readUnavailable: true }
          }
        }
        return JSON.stringify({ version: 2, state: workspaceSnapshot(get()), exportedAt: new Date().toISOString(), recovery }, null, 2)
      },

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
          conversations: [conversation, ...state.conversations],
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
            messages: [...conversation.messages, message],
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
            ? { ...conversation, messages, updatedAt: Date.now() }
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
      name: storageKey,
      version: 2,
      storage,
      partialize: workspaceSnapshot,
      skipHydration: true,
      merge: (saved, current) => {
        if (!saved) return current
        const restored = pending ? mergeRecoveredWorkspace(saved as AssistantWorkspace, current) : saved as AssistantWorkspace
        if (!restored) { rejectConflict(); return { ...current, storageIssue: issue, hasUnsavedChanges: pending } }
        return { ...current, ...restored, storageIssue: issue, hasUnsavedChanges: pending }
      },
    }
  )
  const safeInitializer: typeof initializer = (set, get, api) => {
    // Status reports use the original setter so reporting an error cannot recursively write again.
    reportStatus = (storageIssue, hasUnsavedChanges) => set({ storageIssue, hasUnsavedChanges })
    return initializer(set, get, api)
  }
  const store = create<AssistantState>()(safeInitializer)
  void store.persist.rehydrate()
  return store
}

export const useAssistantStore = createAssistantStore()

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssistantStorageGuard from '../AssistantStorageGuard'
import { useAssistantStore } from '@/stores/assistantStore'
import { useLocaleStore } from '@/stores/localeStore'

function Workspace({ initial = '/assistant' }: { initial?: string }) {
  return (
    <MemoryRouter initialEntries={[initial]}>
      <AssistantStorageGuard />
      <nav>
        <Link to="/search">打开搜索</Link>
        <Link to="/assistant?paper=paper-1">打开伴读</Link>
        <Link to="/assistant">打开对话</Link>
      </nav>
      <Routes>
        <Route path="/search" element={<p>搜索工作区</p>} />
        <Route path="/assistant" element={<p>智能体工作区</p>} />
      </Routes>
    </MemoryRouter>
  )
}

function beforeUnload() {
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  return event.defaultPrevented
}

describe('assistant storage protection across workspaces', () => {
  beforeEach(() => {
    useAssistantStore.persist.clearStorage()
    localStorage.clear()
    useLocaleStore.setState({ locale: 'zh' })
    useAssistantStore.setState({
      folders: [],
      conversations: [{ id: 'chat', folderId: null, title: '新对话', messages: [], createdAt: 1, updatedAt: 1 }],
      activeConversationId: 'chat',
      storageIssue: null,
      hasUnsavedChanges: false,
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it.each(['打开搜索', '打开伴读'])('keeps the close warning after a failed save and navigating to %s, then releases it after retry', (destination) => {
    render(<Workspace />)
    expect(beforeUnload()).toBe(false)
    const previous = localStorage.getItem('scholarnova-assistant-workspace-v2')
    const quota = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    act(() => useAssistantStore.getState().appendMessage('chat', { id: 'pending', role: 'assistant', content: '尚未保存的新回答' }))
    expect(beforeUnload()).toBe(true)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: destination }))
    expect(screen.getByRole('alert')).toHaveTextContent('对话尚未保存，关闭或刷新可能丢失。')
    expect(screen.getByRole('link', { name: '返回智能体检查保存' })).toHaveAttribute('href', '/assistant')
    expect(beforeUnload()).toBe(true)
    expect(localStorage.getItem('scholarnova-assistant-workspace-v2')).toBe(previous)
    expect(useAssistantStore.getState().conversations[0].messages[0].content).toBe('尚未保存的新回答')

    quota.mockRestore()
    act(() => { expect(useAssistantStore.getState().retryPersistence()).toBe(true) })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(beforeUnload()).toBe(false)
    expect(JSON.parse(localStorage.getItem('scholarnova-assistant-workspace-v2')!).state.conversations[0].messages).toHaveLength(1)
  })

  it('returns from the reading workspace to the chat without duplicating its detailed warning', () => {
    render(<Workspace initial="/assistant?paper=paper-1" />)
    const quota = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    act(() => useAssistantStore.getState().appendMessage('chat', { id: 'pending', role: 'user', content: '待保存' }))
    expect(screen.getByRole('alert')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '返回智能体检查保存' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(beforeUnload()).toBe(true)
    quota.mockRestore()
  })

  it('does not show a duplicate banner for the ordinary chat, including an empty paper parameter', () => {
    const quota = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    useAssistantStore.getState().appendMessage('chat', { id: 'pending', role: 'user', content: '待保存' })
    render(<Workspace initial="/assistant/?paper=%20%20" />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(beforeUnload()).toBe(true)
    quota.mockRestore()
  })

  it('removes the listener when the whole application unmounts', () => {
    const quota = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    useAssistantStore.getState().appendMessage('chat', { id: 'pending', role: 'user', content: '待保存' })
    const view = render(<Workspace initial="/search" />)
    expect(beforeUnload()).toBe(true)
    view.unmount()
    expect(beforeUnload()).toBe(false)
    quota.mockRestore()
  })
})

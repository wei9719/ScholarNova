import { useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAssistantStore } from '@/stores/assistantStore'
import { useLocaleStore } from '@/stores/localeStore'

export default function AssistantStorageGuard() {
  const hasUnsavedChanges = useAssistantStore((state) => state.hasUnsavedChanges)
  const storageIssue = useAssistantStore((state) => state.storageIssue)
  const locale = useLocaleStore((state) => state.locale)
  const { pathname, search } = useLocation()

  useEffect(() => {
    if (!hasUnsavedChanges) return
    const protectUnsavedChats = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', protectUnsavedChats)
    return () => window.removeEventListener('beforeunload', protectUnsavedChats)
  }, [hasUnsavedChanges])

  const inChat = pathname.replace(/\/+$/, '') === '/assistant'
    && !new URLSearchParams(search).get('paper')?.trim()
  if (inChat || (!hasUnsavedChanges && !storageIssue)) return null

  const chinese = locale === 'zh'
  const message = hasUnsavedChanges
    ? (chinese ? '对话尚未保存，关闭或刷新可能丢失。' : 'Chat changes are not saved and may be lost on close or refresh.')
    : (chinese ? '对话记录需要检查，请返回智能体处理。' : 'Chat storage needs attention. Return to the assistant to check it.')

  return (
    <div role="alert" className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-[var(--ui-text)]">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <p>{message}</p>
        <Link to="/assistant" className="font-semibold underline underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
          {chinese ? '返回智能体检查保存' : 'Return to assistant to check saving'}
        </Link>
      </div>
    </div>
  )
}

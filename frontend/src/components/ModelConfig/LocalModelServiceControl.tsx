import { useEffect, useRef, useState } from 'react'

type ServiceState = {
  status: 'disabled' | 'starting' | 'running' | 'stopped' | 'error'
  message?: string
}
type ServiceAction = 'status' | 'start' | 'stop'
type LocalModelBridge = Record<ServiceAction, () => Promise<ServiceState>>

export default function LocalModelServiceControl({ isZh }: { isZh: boolean }) {
  // Only the three fixed local-model operations are exposed, never Electron itself.
  const bridge = (window as Window & { scholarLocalModel?: LocalModelBridge }).scholarLocalModel
  const [state, setState] = useState<ServiceState | null>(null)
  const [pending, setPending] = useState<ServiceAction | null>(bridge ? 'status' : null)
  const busy = useRef(Boolean(bridge))

  useEffect(() => {
    if (!bridge) return
    let active = true
    busy.current = true
    setPending('status')
    Promise.resolve().then(() => bridge.status()).then((result) => {
      if (active) setState(result)
    }).catch((error: unknown) => {
      if (active) setState({ status: 'error', message: error instanceof Error ? error.message : undefined })
    }).finally(() => {
      if (active) { busy.current = false; setPending(null) }
    })
    return () => { active = false }
  }, [bridge])

  const run = async (action: ServiceAction) => {
    if (!bridge || busy.current) return
    busy.current = true
    setPending(action)
    try {
      setState(await bridge[action]())
    } catch (error: unknown) {
      setState({ status: 'error', message: error instanceof Error ? error.message : undefined })
    } finally {
      busy.current = false
      setPending(null)
    }
  }

  if (!bridge) return <p className="text-xs text-gray-500 dark:text-gray-400">{isZh
    ? '启动/停止本机模型仅在支持此功能的桌面版中可用；浏览器版请在桌面端管理服务。'
    : 'Starting and stopping the local model requires the supported desktop app. Manage the service there when using a browser.'}</p>

  const statusText = {
    disabled: isZh ? '本机服务未启用，请先完成桌面端配置' : 'Local service is disabled. Complete desktop setup first.',
    starting: isZh ? '进程正在启动，尚未确认模型就绪' : 'The process is starting; model readiness is not confirmed.',
    running: isZh ? '进程已启动，请测试连接确认模型就绪' : 'The process has started. Test the connection to confirm model readiness.',
    stopped: isZh ? '本机进程已停止' : 'The local process has stopped.',
    error: isZh ? '本机服务操作失败' : 'The local service operation failed.',
  }
  const disabled = state?.status === 'disabled'

  return <div className="rounded-lg border border-gray-200 px-3 py-2 text-xs dark:border-gray-700">
    <p role="status" aria-live="polite" className="font-medium text-gray-700 dark:text-gray-300">
      {pending === 'start' ? (isZh ? '正在启动本机进程…' : 'Starting the local process…')
        : pending === 'stop' ? (isZh ? '正在停止本机进程…' : 'Stopping the local process…')
          : state ? statusText[state.status] : (isZh ? '正在读取本机服务状态…' : 'Reading local service status…')}
    </p>
    {state?.message && <p role={state.status === 'error' ? 'alert' : undefined} className="mt-1 break-words text-gray-500 dark:text-gray-400">{state.message}</p>}
    <div className="mt-2 flex flex-wrap gap-2">
      <button type="button" onClick={() => void run('start')}
        disabled={Boolean(pending) || disabled || state?.status === 'running' || state?.status === 'starting'}
        className="config-btn config-btn-secondary !px-3 !py-1.5">{isZh ? '启动本机模型' : 'Start local model'}</button>
      <button type="button" onClick={() => void run('stop')}
        disabled={Boolean(pending) || disabled || state?.status === 'stopped'}
        className="config-btn config-btn-secondary !px-3 !py-1.5">{isZh ? '停止本机模型' : 'Stop local model'}</button>
      <button type="button" onClick={() => void run('status')} disabled={Boolean(pending)}
        className="config-btn config-btn-secondary !px-3 !py-1.5">{isZh ? '刷新服务状态' : 'Refresh service status'}</button>
    </div>
    <p className="mt-2 text-gray-500 dark:text-gray-400">{isZh
      ? '停止将释放本应用本机模型的资源占用，供 LLM-Twin 使用；不会修改云端配置或模型选择，也不会自动转云。仅停止本次运行；下次打开桌面版仍按已保存的启用设置启动。'
      : 'Stopping releases this app’s local-model resources for LLM-Twin. It does not change cloud settings or your model selection, and does not switch to a cloud model. This stops only the current run; the next desktop launch follows the saved enable setting.'}</p>
  </div>
}

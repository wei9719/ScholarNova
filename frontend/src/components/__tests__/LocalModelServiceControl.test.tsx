import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import LocalModelServiceControl from '../ModelConfig/LocalModelServiceControl'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function renderDesktop(status = 'stopped', message?: string) {
  const bridge = {
    status: vi.fn().mockResolvedValue({ status, message }),
    start: vi.fn().mockResolvedValue({ status: 'running' }),
    stop: vi.fn().mockResolvedValue({ status: 'stopped' }),
  }
  vi.stubGlobal('scholarLocalModel', bridge)
  await act(async () => { render(<LocalModelServiceControl isZh />) })
  return bridge
}

it('shows only a desktop support hint when the bridge is unavailable', () => {
  render(<LocalModelServiceControl isZh />)
  expect(screen.getByText(/仅在支持此功能的桌面版中可用/)).toBeInTheDocument()
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
})

it('reports process state without claiming model readiness', async () => {
  const bridge = await renderDesktop('running')
  expect(bridge.status).toHaveBeenCalledOnce()
  expect(screen.getByRole('status')).toHaveTextContent('进程已启动，请测试连接确认模型就绪')
  expect(screen.getByRole('button', { name: '启动本机模型' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '停止本机模型' })).toBeEnabled()
  expect(bridge.start).not.toHaveBeenCalled()
  expect(bridge.stop).not.toHaveBeenCalled()
})

it('keeps unconfigured service controls disabled', async () => {
  const bridge = await renderDesktop('disabled', '请先配置本机服务')
  expect(screen.getByText('请先配置本机服务')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '启动本机模型' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '停止本机模型' })).toBeDisabled()
  expect(bridge.start).not.toHaveBeenCalled()
})

it('disables controls during start and ignores repeated clicks', async () => {
  const bridge = await renderDesktop()
  let complete!: (value: { status: string }) => void
  bridge.start.mockImplementationOnce(() => new Promise((resolve) => { complete = resolve }))
  const start = screen.getByRole('button', { name: '启动本机模型' })
  fireEvent.click(start)
  fireEvent.click(start)
  expect(bridge.start).toHaveBeenCalledOnce()
  expect(bridge.start).toHaveBeenCalledWith()
  expect(start).toBeDisabled()
  expect(screen.getByRole('button', { name: '停止本机模型' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '刷新服务状态' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('正在启动本机进程')
  await act(async () => complete({ status: 'running' }))
  expect(screen.getByRole('status')).toHaveTextContent('进程已启动，请测试连接确认模型就绪')
})

it('stops through the narrow bridge and explains resource release without cloud switching', async () => {
  const bridge = await renderDesktop('running')
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '停止本机模型' })))
  expect(bridge.stop).toHaveBeenCalledOnce()
  expect(bridge.stop).toHaveBeenCalledWith()
  expect(screen.getByRole('status')).toHaveTextContent('本机进程已停止')
  expect(screen.getByRole('button', { name: '启动本机模型' })).toBeEnabled()
  expect(screen.getByText(/供 LLM-Twin 使用；不会修改云端配置或模型选择，也不会自动转云/)).toBeInTheDocument()
})

it('shows rejected operations and permits an explicit status refresh', async () => {
  const bridge = await renderDesktop()
  bridge.start.mockRejectedValueOnce(new Error('本机推理服务启动失败，请检查桌面端日志'))
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '启动本机模型' })))
  expect(screen.getByRole('alert')).toHaveTextContent('本机推理服务启动失败，请检查桌面端日志')
  expect(screen.getByRole('status')).toHaveTextContent('本机服务操作失败')
  expect(screen.getByRole('button', { name: '启动本机模型' })).toBeEnabled()
  bridge.status.mockResolvedValueOnce({ status: 'running' })
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '刷新服务状态' })))
  expect(screen.getByRole('status')).toHaveTextContent('进程已启动，请测试连接确认模型就绪')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('shows service-returned errors and does not auto-start or retry', async () => {
  const bridge = await renderDesktop('error', '端口已被其他程序占用')
  expect(screen.getByRole('alert')).toHaveTextContent('端口已被其他程序占用')
  expect(bridge.start).not.toHaveBeenCalled()
  expect(bridge.stop).not.toHaveBeenCalled()
})

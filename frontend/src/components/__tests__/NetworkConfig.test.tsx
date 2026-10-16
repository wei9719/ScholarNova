import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import NetworkConfig from '../NetworkConfig'

const mocks = vi.hoisted(() => ({ getConfig: vi.fn(), rankingStatus: vi.fn() }))
vi.mock('@/api/client', () => ({
  networkApi: { getConfig: mocks.getConfig },
  papersApi: { rankingStatus: mocks.rankingStatus },
}))
beforeEach(() => {
  mocks.getConfig.mockResolvedValue({ data: {} })
  mocks.rankingStatus.mockResolvedValue({ data: { entry_count: 0 } })
})
afterEach(cleanup)

it('distinguishes year-and-source qualified JIF from OpenAlex two-year citation metrics', async () => {
  render(<NetworkConfig />)
  expect(
    await screen.findByRole('heading', {
      name: '期刊分区、影响因子与开放指标',
    }),
  ).toBeInTheDocument()
  expect(
    screen.getByText(/OpenAlex 两年平均被引不等于 JIF 影响因子/),
  ).toBeInTheDocument()
  expect(
    screen.getByText(/影响因子须同时保留报告年份与数据来源/),
  ).toBeInTheDocument()
  expect(screen.getByText('导入分区 / JIF CSV/JSON')).toBeInTheDocument()
})

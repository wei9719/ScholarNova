import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import AnalysisViz from '../AnalysisViz'

vi.mock('@/stores/localeStore', () => ({
  useLocaleStore: () => ({ locale: 'zh' }),
}))

afterEach(cleanup)

function textPanel() {
  return screen.getByRole('heading', { name: 'AI 文字分析' }).parentElement!
}

it('keeps the full architecture heading and every later planning section readable', () => {
  render(
    <AnalysisViz
      analysis={[
        '1. **研究问题**',
        '比较缺失模式与实验设置。',
        '2. **实施安排**',
        '先跑基线。',
        '3. **研究架构图**',
        '数据层：保留原始观测与缺失掩码。',
        '4. **验收标准**',
        '最后检查误差、运行时间和失败样本。',
      ].join('\n')}
    />,
  )
  const text = within(textPanel())
  expect(text.getByText(/^3\.\s*研究架构图$/)).toBeInTheDocument()
  expect(text.getByText('4. 验收标准')).toBeInTheDocument()
  expect(
    text.getByText('最后检查误差、运行时间和失败样本。'),
  ).toBeInTheDocument()
  expect(textPanel()).not.toHaveTextContent('3. **')
})

it('does not cut prose mentioning architecture while a separate structured diagram is shown', () => {
  render(
    <AnalysisViz
      analysis={
        '这一段提到研究架构图只是为了说明图文关系。\n后续正文仍需完整保留。\n最终步骤是复现实验。'
      }
      architectureJson={{
        title: '实验结构',
        layers: [{ name: '数据层', modules: [{ name: '观测输入' }] }],
      }}
    />,
  )
  expect(
    within(textPanel()).getByText('这一段提到研究架构图只是为了说明图文关系。'),
  ).toBeInTheDocument()
  expect(
    within(textPanel()).getByText('最终步骤是复现实验。'),
  ).toBeInTheDocument()
  expect(screen.getByLabelText('研究架构图')).toBeInTheDocument()
  expect(screen.getByText('观测输入')).toBeInTheDocument()
})

it('hides only a complete architecture control block, keeping semantic text on both sides', () => {
  const architecture = {
    title: '确定性结构',
    layers: [{ name: '证据层', modules: [{ name: '来源快照' }] }],
  }
  render(
    <AnalysisViz
      analysis={[
        '控制块之前的研究依据。',
        `<ARCH_JSON>${JSON.stringify(architecture)}</ARCH_JSON>`,
        '控制块之后的实验边界。',
      ].join('\n')}
    />,
  )
  expect(textPanel()).toHaveTextContent('控制块之前的研究依据。')
  expect(textPanel()).toHaveTextContent('控制块之后的实验边界。')
  expect(textPanel()).not.toHaveTextContent('ARCH_JSON')
  expect(textPanel()).not.toHaveTextContent('"layers"')
  expect(screen.getByLabelText('研究架构图')).toBeInTheDocument()
})

it('does not silently discard following facts when an architecture block is unfinished', () => {
  render(
    <AnalysisViz
      analysis={
        '研究依据在此。\n<ARCH_JSON>{"layers": [\n这一行实验结论不能因缺少控制闭合标签而消失。'
      }
    />,
  )
  expect(textPanel()).toHaveTextContent(
    '这一行实验结论不能因缺少控制闭合标签而消失。',
  )
})

it('retains the 2000-character expand and collapse behavior without losing later sections', () => {
  render(
    <AnalysisViz
      analysis={`研究概述。\n${'前文内容。'.repeat(410)}\n3. **研究架构图**\n架构后的完整验收要求。`}
    />,
  )
  expect(
    within(textPanel()).queryByText('架构后的完整验收要求。'),
  ).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '展开全部' }))
  expect(
    within(textPanel()).getByText('架构后的完整验收要求。'),
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '收起' }))
  expect(
    within(textPanel()).queryByText('架构后的完整验收要求。'),
  ).not.toBeInTheDocument()
})

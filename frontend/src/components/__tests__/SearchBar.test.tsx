import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useLocaleStore } from '@/stores/localeStore'
import SearchBar from '../SearchBar/SearchBar'

beforeEach(() => useLocaleStore.getState().setLocale('zh'))

afterEach(() => {
  cleanup()
  useLocaleStore.getState().setLocale('zh')
  useLocaleStore.persist.clearStorage()
})

describe('production SearchBar', () => {
  it('exposes a search landmark and a Chinese accessible input name', () => {
    render(<SearchBar onSubmit={vi.fn()} />)

    expect(screen.getByRole('search')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '输入你的研究问题...' }))
      .toHaveAttribute('placeholder', '输入你的研究问题...')
    expect(screen.getByRole('button', { name: '搜索' })).toBeDisabled()
  })

  it('uses English labels from the real locale store', () => {
    useLocaleStore.getState().setLocale('en')
    render(<SearchBar onSubmit={vi.fn()} />)

    expect(screen.getByRole('textbox', { name: 'Enter your research question...' }))
      .toHaveAttribute('placeholder', 'Enter your research question...')
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled()
  })

  it('uses a custom placeholder as the input accessible name', () => {
    render(<SearchBar placeholder="检索论文题名或研究问题" onSubmit={vi.fn()} />)

    expect(screen.getByRole('textbox', { name: '检索论文题名或研究问题' }))
      .toHaveAttribute('placeholder', '检索论文题名或研究问题')
  })

  it('accepts typing and submits a trimmed query once on click', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchBar onSubmit={onSubmit} />)

    const input = screen.getByRole('textbox')
    await act(async () => { await user.type(input, '  deep learning  ') })
    expect(input).toHaveValue('  deep learning  ')
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '搜索' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: '搜索' }))
    expect(onSubmit).toHaveBeenCalledOnce()
    expect(onSubmit).toHaveBeenCalledWith('deep learning')
  })

  it('submits from an Enter key interaction', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchBar onSubmit={onSubmit} />)

    await act(async () => { await user.type(screen.getByRole('textbox'), 'NLP{Enter}') })
    expect(onSubmit).toHaveBeenCalledOnce()
    expect(onSubmit).toHaveBeenCalledWith('NLP')
  })

  it('allows the same keyword to be submitted again without editing it', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchBar defaultValue="transformer" onSubmit={onSubmit} />)

    const button = screen.getByRole('button', { name: '搜索' })
    await user.click(button)
    await user.click(button)
    expect(screen.getByRole('textbox')).toHaveValue('transformer')
    expect(onSubmit.mock.calls).toEqual([['transformer'], ['transformer']])
  })

  it.each(['', '   ', '\t  '])('refuses empty or whitespace-only input %j', async (value) => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchBar defaultValue={value} onSubmit={onSubmit} />)

    const button = screen.getByRole('button', { name: '搜索' })
    expect(button).toBeDisabled()
    await user.click(button)
    // Exercise the submit guard independently of the disabled button.
    fireEvent.submit(screen.getByRole('search'))
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('blocks editing, clicks and form submission while busy', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchBar defaultValue="traffic" loading onSubmit={onSubmit} />)

    const input = screen.getByRole('textbox')
    const button = screen.getByRole('button', { name: '正在搜索...' })
    expect(input).toBeDisabled()
    expect(button).toBeDisabled()
    await user.type(input, 'new query{Enter}')
    await user.click(button)
    fireEvent.submit(screen.getByRole('search'))
    expect(input).toHaveValue('traffic')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('preserves the query and permits an identical search after loading ends', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const view = render(<SearchBar defaultValue="traffic" onSubmit={onSubmit} />)

    await user.click(screen.getByRole('button', { name: '搜索' }))
    view.rerender(<SearchBar defaultValue="traffic" loading onSubmit={onSubmit} />)
    expect(screen.getByRole('button', { name: '正在搜索...' })).toBeDisabled()

    view.rerender(<SearchBar defaultValue="traffic" loading={false} onSubmit={onSubmit} />)
    expect(screen.getByRole('textbox')).toBeEnabled()
    expect(screen.getByRole('textbox')).toHaveValue('traffic')
    const button = screen.getByRole('button', { name: '搜索' })
    expect(button).toBeEnabled()
    await user.click(button)
    expect(onSubmit.mock.calls).toEqual([['traffic'], ['traffic']])
  })

  it('supports keyboard focus and activating the search button without a pointer', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchBar defaultValue="evidence" onSubmit={onSubmit} />)

    await user.tab()
    expect(screen.getByRole('textbox')).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: '搜索' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onSubmit).toHaveBeenCalledOnce()
    expect(onSubmit).toHaveBeenCalledWith('evidence')
  })
})

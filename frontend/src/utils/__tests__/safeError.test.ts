import { describe, expect, it } from 'vitest'
import { safeErrorMessage } from '../safeError'

describe('safeErrorMessage', () => {
  it('preserves an actionable message without changing business metadata', () => {
    const detail = Object.freeze({ code: 'queue_full', message: '请求较多，请稍后重试', retry_after: 2 })
    const error = { response: { status: 429, data: { detail } } }
    expect(safeErrorMessage(error, '请求失败')).toBe('请求较多，请稍后重试')
    expect(error.response.data.detail).toBe(detail)
    expect(error.response.status).toBe(429)
    expect(detail.retry_after).toBe(2)
    expect(safeErrorMessage({ response: { data: { detail: '连接超时，请检查网络' } } }, '请求失败')).toBe('连接超时，请检查网络')
  })

  it('extracts only bounded validation messages, not inputs, locations or context', () => {
    const error = { response: { data: { detail: [
      { msg: '模型名称不能为空', input: 'PRIVATE_KEY', loc: ['body', 'api_key'], ctx: { original: 'PRIVATE_KEY' } },
      null, { msg: { private: 'PRIVATE_KEY' } },
      { msg: '参数超出范围' }, { msg: '地址格式不正确' }, { msg: '第四条被省略' },
    ] } }, config: { data: 'PRIVATE_KEY' } }
    expect(safeErrorMessage(error, '请求失败')).toBe('模型名称不能为空；参数超出范围；地址格式不正确')
  })

  it.each([undefined, null, {}, new Error('POST https://example.test?key=PRIVATE_KEY'),
    { response: { data: { detail: { unknown: 'PRIVATE_KEY' } } } },
    { response: { data: { detail: [{ input: 'PRIVATE_KEY' }] } } },
    { response: { data: { detail: 42 } } },
    { response: { data: { detail: '   ' } } },
  ])('uses the provided localized fallback for an unrecognized shape', (error) => {
    expect(safeErrorMessage(error, '请求失败，请重试')).toBe('请求失败，请重试')
  })
})

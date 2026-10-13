import { expect, it } from 'vitest'
import { searchDates } from '../searchDates'
it('uses local today and current calendar years, not a hard-coded cutoff', () => {
  const now = new Date(2027, 0, 2, 0, 30)
  expect(searchDates('year', '', '', now)).toEqual({ date_from: '2027-01-01', date_to: '2027-01-02' })
  expect(searchDates('three', '', '', now)).toEqual({ date_from: '2025-01-01', date_to: '2027-01-02' })
  expect(searchDates('custom', '2021', '2025', now)).toEqual({ date_from: '2021-01-01', date_to: '2025-12-31' })
  expect(searchDates('all', '', '', now)).toEqual({})
})
it.each([['2027', '2020'], ['', '2025'], ['1900', '2050'], ['2020.5', '2025']])('rejects invalid range %s–%s', (from, to) => {
  expect(() => searchDates('custom', from, to, new Date(2026, 9, 13))).toThrow()
})

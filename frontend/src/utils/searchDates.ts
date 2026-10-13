export type SearchPeriod = 'all' | 'year' | 'three' | 'custom'

// Use the reader's local calendar day, not UTC (which can be yesterday in China).
export function searchDates(period: SearchPeriod, fromYear: string, toYear: string, now = new Date()): { date_from?: string; date_to?: string } {
  if (period === 'all') return {}
  const current = now.getFullYear()
  const start = period === 'year' ? current : period === 'three' ? current - 2 : Number(fromYear)
  const end = period === 'custom' ? Number(toYear) : current
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1000 || end > current || start > end) throw new Error('INVALID_YEAR_RANGE')
  const today = `${current}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  return { date_from: `${start}-01-01`, date_to: end === current ? today : `${end}-12-31` }
}

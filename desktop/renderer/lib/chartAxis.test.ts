import { TickMarkType } from 'lightweight-charts'
import type { IChartApi, Time } from 'lightweight-charts'

import { formatRangeLabel, monthlyTickMarkFormatter, visibleRangeIso } from './chartAxis'

describe('formatRangeLabel', () => {
  it('shows dates only when both endpoints are midnight (a daily range)', () => {
    expect(formatRangeLabel('2026-04-10T00:00:00+00:00', '2026-04-20T00:00:00+00:00')).toBe(
      '2026-04-10 → 2026-04-20',
    )
  })

  it('appends the HH:MM when an endpoint is intraday', () => {
    expect(formatRangeLabel('2026-04-10T13:30:00+00:00', '2026-04-10T15:45:00+00:00')).toBe(
      '2026-04-10 13:30 → 2026-04-10 15:45',
    )
  })
})

describe('monthlyTickMarkFormatter', () => {
  // 2021-01-01T00:00:00Z and 2021-07-01T00:00:00Z as unix seconds.
  const JAN_2021 = 1609459200 as unknown as Time
  const JUL_2021 = 1625097600 as unknown as Time

  it('renders the year for a Year tick', () => {
    expect(monthlyTickMarkFormatter(JAN_2021, TickMarkType.Year, 'en-US')).toBe('2021')
  })

  it('renders the abbreviated UTC month for a non-year tick', () => {
    expect(monthlyTickMarkFormatter(JUL_2021, TickMarkType.Month, 'en-US')).toBe('Jul')
  })

  it('reads the month in UTC (not the host timezone) so a start-of-month bar never rolls back a day', () => {
    // Midnight UTC on the 1st must stay in its own month regardless of locale offset.
    expect(monthlyTickMarkFormatter(JAN_2021, TickMarkType.Month, 'en-US')).toBe('Jan')
  })
})

describe('visibleRangeIso', () => {
  function chartWithRange(range: { from: Time; to: Time } | null): IChartApi {
    return {
      timeScale: () => ({ getVisibleRange: () => range }),
    } as unknown as IChartApi
  }

  it('maps a numeric [from, to] window to ISO strings', () => {
    // 2021-01-01 .. 2021-07-01 (unix seconds).
    const chart = chartWithRange({
      from: 1609459200 as unknown as Time,
      to: 1625097600 as unknown as Time,
    })
    expect(visibleRangeIso(chart)).toEqual({
      range_start: '2021-01-01T00:00:00.000Z',
      range_end: '2021-07-01T00:00:00.000Z',
    })
  })

  it('returns null when the chart has no visible range', () => {
    expect(visibleRangeIso(chartWithRange(null))).toBeNull()
  })

  it('returns null when the range endpoints are not numeric (business-day strings)', () => {
    const chart = chartWithRange({
      from: '2021-01-01' as unknown as Time,
      to: '2021-07-01' as unknown as Time,
    })
    expect(visibleRangeIso(chart)).toBeNull()
  })
})

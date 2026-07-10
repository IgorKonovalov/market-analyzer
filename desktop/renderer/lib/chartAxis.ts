/**
 * Pure axis / visible-range formatters for `CandlestickChart` (Plan 0072 phase 8
 * decomposition — lifted verbatim out of the component, no behaviour change).
 *
 * `monthlyTickMarkFormatter` (Plan 0050 phase 7) and `formatRangeLabel` (the
 * gesture range-selection label) are pure string builders; `visibleRangeIso`
 * reads the chart's current window into ISO strings for the pattern-scan
 * triggers. All UTC, matching the bar timestamps.
 */
import { TickMarkType } from 'lightweight-charts'
import type { IChartApi, Time, TickMarkType as TickMarkTypeT } from 'lightweight-charts'

/** Human-readable label for a selected [start, end] window. UTC (matching the
 * bar timestamps); the time is shown only when it isn't midnight, so a daily
 * range reads as plain dates. */
export function formatRangeLabel(startIso: string, endIso: string): string {
  const fmt = (iso: string): string => {
    const date = iso.slice(0, 10)
    const time = iso.slice(11, 16)
    return time === '00:00' ? date : `${date} ${time}`
  }
  return `${fmt(startIso)} → ${fmt(endIso)}`
}

/** Axis tick formatter for the monthly (`1mo`) timeframe (Plan 0050 phase 7).
 * Month-spaced bars must read as month/year, never day-of-month or intraday
 * labels (which lightweight-charts' default would emit for some zoom levels,
 * producing repeated "1" day labels). Year boundaries show the year; every other
 * tick shows the abbreviated month. UTC, matching the bar timestamps. */
export function monthlyTickMarkFormatter(
  time: Time,
  tickMarkType: TickMarkTypeT,
  locale: string,
): string {
  const ms = typeof time === 'number' ? time * 1000 : Date.parse(String(time))
  const date = new Date(ms)
  if (tickMarkType === TickMarkType.Year) {
    return String(date.getUTCFullYear())
  }
  return date.toLocaleDateString(locale, { month: 'short', timeZone: 'UTC' })
}

/** The chart's current visible [from, to] window as ISO strings, or null when
 * the chart has no data / no resolvable numeric range yet. Shared by the
 * pattern-scan triggers (markers + trendlines). */
export function visibleRangeIso(
  chart: IChartApi,
): { range_start: string; range_end: string } | null {
  const range = chart.timeScale().getVisibleRange()
  const toIso = (t: Time): string | null =>
    typeof t === 'number' ? new Date(t * 1000).toISOString() : null
  const rangeStart = range ? toIso(range.from) : null
  const rangeEnd = range ? toIso(range.to) : null
  if (rangeStart === null || rangeEnd === null) return null
  return { range_start: rangeStart, range_end: rangeEnd }
}

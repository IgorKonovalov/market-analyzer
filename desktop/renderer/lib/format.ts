/**
 * Display formatters for backtest metrics, trades, and timestamps
 * (Plan 0008 phase 5).
 *
 * Tiny on purpose — every function is one line of `Intl.NumberFormat` or
 * stdlib date-fmt. The shared use case is the BacktestView's metrics table
 * + trade log and RecentBacktestsView's list; if a third caller arrives
 * with conflicting needs, split per-caller rather than growing this file.
 *
 * Locale is intentionally `en-US` so the same number/date display works in
 * every region (the app is single-user / single-locale today; a future
 * i18n plan owns localization properly).
 */

const PCT_FORMAT = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: 'exceptZero',
})

const RATIO_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: 'exceptZero',
})

const USD_FORMAT = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const USD_SIGNED_FORMAT = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: 'exceptZero',
})

const INT_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

/** Format a unitless fraction (e.g. 0.1234) as a signed percent ("+12.34%"). */
export function formatPct(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return PCT_FORMAT.format(value)
}

/** Format an unbounded ratio (e.g. Sharpe) as a signed two-decimal string. */
export function formatRatio(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return RATIO_FORMAT.format(value)
}

/** Format USD without sign. */
export function formatUsd(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return USD_FORMAT.format(value)
}

/** Format USD with explicit +/− sign (used for P&L $ in the trade log). */
export function formatUsdSigned(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return USD_SIGNED_FORMAT.format(value)
}

/** Format an integer count with thousand separators. */
export function formatInt(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return INT_FORMAT.format(value)
}

/** Format an ISO 8601 timestamp as a UTC date (YYYY-MM-DD). */
export function formatDate(iso: string): string {
  return iso.slice(0, 10)
}

/** Format an ISO 8601 timestamp as a UTC date + minute (YYYY-MM-DD HH:mm). */
export function formatDateTime(iso: string): string {
  // ISO format is `YYYY-MM-DDTHH:mm:ss...` — slice keeps locale out of the
  // picture and avoids `new Date(...).toLocaleString()` which would render
  // in the user's timezone.
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`
}

const ISO_DURATION = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/

/** Format an ISO 8601 duration (e.g. "P2DT6H", "PT30M") as a compact human
 * string ("2d 6h", "30m"). A `timedelta` never carries months or years, so only
 * D/H/M/S components appear. Falls back to the raw string if unparseable. */
export function formatDuration(iso: string): string {
  const match = ISO_DURATION.exec(iso)
  if (match === null) return iso
  const [, days, hours, minutes, seconds] = match
  const parts: string[] = []
  if (days) parts.push(`${days}d`)
  if (hours) parts.push(`${hours}h`)
  if (minutes) parts.push(`${minutes}m`)
  // Only surface seconds when nothing coarser is present (sub-minute windows).
  if (seconds && !days && !hours && !minutes) parts.push(`${Math.round(Number(seconds))}s`)
  return parts.length > 0 ? parts.join(' ') : '0m'
}

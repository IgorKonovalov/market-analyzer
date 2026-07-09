/**
 * List of persisted backtest runs (Plan 0008 phase 5).
 *
 * Backed by `GET /backtests` — server returns rows ordered by `finished_at`
 * desc by default. The user can re-sort on the four numeric / time columns
 * by clicking the header; sort is purely client-side (re-fetching to change
 * order would be wasted work given the row count is bounded at `MAX_LIST_LIMIT`).
 *
 * Clicking a row hands the `run_id` back to the parent via `onSelect`;
 * App.tsx swaps the view to `BacktestView` and primes `useBacktestResult`
 * with the click-through path.
 */
import { useEffect, useMemo, useState } from 'react'

import { listBacktests } from '../api/backtests'
import { formatDate, formatDateTime, formatInt, formatPct, formatRatio } from '../lib/format'
import { t } from '../lib/i18n'
import type { BacktestRunSummary } from '../types/sidecar/backtest-run-summary'
import styles from './RecentBacktestsView.module.css'

type SortColumn = 'finished_at' | 'total_return' | 'sharpe' | 'max_drawdown'
type SortDirection = 'asc' | 'desc'

interface SortState {
  column: SortColumn
  direction: SortDirection
}

interface FetchState {
  status: 'idle' | 'loading' | 'ready' | 'error'
  rows: BacktestRunSummary[]
  error: string | null
}

const INITIAL: FetchState = { status: 'idle', rows: [], error: null }

export interface RecentBacktestsViewProps {
  onSelect: (runId: string) => void
  /** Bump to force a refetch (e.g. when a new `run.completed` envelope lands
   * while this view is mounted). Optional — when omitted the list is fetched
   * once on mount. */
  refreshKey?: number
}

export function RecentBacktestsView({
  onSelect,
  refreshKey,
}: RecentBacktestsViewProps): JSX.Element {
  const [state, setState] = useState<FetchState>(INITIAL)
  const [sort, setSort] = useState<SortState>({ column: 'finished_at', direction: 'desc' })

  useEffect(() => {
    let cancelled = false
    setState((s) => ({ ...s, status: 'loading', error: null }))
    listBacktests()
      .then((rows) => {
        if (cancelled) return
        setState({ status: 'ready', rows, error: null })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : t('recent.loadError')
        setState({ status: 'error', rows: [], error: message })
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  const sortedRows = useMemo(() => sortRows(state.rows, sort), [state.rows, sort])

  const onHeaderClick = (column: SortColumn): void => {
    setSort((prev) =>
      prev.column === column
        ? { column, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
        : { column, direction: 'desc' },
    )
  }

  return (
    <section className={styles.root} aria-label={t('recent.title')}>
      <header className={styles.header}>
        <h2 className={styles.title}>{t('recent.title')}</h2>
        <p className={styles.lede}>{t('recent.lede')}</p>
      </header>

      {state.status === 'loading' && (
        <div className={styles.statusBlock} role="status">
          {t('recent.loading')}
        </div>
      )}
      {state.status === 'error' && (
        <div className={styles.error} role="alert">
          {state.error ?? t('recent.loadError')}
        </div>
      )}
      {state.status === 'ready' && state.rows.length === 0 && (
        <div className={styles.statusBlock} role="status" data-testid="recent-empty">
          {t('recent.empty')}
        </div>
      )}
      {state.status === 'ready' && state.rows.length > 0 && (
        <div className={styles.tableScroll}>
          <table className={styles.table} data-testid="recent-table">
            <thead>
              <tr>
                <th scope="col">{t('recent.colStrategy')}</th>
                <th scope="col">{t('recent.colSymbol')}</th>
                <th scope="col">{t('recent.colTimeframe')}</th>
                <th scope="col">{t('recent.colRange')}</th>
                <SortableHeader
                  label={t('recent.colTotalReturn')}
                  column="total_return"
                  sort={sort}
                  onClick={onHeaderClick}
                />
                <SortableHeader
                  label={t('recent.colSharpe')}
                  column="sharpe"
                  sort={sort}
                  onClick={onHeaderClick}
                />
                <SortableHeader
                  label={t('recent.colMaxDd')}
                  column="max_drawdown"
                  sort={sort}
                  onClick={onHeaderClick}
                />
                <th scope="col" className={styles.numCol}>
                  {t('recent.colTrades')}
                </th>
                <SortableHeader
                  label={t('recent.colFinished')}
                  column="finished_at"
                  sort={sort}
                  onClick={onHeaderClick}
                />
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => (
                <tr
                  key={row.run_id}
                  className={styles.row}
                  data-testid="recent-row"
                  data-run-id={row.run_id}
                  onClick={() => onSelect(row.run_id)}
                  tabIndex={0}
                  role="button"
                  aria-label={t('recent.openBacktestLabel', { runId: row.run_id })}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      onSelect(row.run_id)
                    }
                  }}
                >
                  <td>
                    {row.strategy_id} v{row.strategy_version}
                  </td>
                  <td>{row.symbol}</td>
                  <td>{row.timeframe}</td>
                  <td>
                    {formatDate(row.range_start)} → {formatDate(row.range_end)}
                  </td>
                  <td className={styles.numCol}>{formatPct(row.total_return)}</td>
                  <td className={styles.numCol}>{formatRatio(row.sharpe)}</td>
                  <td className={styles.numCol}>{formatPct(row.max_drawdown)}</td>
                  <td className={styles.numCol}>{formatInt(row.trade_count)}</td>
                  <td>{formatDateTime(row.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

interface SortableHeaderProps {
  label: string
  column: SortColumn
  sort: SortState
  onClick: (column: SortColumn) => void
}

function SortableHeader({ label, column, sort, onClick }: SortableHeaderProps): JSX.Element {
  const active = sort.column === column
  const arrow = active ? (sort.direction === 'asc' ? ' ▲' : ' ▼') : ''
  return (
    <th scope="col" className={styles.numCol}>
      <button
        type="button"
        className={styles.sortButton}
        onClick={() => onClick(column)}
        aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        {label}
        {arrow}
      </button>
    </th>
  )
}

function sortRows(rows: BacktestRunSummary[], sort: SortState): BacktestRunSummary[] {
  const sorted = [...rows]
  const factor = sort.direction === 'asc' ? 1 : -1
  sorted.sort((a, b) => {
    if (sort.column === 'finished_at') {
      return factor * a.finished_at.localeCompare(b.finished_at)
    }
    const av = a[sort.column]
    const bv = b[sort.column]
    if (av === bv) return 0
    return factor * (av < bv ? -1 : 1)
  })
  return sorted
}

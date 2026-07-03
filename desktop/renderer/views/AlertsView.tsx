/**
 * Alerts view (Plan 0060 phase 4): the watch list + fired-alert history.
 *
 * Two panels, matching the plan's "agent creates, viewer manages" grain
 * (ADR-0015): the watches panel lists the persisted definitions with the one
 * viewer-owned mutation (enable/disable via `POST /watches/{id}`); the
 * history panel lists fired alerts newest-first — the persisted page from
 * `GET /alerts`, with this session's live fires (routed through `alertBus`
 * from App's single `useEventStream`) prepended as they arrive. A live fire
 * that is already in the fetched page (the sidecar persists before it
 * publishes) is deduped on `(watch_id, fired_at)`.
 *
 * Alert rows render condition FACTS — the payload's `condition` string —
 * never advice (ADR-0029).
 */
import { useEffect, useState } from 'react'

import { api, ApiError } from '../api/client'
import { subscribeAlerts } from '../handlers/alertBus'
import { formatDateTime } from '../lib/format'
import type { AlertOut } from '../types/sidecar/alert-out'
import type { WatchOut } from '../types/sidecar/watch-out'
import type { AlertTriggeredPayloadV1 } from '../types/events'
import styles from './AlertsView.module.css'

const HISTORY_PAGE_LIMIT = 50

/** One normalized history row — fetched and live alerts render identically. */
interface AlertRow {
  /** `(watch_id, fired_at)` — dedup key between the live and fetched sources. */
  key: string
  firedAt: string
  watchId: number
  symbol: string | null
  timeframe: string | null
  kind: string | null
  condition: string | null
}

function rowKey(watchId: number, firedAt: string): string {
  return `${watchId}|${firedAt}`
}

/** Normalize a persisted alert. `payload` is `Record<string, unknown>` on the
 * generated type (the sidecar stores it as an opaque validated blob), so the
 * display fields are read defensively. */
export function rowFromAlertOut(alert: AlertOut): AlertRow {
  const payload = alert.payload
  const str = (key: string): string | null =>
    typeof payload[key] === 'string' ? (payload[key] as string) : null
  return {
    key: rowKey(alert.watch_id, alert.fired_at),
    firedAt: alert.fired_at,
    watchId: alert.watch_id,
    symbol: str('symbol'),
    timeframe: str('timeframe'),
    kind: str('kind'),
    condition: str('condition'),
  }
}

/** Normalize a live SSE fire (already Zod-validated at the stream boundary). */
export function rowFromLivePayload(payload: AlertTriggeredPayloadV1): AlertRow {
  return {
    key: rowKey(payload.watch_id, payload.fired_at),
    firedAt: payload.fired_at,
    watchId: payload.watch_id,
    symbol: payload.symbol,
    timeframe: payload.timeframe,
    kind: payload.kind,
    condition: payload.condition,
  }
}

type WatchesState =
  | { status: 'loading' }
  | { status: 'ready'; watches: WatchOut[] }
  | { status: 'error'; message: string }

type HistoryState =
  | { status: 'loading' }
  | { status: 'ready'; rows: AlertRow[]; total: number }
  | { status: 'error'; message: string }

export function AlertsView(): JSX.Element {
  const [watchesState, setWatchesState] = useState<WatchesState>({ status: 'loading' })
  const [historyState, setHistoryState] = useState<HistoryState>({ status: 'loading' })
  const [liveRows, setLiveRows] = useState<AlertRow[]>([])
  const [toggleError, setToggleError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getWatches()
      .then((watches) => {
        if (!cancelled) setWatchesState({ status: 'ready', watches })
      })
      .catch((err: unknown) => {
        if (!cancelled) setWatchesState({ status: 'error', message: describeError(err) })
      })
    api
      .getAlerts({ limit: HISTORY_PAGE_LIMIT })
      .then((page) => {
        if (!cancelled)
          setHistoryState({
            status: 'ready',
            rows: page.alerts.map(rowFromAlertOut),
            total: page.total,
          })
      })
      .catch((err: unknown) => {
        if (!cancelled) setHistoryState({ status: 'error', message: describeError(err) })
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Live fires prepend (newest first) while the view is mounted.
  useEffect(
    () =>
      subscribeAlerts((payload) => {
        setLiveRows((rows) => [rowFromLivePayload(payload), ...rows])
      }),
    [],
  )

  const onToggle = (watch: WatchOut, enabled: boolean): void => {
    setToggleError(null)
    api
      .setWatchEnabled(watch.id, enabled)
      .then((updated) => {
        setWatchesState((state) =>
          state.status === 'ready'
            ? {
                status: 'ready',
                watches: state.watches.map((w) => (w.id === updated.id ? updated : w)),
              }
            : state,
        )
      })
      .catch((err: unknown) => {
        setToggleError(describeError(err))
      })
  }

  return (
    <section className={styles.view} aria-label="Alerts">
      <div className={styles.columns}>
        <section className={styles.panel} aria-label="Watches">
          <h2 className={styles.panelTitle}>Watches</h2>
          <WatchesPanel state={watchesState} onToggle={onToggle} />
          {toggleError !== null && (
            <p className={styles.error} role="alert" data-testid="watch-toggle-error">
              {toggleError}
            </p>
          )}
        </section>
        <section className={styles.panel} aria-label="Alert history">
          <h2 className={styles.panelTitle}>Alert history</h2>
          <HistoryPanel state={historyState} liveRows={liveRows} />
        </section>
      </div>
      <p className={styles.disclaimer}>
        Alerts report conditions the agent was asked to watch — facts, not advice.
      </p>
    </section>
  )
}

interface WatchesPanelProps {
  state: WatchesState
  onToggle: (watch: WatchOut, enabled: boolean) => void
}

function WatchesPanel({ state, onToggle }: WatchesPanelProps): JSX.Element {
  if (state.status === 'loading') {
    return (
      <p className={styles.muted} role="status" data-testid="watches-loading">
        Loading watches…
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <p className={styles.error} role="alert" data-testid="watches-error">
        Failed to load watches: {state.message}
      </p>
    )
  }
  if (state.watches.length === 0) {
    return (
      <p className={styles.muted} data-testid="watches-empty">
        No watches yet — ask the agent to create one.
      </p>
    )
  }
  return (
    <ul className={styles.watchList} data-testid="watch-list">
      {state.watches.map((watch) => (
        <li key={watch.id} className={styles.watchRow} data-testid={`watch-row-${watch.id}`}>
          <label className={styles.watchToggle}>
            <input
              type="checkbox"
              checked={watch.enabled}
              onChange={(e) => onToggle(watch, e.target.checked)}
              aria-label={`Watch ${watch.id}: ${watch.symbol} ${watch.timeframe} ${watch.kind} — ${
                watch.enabled ? 'enabled' : 'disabled'
              }`}
            />
            <span className={styles.watchSymbol}>{watch.symbol}</span>
            <span className={styles.watchMeta}>
              {watch.timeframe} · {kindLabel(watch.kind)}
            </span>
          </label>
          <span className={watch.enabled ? styles.enabled : styles.disabled}>
            {watch.enabled ? 'enabled' : 'disabled'}
          </span>
        </li>
      ))}
    </ul>
  )
}

interface HistoryPanelProps {
  state: HistoryState
  liveRows: AlertRow[]
}

function HistoryPanel({ state, liveRows }: HistoryPanelProps): JSX.Element {
  if (state.status === 'loading') {
    return (
      <p className={styles.muted} role="status" data-testid="alerts-loading">
        Loading alert history…
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <p className={styles.error} role="alert" data-testid="alerts-error">
        Failed to load alert history: {state.message}
      </p>
    )
  }

  const fetchedKeys = new Set(state.rows.map((r) => r.key))
  const rows = [...liveRows.filter((r) => !fetchedKeys.has(r.key)), ...state.rows]

  if (rows.length === 0) {
    return (
      <p className={styles.muted} data-testid="alerts-empty">
        Nothing has fired yet.
      </p>
    )
  }
  return (
    <ol className={styles.alertList} data-testid="alert-list">
      {rows.map((row) => (
        <li key={row.key} className={styles.alertRow}>
          <span className={styles.alertWhen}>{formatDateTime(row.firedAt)} UTC</span>
          <span className={styles.alertWhat}>
            <span className={styles.alertSymbol}>
              {row.symbol ?? `watch ${row.watchId}`}
              {row.timeframe !== null ? ` ${row.timeframe}` : ''}
            </span>
            <span className={styles.alertCondition}>{row.condition ?? '(no condition text)'}</span>
          </span>
        </li>
      ))}
    </ol>
  )
}

const KIND_LABELS: Record<string, string> = {
  indicator_threshold: 'indicator threshold',
  pattern: 'pattern',
  strategy_signal: 'strategy signal',
}

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return String(err)
}

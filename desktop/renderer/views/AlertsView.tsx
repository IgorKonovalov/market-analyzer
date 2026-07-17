/**
 * Alerts view (Plan 0060 phase 4; management verbs widened by Plan 0110):
 * the watch list + fired-alert history.
 *
 * Two panels, matching the plan's "agent creates, viewer manages" grain
 * (ADR-0015): the watches panel lists the persisted definitions with the
 * viewer-owned mutations — enable/disable, note edit (partial
 * `POST /watches/{id}`), and delete-with-confirm (`DELETE /watches/{id}`,
 * cascading the watch's history rows). The history panel lists fired alerts
 * newest-first — the persisted page from `GET /alerts`, with this session's
 * live fires (routed through `alertBus` from App's single `useEventStream`)
 * prepended as they arrive. A live fire that is already in the fetched page
 * (the sidecar persists before it publishes) is deduped on
 * `(watch_id, fired_at)`.
 *
 * Alert rows render condition FACTS — the payload's `condition` string —
 * never advice (ADR-0029). The watch `note` is user/agent CONTEXT: rendered
 * visually distinct from condition text, echoed onto history rows by a
 * render-time `watch_id` join (never baked into the persisted payload).
 */
import { useEffect, useState } from 'react'

import { api, ApiError } from '../api/client'
import { subscribeAlerts } from '../handlers/alertBus'
import { subscribeDefiPositionAlerts } from '../handlers/defiPositionAlertBus'
import { shortAddress } from '../lib/defiPositionAlert'
import { formatDateTime } from '../lib/format'
import { t } from '../lib/i18n'
import { formatWatchCondition } from '../lib/watchCondition'
import type { AlertOut } from '../types/sidecar/alert-out'
import type { PositionAlertOut } from '../types/sidecar/position-alert-out'
import type { WatchOut } from '../types/sidecar/watch-out'
import type { AlertTriggeredPayloadV1, DefiPositionAlertPayloadV1 } from '../types/events'
import styles from './AlertsView.module.css'

/** Mirror of the sidecar's note cap — the input refuses what the API would 422. */
const NOTE_MAX_LENGTH = 500

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

/** One normalized DeFi out-of-range row (Plan 0099 phase 4) — fetched history
 * and live SSE fires render identically. Condition facts only (ADR-0029). */
interface DefiAlertRow {
  /** `(watch_id, fired_at)` — dedup key between the live and fetched sources. */
  key: string
  firedAt: string
  chain: string
  poolAddress: string
  hoursOut: number
  tickLower: number
  tickUpper: number
  currentTick: number
}

export function rowFromPositionAlertOut(alert: PositionAlertOut): DefiAlertRow {
  return {
    key: rowKey(alert.watch_id, alert.fired_at),
    firedAt: alert.fired_at,
    chain: alert.chain,
    poolAddress: alert.pool_address,
    hoursOut: alert.hours_out,
    tickLower: alert.tick_lower,
    tickUpper: alert.tick_upper,
    currentTick: alert.current_tick,
  }
}

export function rowFromLiveDefiPayload(payload: DefiPositionAlertPayloadV1): DefiAlertRow {
  return {
    key: rowKey(payload.watch_id, payload.fired_at),
    firedAt: payload.fired_at,
    chain: payload.chain,
    poolAddress: payload.pool_address,
    hoursOut: payload.hours_out,
    tickLower: payload.tick_lower,
    tickUpper: payload.tick_upper,
    currentTick: payload.current_tick,
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

type DefiHistoryState =
  | { status: 'loading' }
  | { status: 'ready'; rows: DefiAlertRow[]; total: number }
  | { status: 'error'; message: string }

/** Inline note-editor state: which watch is being edited, and the draft text. */
interface NoteEdit {
  watchId: number
  draft: string
}

export function AlertsView(): JSX.Element {
  const [watchesState, setWatchesState] = useState<WatchesState>({ status: 'loading' })
  const [historyState, setHistoryState] = useState<HistoryState>({ status: 'loading' })
  const [defiHistoryState, setDefiHistoryState] = useState<DefiHistoryState>({ status: 'loading' })
  const [liveRows, setLiveRows] = useState<AlertRow[]>([])
  const [liveDefiRows, setLiveDefiRows] = useState<DefiAlertRow[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [noteEdit, setNoteEdit] = useState<NoteEdit | null>(null)

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
    // The DeFi out-of-range history (Plan 0099 phase 4) — SSE only carries
    // this session's live fires; the persisted page fills in the rest.
    api
      .getPositionAlerts({ limit: HISTORY_PAGE_LIMIT })
      .then((page) => {
        if (!cancelled)
          setDefiHistoryState({
            status: 'ready',
            rows: page.alerts.map(rowFromPositionAlertOut),
            total: page.total,
          })
      })
      .catch((err: unknown) => {
        if (!cancelled) setDefiHistoryState({ status: 'error', message: describeError(err) })
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
  useEffect(
    () =>
      subscribeDefiPositionAlerts((payload) => {
        setLiveDefiRows((rows) => [rowFromLiveDefiPayload(payload), ...rows])
      }),
    [],
  )

  const replaceWatch = (updated: WatchOut): void => {
    setWatchesState((state) =>
      state.status === 'ready'
        ? {
            status: 'ready',
            watches: state.watches.map((w) => (w.id === updated.id ? updated : w)),
          }
        : state,
    )
  }

  const onToggle = (watch: WatchOut, enabled: boolean): void => {
    setActionError(null)
    api
      .setWatchEnabled(watch.id, enabled)
      .then(replaceWatch)
      .catch((err: unknown) => {
        setActionError(describeError(err))
      })
  }

  const onSaveNote = (watch: WatchOut, draft: string): void => {
    setActionError(null)
    const trimmed = draft.trim()
    api
      .setWatchNote(watch.id, trimmed === '' ? null : trimmed)
      .then((updated) => {
        replaceWatch(updated)
        setNoteEdit(null)
      })
      .catch((err: unknown) => {
        setActionError(describeError(err))
      })
  }

  const onDelete = (watch: WatchOut): void => {
    const confirmed = window.confirm(
      t('alerts.deleteConfirm', { id: watch.id, symbol: watch.symbol, timeframe: watch.timeframe }),
    )
    if (!confirmed) return
    setActionError(null)
    api
      .deleteWatch(watch.id)
      .then(() => {
        setWatchesState((state) =>
          state.status === 'ready'
            ? { status: 'ready', watches: state.watches.filter((w) => w.id !== watch.id) }
            : state,
        )
        // The sidecar cascade removed the watch's history rows; drop them from
        // local state too so the panel matches what a refetch would return.
        setHistoryState((state) => {
          if (state.status !== 'ready') return state
          const rows = state.rows.filter((r) => r.watchId !== watch.id)
          return { status: 'ready', rows, total: state.total - (state.rows.length - rows.length) }
        })
        setLiveRows((rows) => rows.filter((r) => r.watchId !== watch.id))
        setNoteEdit((edit) => (edit?.watchId === watch.id ? null : edit))
      })
      .catch((err: unknown) => {
        setActionError(describeError(err))
      })
  }

  const watches = watchesState.status === 'ready' ? watchesState.watches : []

  return (
    <section className={styles.view} aria-label={t('alerts.alerts')}>
      <div className={styles.columns}>
        <section className={styles.panel} aria-label={t('alerts.watches')}>
          <h2 className={styles.panelTitle}>{t('alerts.watches')}</h2>
          <WatchesPanel
            state={watchesState}
            noteEdit={noteEdit}
            onToggle={onToggle}
            onDelete={onDelete}
            onNoteEditChange={setNoteEdit}
            onSaveNote={onSaveNote}
          />
          {actionError !== null && (
            <p className={styles.error} role="alert" data-testid="watch-action-error">
              {actionError}
            </p>
          )}
        </section>
        <section className={styles.panel} aria-label={t('alerts.alertHistory')}>
          <h2 className={styles.panelTitle}>{t('alerts.alertHistory')}</h2>
          <HistoryPanel state={historyState} liveRows={liveRows} watches={watches} />
        </section>
      </div>
      <section className={styles.panel} aria-label={t('alerts.defi.title')}>
        <h2 className={styles.panelTitle}>{t('alerts.defi.title')}</h2>
        <DefiHistoryPanel state={defiHistoryState} liveRows={liveDefiRows} />
      </section>
      <p className={styles.disclaimer}>{t('alerts.disclaimer')}</p>
    </section>
  )
}

interface DefiHistoryPanelProps {
  state: DefiHistoryState
  liveRows: DefiAlertRow[]
}

/** The DeFi out-of-range panel (Plan 0099 phase 4): persisted history merged
 * with this session's live fires, deduped on `(watch_id, fired_at)` like the
 * market-alert panel. Rows are condition FACTS — pool, tick range vs current
 * tick, hours idle — never rebalance advice (ADR-0029/0093). */
function DefiHistoryPanel({ state, liveRows }: DefiHistoryPanelProps): JSX.Element {
  if (state.status === 'loading') {
    return (
      <p className={styles.muted} role="status" data-testid="defi-alerts-loading">
        {t('alerts.defi.loading')}
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <p className={styles.error} role="alert" data-testid="defi-alerts-error">
        {t('alerts.defi.historyError')} {state.message}
      </p>
    )
  }

  const fetchedKeys = new Set(state.rows.map((r) => r.key))
  const rows = [...liveRows.filter((r) => !fetchedKeys.has(r.key)), ...state.rows]

  if (rows.length === 0) {
    return (
      <p className={styles.muted} data-testid="defi-alerts-empty">
        {t('alerts.defi.nothingFired')}
      </p>
    )
  }
  return (
    <ol className={styles.alertList} data-testid="defi-alert-list">
      {rows.map((row) => (
        <li key={row.key} className={styles.alertRow}>
          <span className={styles.alertWhen}>{formatDateTime(row.firedAt)} UTC</span>
          <span className={styles.alertWhat}>
            <span className={styles.alertSymbol}>
              {row.chain} · {shortAddress(row.poolAddress)}
            </span>
            <span className={styles.alertCondition}>
              {t('alerts.defi.rowCondition', {
                hours: row.hoursOut.toFixed(1),
                tick: row.currentTick,
                lower: row.tickLower,
                upper: row.tickUpper,
              })}
            </span>
          </span>
        </li>
      ))}
    </ol>
  )
}

interface WatchesPanelProps {
  state: WatchesState
  noteEdit: NoteEdit | null
  onToggle: (watch: WatchOut, enabled: boolean) => void
  onDelete: (watch: WatchOut) => void
  onNoteEditChange: (edit: NoteEdit | null) => void
  onSaveNote: (watch: WatchOut, draft: string) => void
}

function WatchesPanel({
  state,
  noteEdit,
  onToggle,
  onDelete,
  onNoteEditChange,
  onSaveNote,
}: WatchesPanelProps): JSX.Element {
  if (state.status === 'loading') {
    return (
      <p className={styles.muted} role="status" data-testid="watches-loading">
        {t('alerts.loadingWatches')}
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <p className={styles.error} role="alert" data-testid="watches-error">
        {t('alerts.watchesError')} {state.message}
      </p>
    )
  }
  if (state.watches.length === 0) {
    return (
      <p className={styles.muted} data-testid="watches-empty">
        {t('alerts.noWatches')}
      </p>
    )
  }
  return (
    <ul className={styles.watchList} data-testid="watch-list">
      {state.watches.map((watch) => (
        <li key={watch.id} className={styles.watchRow} data-testid={`watch-row-${watch.id}`}>
          <div className={styles.watchHeader}>
            <label className={styles.watchToggle}>
              <input
                type="checkbox"
                checked={watch.enabled}
                onChange={(e) => onToggle(watch, e.target.checked)}
                aria-label={t('alerts.watchRowLabel', {
                  id: watch.id,
                  symbol: watch.symbol,
                  timeframe: watch.timeframe,
                  kind: watch.kind,
                  state: watch.enabled ? t('alerts.enabled') : t('alerts.disabled'),
                })}
              />
              <span className={styles.watchSymbol}>{watch.symbol}</span>
              <span className={styles.watchMeta}>
                {watch.timeframe} · {kindLabel(watch.kind)}
              </span>
            </label>
            <span className={watch.enabled ? styles.enabled : styles.disabled}>
              {watch.enabled ? t('alerts.enabled') : t('alerts.disabled')}
            </span>
            <button
              type="button"
              className={styles.iconButton}
              onClick={() => onDelete(watch)}
              aria-label={t('alerts.deleteWatch', { id: watch.id })}
              data-testid={`watch-delete-${watch.id}`}
            >
              ✕
            </button>
          </div>
          <span className={styles.watchCondition} data-testid={`watch-condition-${watch.id}`}>
            {formatWatchCondition(watch.kind, watch.params)}
          </span>
          <span className={styles.watchCreated} data-testid={`watch-created-${watch.id}`}>
            {t('alerts.createdAt', { when: formatDateTime(watch.created_at) })}
          </span>
          <WatchNote
            watch={watch}
            edit={noteEdit?.watchId === watch.id ? noteEdit : null}
            onEditChange={onNoteEditChange}
            onSave={onSaveNote}
          />
        </li>
      ))}
    </ul>
  )
}

interface WatchNoteProps {
  watch: WatchOut
  edit: NoteEdit | null
  onEditChange: (edit: NoteEdit | null) => void
  onSave: (watch: WatchOut, draft: string) => void
}

/** The note line of one watch row: display (muted, distinct from the condition
 * fact per ADR-0029) with a pencil toggle, or the inline editor. */
function WatchNote({ watch, edit, onEditChange, onSave }: WatchNoteProps): JSX.Element {
  if (edit === null) {
    return (
      <span className={styles.watchNote}>
        {watch.note !== null && (
          <span className={styles.noteText} data-testid={`watch-note-${watch.id}`}>
            {watch.note}
          </span>
        )}
        <button
          type="button"
          className={styles.iconButton}
          onClick={() => onEditChange({ watchId: watch.id, draft: watch.note ?? '' })}
          aria-label={t('alerts.editNote', { id: watch.id })}
          data-testid={`watch-note-edit-${watch.id}`}
        >
          ✎
        </button>
      </span>
    )
  }
  return (
    <span className={styles.watchNote}>
      <input
        type="text"
        className={styles.noteInput}
        value={edit.draft}
        maxLength={NOTE_MAX_LENGTH}
        onChange={(e) => onEditChange({ watchId: watch.id, draft: e.target.value })}
        aria-label={t('alerts.noteInputLabel', { id: watch.id })}
        data-testid={`watch-note-input-${watch.id}`}
      />
      <button
        type="button"
        className={styles.noteButton}
        onClick={() => onSave(watch, edit.draft)}
        data-testid={`watch-note-save-${watch.id}`}
      >
        {t('alerts.saveNote')}
      </button>
      <button type="button" className={styles.noteButton} onClick={() => onEditChange(null)}>
        {t('alerts.cancelNote')}
      </button>
    </span>
  )
}

interface HistoryPanelProps {
  state: HistoryState
  liveRows: AlertRow[]
  /** The fetched watch list — history rows echo each watch's note by
   * `watch_id` join at render time (a deleted watch simply has no note;
   * the payload itself never carries it). */
  watches: WatchOut[]
}

function HistoryPanel({ state, liveRows, watches }: HistoryPanelProps): JSX.Element {
  if (state.status === 'loading') {
    return (
      <p className={styles.muted} role="status" data-testid="alerts-loading">
        {t('alerts.loadingHistory')}
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <p className={styles.error} role="alert" data-testid="alerts-error">
        {t('alerts.historyError')} {state.message}
      </p>
    )
  }

  const fetchedKeys = new Set(state.rows.map((r) => r.key))
  const rows = [...liveRows.filter((r) => !fetchedKeys.has(r.key)), ...state.rows]

  if (rows.length === 0) {
    return (
      <p className={styles.muted} data-testid="alerts-empty">
        {t('alerts.nothingFired')}
      </p>
    )
  }
  const noteByWatchId = new Map<number, string>()
  for (const w of watches) {
    if (w.note !== null) noteByWatchId.set(w.id, w.note)
  }
  return (
    <ol className={styles.alertList} data-testid="alert-list">
      {rows.map((row) => {
        const note = noteByWatchId.get(row.watchId)
        return (
          <li key={row.key} className={styles.alertRow}>
            <span className={styles.alertWhen}>{formatDateTime(row.firedAt)} UTC</span>
            <span className={styles.alertWhat}>
              <span className={styles.alertSymbol}>
                {row.symbol ?? t('alerts.watchFallback', { id: row.watchId })}
                {row.timeframe !== null ? ` ${row.timeframe}` : ''}
              </span>
              <span className={styles.alertCondition}>
                {row.condition ?? t('alerts.noConditionText')}
              </span>
              {note !== undefined && <span className={styles.noteText}>{note}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

const KIND_LABELS: Record<string, string> = {
  indicator_threshold: 'alerts.kind.indicatorThreshold',
  pattern: 'alerts.kind.pattern',
  strategy_signal: 'alerts.kind.strategySignal',
}

function kindLabel(kind: string): string {
  const key = KIND_LABELS[kind]
  return key !== undefined ? t(key) : kind
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return String(err)
}

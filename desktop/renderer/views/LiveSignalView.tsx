/**
 * Live-signal panel (Plan 0026 phase 3).
 *
 * A reactive, read-only surface: it renders the latest `SignalEvaluation` the
 * agent produced via the `signal.evaluated v1` SSE event (routed through
 * `App`'s single `useEventStream`). There is no form and no controls — per
 * ADR-0015 the agent drives evaluation; this panel only reflects it.
 *
 * It is strictly a CONDITION REPORT — it states what the strategy's signals
 * *are* (implied position, the most recent signal, freshness), never what to do
 * about them. No recommendation language anywhere.
 *
 * The full evaluation rides inline in the event payload, so the panel needs no
 * sidecar fetch (unlike the backtest view, which fetches a persisted result).
 */
import { formatDateTime } from '../lib/format'
import type { EvaluatedSignal, SignalEvaluation } from '../types/events'
import styles from './LiveSignalView.module.css'

interface Props {
  /** The latest evaluation, or `null` before any `signal.evaluated` event. */
  evaluation: SignalEvaluation | null
}

const KIND_LABEL: Record<EvaluatedSignal['kind'], string> = {
  enter_long: 'enter long',
  exit_long: 'exit long',
  enter_short: 'enter short',
  exit_short: 'exit short',
}

export function LiveSignalView({ evaluation }: Props): JSX.Element {
  if (evaluation === null) {
    return (
      <section className={styles.view} aria-label="Live signal evaluation">
        <p className={styles.empty} data-testid="live-signal-empty">
          No evaluation yet — ask the agent to evaluate a strategy.
        </p>
      </section>
    )
  }

  const lastSignal = evaluation.last_signal ?? null

  return (
    <section className={styles.view} aria-label="Live signal evaluation">
      <header className={styles.header}>
        <h2 className={styles.title} data-testid="live-signal-title">
          <span className={styles.strategy}>{evaluation.strategy_id}</span>
          <span className={styles.dot} aria-hidden="true">
            ·
          </span>
          <span className={styles.symbol}>{evaluation.symbol}</span>
          <span className={styles.dot} aria-hidden="true">
            ·
          </span>
          <span className={styles.timeframe}>{evaluation.timeframe}</span>
        </h2>
      </header>

      <dl className={styles.grid}>
        <div className={styles.row}>
          <dt>Current position</dt>
          <dd
            data-testid="live-signal-position"
            data-position={evaluation.current_position}
            className={styles[`pos_${evaluation.current_position}`]}
          >
            {evaluation.current_position}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>Last signal</dt>
          <dd data-testid="live-signal-last">
            {lastSignal === null ? (
              <span className={styles.muted}>none yet</span>
            ) : (
              <LastSignalLine
                signal={lastSignal}
                barsSince={evaluation.bars_since_last_signal ?? null}
              />
            )}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>Freshness</dt>
          <dd>
            {evaluation.fresh_signal ? (
              <span className={styles.fresh} data-fresh="true" data-testid="live-signal-freshness">
                fresh — fired on the last closed bar
              </span>
            ) : (
              <span className={styles.stale} data-fresh="false" data-testid="live-signal-freshness">
                no fresh signal on the last closed bar
              </span>
            )}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>Evaluated through</dt>
          <dd data-testid="live-signal-through">
            {formatDateTime(evaluation.evaluated_through_ts)} UTC
            <span className={styles.muted}> · {evaluation.closed_bar_count} closed bars</span>
          </dd>
        </div>
      </dl>

      {evaluation.latest_bar_excluded_as_forming && (
        <p className={styles.forming} data-testid="live-signal-forming" role="note">
          The latest bar is still forming and was excluded — this reads through the last closed bar.
        </p>
      )}

      <p className={styles.disclaimer}>
        A condition report of the strategy&apos;s current signal state — not advice.
      </p>
    </section>
  )
}

interface LastSignalLineProps {
  signal: EvaluatedSignal
  barsSince: number | null
}

/** Renders the most-recent signal as "<kind> at <ts> (<n> bars ago) — <reason>". */
function LastSignalLine({ signal, barsSince }: LastSignalLineProps): JSX.Element {
  const ago =
    barsSince === null
      ? ''
      : barsSince === 0
        ? ' (last closed bar)'
        : ` (${barsSince} bar${barsSince === 1 ? '' : 's'} ago)`

  return (
    <span>
      <span className={styles[`kind_${signal.kind}`]} data-testid="live-signal-kind">
        {KIND_LABEL[signal.kind]}
      </span>{' '}
      at {formatDateTime(signal.event_ts)} UTC{ago}
      {signal.reason ? <span className={styles.reason}> — {signal.reason}</span> : null}
    </span>
  )
}

/**
 * Track-record view (Plan 0080 phase 5, ADR-0075).
 *
 * A read-only, reactive surface over the advisor's OWN live track record: how
 * its past recommendations turned out against realized price. It fetches the
 * authoritative aggregate from `GET /track_record` on mount and refetches when
 * a `recommendation.scored` event lands (the `refreshKey` bump from `App`); the
 * event is the live nudge, the fetch is the data — the renderer never
 * re-aggregates (the honest small-n / baseline logic lives once, in the sidecar).
 *
 * The defining constraint (ADR-0075 honesty): the number that matters is the
 * edge over a trivial baseline (`hit_rate_vs_baseline`), shown prominently; a
 * below-`MIN` sample renders "not enough calls to conclude" and NO percentage —
 * a handful of calls is never dressed as a conclusion. And, as with the
 * Recommendations panel (ADR-0029), this is a FACT surface: it reports the
 * record and offers NO path to act — zero interactive action controls, enforced
 * as a spec.
 */
import { useEffect, useState } from 'react'

import { api } from '../api/client'
import { formatPct, formatRatio } from '../lib/format'
import { t } from '../lib/i18n'
import type { GetTrackRecordResponse } from '../types/sidecar/get-track-record-response'
import type { ScoredCallOut } from '../types/sidecar/scored-call-out'
import type { TrackRecord } from '../types/sidecar/track-record'
import styles from './TrackRecordView.module.css'

interface Props {
  /** Bump to refetch — `App` increments it on each `recommendation.scored`
   * event. Omitted → fetched once on mount. */
  refreshKey?: number
}

interface FetchState {
  status: 'loading' | 'ready' | 'error'
  data: GetTrackRecordResponse | null
  error: string | null
}

const INITIAL: FetchState = { status: 'loading', data: null, error: null }

/** Unsigned percent — a hit-rate / frequency is a magnitude, not a delta, so a
 * forced "+" would misread. */
const PCT = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

/** Brier is a bounded 0–1 score; four places so a good one does not collapse. */
const SCORE = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
})

function pct(value: number | null): string {
  return value != null ? PCT.format(value) : '—'
}

const DIRECTION_LABEL_KEY: Record<ScoredCallOut['direction'], string> = {
  long: 'trackRecord.directionLong',
  short: 'trackRecord.directionShort',
}

const OUTCOME_LABEL_KEY: Record<string, string> = {
  target_hit: 'trackRecord.outcomeTargetHit',
  stopped: 'trackRecord.outcomeStopped',
  timeout: 'trackRecord.outcomeTimeout',
}

export function TrackRecordView({ refreshKey }: Props): JSX.Element {
  const [state, setState] = useState<FetchState>(INITIAL)

  useEffect(() => {
    let cancelled = false
    setState((s) => ({ ...s, status: 'loading', error: null }))
    api
      .getTrackRecord()
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data, error: null })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : t('trackRecord.loadError')
        setState({ status: 'error', data: null, error: message })
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  return (
    <section className={styles.view} aria-label={t('trackRecord.title')}>
      <header className={styles.header}>
        <h2 className={styles.title}>{t('trackRecord.title')}</h2>
        <p className={styles.lede}>{t('trackRecord.lede')}</p>
        <p className={styles.disclaimer} role="note">
          {t('trackRecord.disclaimer')}
        </p>
      </header>

      {state.status === 'loading' && (
        <p className={styles.status} role="status" data-testid="track-record-loading">
          {t('trackRecord.loading')}
        </p>
      )}
      {state.status === 'error' && (
        <p className={styles.error} role="alert" data-testid="track-record-error">
          {state.error ?? t('trackRecord.loadError')}
        </p>
      )}
      {state.status === 'ready' && state.data !== null && <TrackRecordBody data={state.data} />}
    </section>
  )
}

function TrackRecordBody({ data }: { data: GetTrackRecordResponse }): JSX.Element {
  const record = data.track_record
  if (record.n === 0) {
    return (
      <p className={styles.status} role="status" data-testid="track-record-empty">
        {t('trackRecord.empty')}
      </p>
    )
  }

  return (
    <>
      <p className={styles.sample} data-testid="track-record-sample">
        {t('trackRecord.sampleSize', { n: record.n })}
      </p>

      {record.sufficient ? (
        <Aggregate record={record} />
      ) : (
        <p className={styles.insufficient} role="note" data-testid="track-record-insufficient">
          {t('trackRecord.insufficient', { n: record.n, min: record.min_n })}
        </p>
      )}

      <RecentCalls calls={data.recent} />
    </>
  )
}

/** The advisor's conclusion — only rendered on a sufficient sample, so a
 * below-`MIN` record can never show a bare percentage. */
function Aggregate({ record }: { record: TrackRecord }): JSX.Element {
  return (
    <>
      <section className={styles.deltaBlock} aria-label={t('trackRecord.baselineDeltaTitle')}>
        <span className={styles.deltaValue} data-testid="track-record-baseline-delta">
          {record.hit_rate_vs_baseline != null ? formatPct(record.hit_rate_vs_baseline) : '—'}
        </span>
        <span className={styles.deltaLabel}>{t('trackRecord.baselineDeltaLabel')}</span>
      </section>

      <dl className={styles.grid}>
        <div className={styles.row}>
          <dt>{t('trackRecord.hitRate')}</dt>
          <dd data-testid="track-record-hit-rate">{pct(record.hit_rate)}</dd>
        </div>
        <div className={styles.row}>
          <dt>{t('trackRecord.baselineHitRate')}</dt>
          <dd data-testid="track-record-baseline-hit-rate">{pct(record.baseline_hit_rate)}</dd>
        </div>
        <div className={styles.row}>
          <dt>{t('trackRecord.meanR')}</dt>
          <dd data-testid="track-record-mean-r">
            {record.mean_r != null ? formatRatio(record.mean_r) : '—'}
          </dd>
        </div>
      </dl>

      <Calibration record={record} />
    </>
  )
}

function Calibration({ record }: { record: TrackRecord }): JSX.Element {
  return (
    <section
      className={styles.calibration}
      aria-label={t('trackRecord.calibrationTitle')}
      data-testid="track-record-calibration"
    >
      <h3 className={styles.sectionTitle}>{t('trackRecord.calibrationTitle')}</h3>
      <dl className={styles.grid}>
        <div className={styles.row}>
          <dt>{t('trackRecord.brier')}</dt>
          <dd data-testid="track-record-brier">
            {record.brier != null ? SCORE.format(record.brier) : '—'}
          </dd>
        </div>
        <div className={styles.row}>
          <dt>{t('trackRecord.meanPredicted')}</dt>
          <dd>{pct(record.mean_forecast_prob)}</dd>
        </div>
        <div className={styles.row}>
          <dt>{t('trackRecord.observedFreq')}</dt>
          <dd>{pct(record.observed_hit_rate)}</dd>
        </div>
      </dl>
      {record.reliability.length > 0 && (
        <table className={styles.table} data-testid="track-record-reliability">
          <thead>
            <tr>
              <th scope="col">{t('trackRecord.reliabilityBand')}</th>
              <th scope="col">{t('trackRecord.meanPredicted')}</th>
              <th scope="col">{t('trackRecord.observedFreq')}</th>
              <th scope="col">{t('trackRecord.colCount')}</th>
            </tr>
          </thead>
          <tbody>
            {record.reliability.map((bucket) => (
              <tr key={`${bucket.lower}-${bucket.upper}`}>
                <td>
                  {PCT.format(bucket.lower)}–{PCT.format(bucket.upper)}
                </td>
                <td>{PCT.format(bucket.mean_predicted)}</td>
                <td>{PCT.format(bucket.observed_freq)}</td>
                <td>{bucket.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function RecentCalls({ calls }: { calls: ScoredCallOut[] }): JSX.Element {
  if (calls.length === 0) {
    return (
      <section className={styles.recent} aria-label={t('trackRecord.recentTitle')}>
        <h3 className={styles.sectionTitle}>{t('trackRecord.recentTitle')}</h3>
        <p className={styles.status}>{t('trackRecord.recentEmpty')}</p>
      </section>
    )
  }
  return (
    <section className={styles.recent} aria-label={t('trackRecord.recentTitle')}>
      <h3 className={styles.sectionTitle}>{t('trackRecord.recentTitle')}</h3>
      <table className={styles.table} data-testid="track-record-recent">
        <thead>
          <tr>
            <th scope="col">{t('trackRecord.colSymbol')}</th>
            <th scope="col">{t('trackRecord.colDirection')}</th>
            <th scope="col">{t('trackRecord.colOutcome')}</th>
            <th scope="col">{t('trackRecord.colRealizedR')}</th>
            <th scope="col">{t('trackRecord.colAsOf')}</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <tr
              key={`${call.symbol}:${call.as_of_bar_ts}:${call.horizon_bars}`}
              data-testid="track-record-recent-row"
              data-outcome={call.outcome_class}
            >
              <td>{call.symbol}</td>
              <td>{t(DIRECTION_LABEL_KEY[call.direction])}</td>
              <td data-testid="recent-outcome">
                {OUTCOME_LABEL_KEY[call.outcome_class] != null
                  ? t(OUTCOME_LABEL_KEY[call.outcome_class])
                  : call.outcome_class}
              </td>
              <td>{call.realized_r != null ? formatRatio(call.realized_r) : '—'}</td>
              <td>{call.as_of_bar_ts.slice(0, 10)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

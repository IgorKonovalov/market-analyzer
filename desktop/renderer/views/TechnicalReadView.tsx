/**
 * Technical Read view (Plan 0074 phase 3, ADR-0068).
 *
 * A reactive, read-only surface for the LESSER advisory tier: it renders the
 * latest `TechnicalRead` the agent produced via the `technical_read.completed v1`
 * SSE event (routed through `App`'s single `useEventStream`, Zod-validated in the
 * dispatcher).
 *
 * The defining constraint (ADR-0068): a single-indicator read must never be
 * mistaken for the fully-corroborated `recommend` call. So the "single indicator —
 * not corroborated" banner is the panel's prominent first child, and the model
 * itself carries NO conviction and NO entry/stop/target — there is structurally
 * nothing here to act on. Like the Recommendations view (ADR-0025/0029) this
 * surface offers NO interactive control of any kind; a renderer spec enforces that
 * as a test, not a guideline.
 */
import { formatDateTime } from '../lib/format'
import { t } from '../lib/i18n'
import type { TechnicalRead } from '../types/events'
import styles from './TechnicalReadView.module.css'

interface Props {
  /** The latest technical read, or `null` before any `technical_read.completed` event. */
  read: TechnicalRead | null
}

/** Catalog keys for the read's closed-vocabulary direction (localized; the
 * composed `regime_state`/`rationale` strings stay in the sidecar's English). */
const DIRECTION_LABEL_KEY: Record<TechnicalRead['direction'], string> = {
  long: 'technicalRead.directionLong',
  short: 'technicalRead.directionShort',
  flat: 'technicalRead.directionFlat',
}

/** Catalog keys for the read's closed-vocabulary indicator id. */
const INDICATOR_LABEL_KEY: Record<TechnicalRead['indicator_id'], string> = {
  supertrend: 'technicalRead.indicatorSupertrend',
  ema_stack: 'technicalRead.indicatorEmaStack',
  macd: 'technicalRead.indicatorMacd',
  ichimoku: 'technicalRead.indicatorIchimoku',
}

export function TechnicalReadView({ read }: Props): JSX.Element {
  if (read === null) {
    return (
      <section className={styles.view} aria-label={t('technicalRead.viewLabel')}>
        <p className={styles.empty} data-testid="technical-read-empty">
          {t('technicalRead.empty')}
        </p>
      </section>
    )
  }

  return (
    <section className={styles.view} aria-label={t('technicalRead.viewLabel')}>
      {/* The not-corroborated banner is the prominent first child (ADR-0068). */}
      <p className={styles.banner} data-testid="not-corroborated-banner" role="note">
        <strong>{t('technicalRead.notCorroboratedTitle')}</strong>{' '}
        {t('technicalRead.notCorroboratedBody')}
      </p>

      <header className={styles.header}>
        <h2 className={styles.title} data-testid="technical-read-title">
          <span className={styles.symbol}>{read.symbol}</span>
          <span className={styles.dot} aria-hidden="true">
            ·
          </span>
          <span className={styles.timeframe}>{read.timeframe}</span>
        </h2>
        <p className={styles.asOf} data-testid="technical-read-as-of">
          {t('technicalRead.asOf')} {formatDateTime(read.as_of_bar_ts)} UTC{' '}
          {t('technicalRead.lastClosedBar')}
        </p>
      </header>

      <dl className={styles.grid}>
        <div className={styles.row}>
          <dt>{t('technicalRead.indicator')}</dt>
          <dd data-testid="technical-read-indicator" data-indicator={read.indicator_id}>
            {t(INDICATOR_LABEL_KEY[read.indicator_id])}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>{t('technicalRead.direction')}</dt>
          <dd
            data-testid="technical-read-direction"
            data-direction={read.direction}
            className={styles[`dir_${read.direction}`]}
          >
            {t(DIRECTION_LABEL_KEY[read.direction])}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>{t('technicalRead.regimeState')}</dt>
          <dd data-testid="technical-read-regime">{read.regime_state}</dd>
        </div>
      </dl>

      <section className={styles.rationale} aria-label={t('technicalRead.why')}>
        <h3 className={styles.sectionTitle}>{t('technicalRead.why')}</h3>
        <ul className={styles.rationaleList} data-testid="technical-read-rationale">
          {read.rationale.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </section>

      <p className={styles.disclaimer}>{t('technicalRead.disclaimer')}</p>
    </section>
  )
}

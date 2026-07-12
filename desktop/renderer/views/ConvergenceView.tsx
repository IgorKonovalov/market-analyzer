/**
 * Convergence view (Plan 0078 phase 3, ADR-0041/0029).
 *
 * A reactive, read-only surface for the prediction-market convergence screener:
 * it renders the latest ranked opportunities the agent produced via the
 * `prediction.screen_completed v1` SSE event (routed through `App`'s single
 * `useEventStream`, Zod-validated in the dispatcher).
 *
 * The defining constraint (ADR-0029/0041): opportunities are FACTS with their
 * risks attached, never a buy call. So there is NO action control of any kind
 * (a renderer spec enforces that as a test), and every opportunity renders its
 * risk context — the labeled `resolution_risk` heuristic, the liquidity caution,
 * the capital-lockup note — beside the gross return, never as a clean number.
 * `implied_return_if_right` is gross of the resolution tail, never expected value.
 */
import { formatDateTime, formatDuration, formatPct, formatUsd } from '../lib/format'
import { t } from '../lib/i18n'
import type {
  ConvergenceOpportunity,
  PredictionScreenCompletedPayloadV1,
  ResolutionRiskLevel,
} from '../types/events'
import styles from './ConvergenceView.module.css'

interface Props {
  /** The latest screen, or `null` before any `prediction.screen_completed` event. */
  screen: PredictionScreenCompletedPayloadV1 | null
}

/** Catalog keys for the closed-vocabulary resolution-risk level (localized; the
 * composed `reasons`/`caution`/`note` strings stay in the sidecar's English per
 * ADR-0063). */
const RISK_LEVEL_KEY: Record<ResolutionRiskLevel, string> = {
  low: 'convergence.riskLevelLow',
  medium: 'convergence.riskLevelMedium',
  high: 'convergence.riskLevelHigh',
}

/** A probability in [0, 1] as a plain percent (no sign — it is not a change). */
function formatProbability(p: number): string {
  return `${(p * 100).toFixed(1)}%`
}

/**
 * The market URL is provenance (a citation of where the public fact lives), not a
 * trade control (ADR-0029/0041). Render it as a link only when it host-validates to
 * `polymarket.com` over https — a renderer-side allowlist and defense in depth: the
 * sidecar already host-validates when it builds the URL (Plan 0089 phase 1) and the
 * IPC boundary rejects non-`http(s)`, but the renderer never even offers an
 * off-allowlist link and never navigates itself (ADR-0008). Anything else → `null`.
 */
function safePolymarketUrl(url: string | null | undefined): string | null {
  if (url === null || url === undefined || url === '') return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && parsed.host === 'polymarket.com' ? parsed.href : null
  } catch {
    return null
  }
}

export function ConvergenceView({ screen }: Props): JSX.Element {
  if (screen === null) {
    return (
      <section className={styles.view} aria-label={t('convergence.viewLabel')}>
        <p className={styles.empty} data-testid="convergence-empty">
          {t('convergence.empty')}
        </p>
      </section>
    )
  }

  return (
    <section className={styles.view} aria-label={t('convergence.viewLabel')}>
      {/* Facts-not-a-call disclaimer, the prominent first child (ADR-0029/0041). */}
      <p className={styles.disclaimer} role="note" data-testid="convergence-disclaimer">
        <strong>{t('convergence.disclaimerTitle')}</strong> {t('convergence.disclaimerBody')}
      </p>

      <header className={styles.header}>
        <h2 className={styles.title} data-testid="convergence-query">
          {t('convergence.forQuery')} <span className={styles.query}>“{screen.query}”</span>
        </h2>
        <p className={styles.meta} data-testid="convergence-meta">
          {screen.opportunities.length} {t('convergence.opportunities')}
          <span className={styles.dot} aria-hidden="true">
            ·
          </span>
          {screen.source}
          <span className={styles.dot} aria-hidden="true">
            ·
          </span>
          {t('convergence.asOf')} {formatDateTime(screen.queried_at)} UTC
        </p>
      </header>

      <ul className={styles.list}>
        {/* Pin edge-descending in the view (Plan 0089): the screener already ranks by
         * -implied_return_if_right, and this defensive stable sort guarantees the
         * largest-upside opportunity leads even if a future producer reorders the
         * payload — the ranking key is unchanged, only re-asserted at render. */}
        {[...screen.opportunities]
          .sort((a, b) => b.implied_return_if_right - a.implied_return_if_right)
          .map((opportunity) => (
            <li
              key={opportunity.market_id}
              className={styles.card}
              data-testid="convergence-opportunity"
              data-market-id={opportunity.market_id}
            >
              <OpportunityCard opportunity={opportunity} />
            </li>
          ))}
      </ul>
    </section>
  )
}

function OpportunityCard({ opportunity }: { opportunity: ConvergenceOpportunity }): JSX.Element {
  const level = opportunity.resolution_risk.level
  const hasCaution =
    opportunity.liquidity_caution !== null &&
    opportunity.liquidity_caution !== undefined &&
    opportunity.liquidity_caution !== ''
  const marketUrl = safePolymarketUrl(opportunity.market_url)

  return (
    <>
      <div className={styles.cardHeader}>
        <p className={styles.question}>{opportunity.question}</p>
        <span
          className={`${styles.badge} ${styles[`risk_${level}`]}`}
          data-testid="resolution-risk-badge"
          data-level={level}
        >
          {t('convergence.resolutionRisk')}: {t(RISK_LEVEL_KEY[level])}
        </span>
      </div>

      <dl className={styles.grid}>
        <div className={styles.row}>
          <dt>{t('convergence.outcome')}</dt>
          <dd data-testid="opportunity-outcome">
            <span className={styles.outcome}>{opportunity.outcome_label}</span>
            <span className={styles.dot} aria-hidden="true">
              ·
            </span>
            {formatProbability(opportunity.implied_probability)}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>{t('convergence.returnIfRight')}</dt>
          <dd data-testid="opportunity-return">
            <span className={styles.returnValue}>
              {formatPct(opportunity.implied_return_if_right)}
            </span>
            <span className={styles.grossNote}>{t('convergence.returnGrossNote')}</span>
          </dd>
        </div>

        <div className={styles.row}>
          <dt>{t('convergence.timeToResolution')}</dt>
          <dd data-testid="opportunity-ttr">
            {formatDuration(opportunity.time_to_resolution)}
            <span className={styles.closesAt}>
              {t('convergence.closesAt')} {formatDateTime(opportunity.closes_at)} UTC
            </span>
          </dd>
        </div>

        {opportunity.volume_usd !== null && opportunity.volume_usd !== undefined && (
          <div className={styles.row}>
            <dt>{t('convergence.volume')}</dt>
            <dd data-testid="opportunity-volume">{formatUsd(opportunity.volume_usd)}</dd>
          </div>
        )}
      </dl>

      {/* The resolution-risk reasons — always present, the honesty core of the
       * plan: a labeled heuristic, never a probability of dispute. */}
      <section className={styles.risk} aria-label={t('convergence.resolutionRisk')}>
        <h3 className={styles.riskTitle}>
          {t('convergence.resolutionRisk')}{' '}
          <span className={styles.heuristicNote}>{t('convergence.riskHeuristicNote')}</span>
        </h3>
        <ul className={styles.reasons} data-testid="resolution-risk-reasons">
          {opportunity.resolution_risk.reasons.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      </section>

      {hasCaution && (
        <p className={styles.caution} role="note" data-testid="liquidity-caution">
          {opportunity.liquidity_caution}
        </p>
      )}

      <p className={styles.lockup} data-testid="capital-lockup">
        {opportunity.capital_lockup_note}
      </p>

      {/* Provenance/citation link — where the public fact lives (Plan 0089). Read-only:
       * it opens the market page in the OS browser, never navigates the renderer and
       * is never a trade control (ADR-0008 / ADR-0029). Rendered only when the URL
       * host-validates to polymarket.com. */}
      {marketUrl !== null && (
        <p className={styles.provenance}>
          <a
            className={styles.marketLink}
            href={marketUrl}
            rel="noreferrer"
            data-testid="market-link"
            onClick={(e) => {
              e.preventDefault()
              void window.api?.shell?.openExternal({ url: marketUrl })
            }}
          >
            {t('convergence.viewOnPolymarket')}
          </a>
        </p>
      )}
    </>
  )
}

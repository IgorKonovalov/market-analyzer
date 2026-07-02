/**
 * Recommendations view (Plan 0039 phase 2, ADR-0029).
 *
 * A reactive, read-only surface: it renders the latest `Recommendation` the
 * agent produced via the `recommendation.completed v1` SSE event (routed
 * through `App`'s single `useEventStream`, Zod-validated in the dispatcher).
 *
 * The defining constraint (ADR-0025 boundary): a recommendation with
 * entry/stop/target levels *looks like an order ticket*, so this view makes
 * the advisory-only nature unmissable and offers NO path to act — no submit,
 * no buy/sell, no "send to broker", no interactive control of any kind. A
 * renderer spec enforces that as a test, not a guideline.
 *
 * Honest uncertainty (ADR-0029): conviction is shown as what it is — a
 * derived, often-modest number with its basis alongside — and a low-conviction
 * call is styled quietly, never as a strong verdict. The advisory levels stay
 * in this panel; if the user wants them on the price chart, the agent can push
 * labeled `price_line` overlays through the existing chart channel.
 */
import { formatDateTime } from '../lib/format'
import type { BasisValue, Recommendation } from '../types/events'
import styles from './RecommendationsView.module.css'

interface Props {
  /** The latest recommendation, or `null` before any `recommendation.completed` event. */
  recommendation: Recommendation | null
}

/** Unsigned price display — signDisplay would misread as P&L on a price level. */
const PRICE_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const CONVICTION_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

type ConvictionStrength = 'low' | 'moderate' | 'high'

/** Bands drive styling only (the number itself is always shown): a marginal
 * edge must read quietly, per the plan's "low conviction is not styled as a
 * strong call" acceptance criterion. */
function convictionStrength(conviction: number): ConvictionStrength {
  if (conviction >= 0.66) return 'high'
  if (conviction >= 0.33) return 'moderate'
  return 'low'
}

const DIRECTION_LABEL: Record<Recommendation['direction'], string> = {
  long: 'long',
  short: 'short',
  flat: 'flat — no actionable edge',
}

export function RecommendationsView({ recommendation }: Props): JSX.Element {
  if (recommendation === null) {
    return (
      <section className={styles.view} aria-label="Advisory recommendation">
        <p className={styles.empty} data-testid="recommendation-empty">
          No recommendation yet — ask the agent for one via the `recommend` tool.
        </p>
      </section>
    )
  }

  const strength = convictionStrength(recommendation.conviction)
  const hasLevels =
    recommendation.direction !== 'flat' &&
    (recommendation.entry_zone != null ||
      recommendation.stop != null ||
      recommendation.targets.length > 0)

  return (
    <section className={styles.view} aria-label="Advisory recommendation">
      <p className={styles.advisoryBanner} data-testid="advisory-label" role="note">
        <strong>Advisory only.</strong> This is a recommendation, not an order ticket — nothing in
        this app can act on it. The agent recommends; you decide.
      </p>

      <header className={styles.header}>
        <h2 className={styles.title} data-testid="recommendation-title">
          <span className={styles.symbol}>{recommendation.symbol}</span>
          <span className={styles.dot} aria-hidden="true">
            ·
          </span>
          <span className={styles.timeframe}>{recommendation.timeframe}</span>
        </h2>
        <p className={styles.asOf} data-testid="recommendation-as-of">
          as of {formatDateTime(recommendation.as_of_bar_ts)} UTC (last closed bar the basis saw)
        </p>
      </header>

      <dl className={styles.grid}>
        <div className={styles.row}>
          <dt>Direction</dt>
          <dd
            data-testid="recommendation-direction"
            data-direction={recommendation.direction}
            className={styles[`dir_${recommendation.direction}`]}
          >
            {DIRECTION_LABEL[recommendation.direction]}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>Conviction</dt>
          <dd>
            <span
              className={styles.conviction}
              data-testid="recommendation-conviction"
              data-strength={strength}
            >
              {CONVICTION_FORMAT.format(recommendation.conviction)} — {strength}
            </span>
            <span className={styles.muted}>
              {' '}
              derived (forecast probability × backtested edge), never invented
            </span>
          </dd>
        </div>
      </dl>

      {hasLevels && (
        <section className={styles.levels} aria-label="Advisory levels">
          <h3 className={styles.levelsTitle}>Advisory levels — for your judgement, not a ticket</h3>
          <dl className={styles.grid}>
            {recommendation.entry_zone != null && (
              <div className={styles.row}>
                <dt>Entry zone (advisory)</dt>
                <dd data-testid="recommendation-entry">
                  {PRICE_FORMAT.format(recommendation.entry_zone[0])} –{' '}
                  {PRICE_FORMAT.format(recommendation.entry_zone[1])}
                </dd>
              </div>
            )}
            {recommendation.stop != null && (
              <div className={styles.row}>
                <dt>Stop (advisory)</dt>
                <dd data-testid="recommendation-stop">
                  {PRICE_FORMAT.format(recommendation.stop)}
                </dd>
              </div>
            )}
            {recommendation.targets.length > 0 && (
              <div className={styles.row}>
                <dt>Target{recommendation.targets.length === 1 ? '' : 's'} (advisory)</dt>
                <dd data-testid="recommendation-targets">
                  {recommendation.targets.map((t) => PRICE_FORMAT.format(t)).join(', ')}
                </dd>
              </div>
            )}
          </dl>
        </section>
      )}

      <section className={styles.rationale} aria-label="Rationale">
        <h3 className={styles.sectionTitle}>Why</h3>
        <ul className={styles.rationaleList} data-testid="recommendation-rationale">
          {recommendation.rationale.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      <section className={styles.basis} aria-label="Basis">
        <h3 className={styles.sectionTitle}>What backed this call</h3>
        <div className={styles.basisGrid}>
          <BasisList
            label="Conditions"
            items={recommendation.basis.conditions}
            testId="basis-conditions"
          />
          <BasisList
            label="Live signals"
            items={recommendation.basis.signals}
            testId="basis-signals"
          />
          <BasisFacts
            label="Backtested edge"
            facts={recommendation.basis.backtest ?? null}
            testId="basis-backtest"
          />
          <BasisFacts
            label="Forecast"
            facts={recommendation.basis.forecast ?? null}
            testId="basis-forecast"
          />
        </div>
      </section>

      <p className={styles.disclaimer}>
        Labeled advisory (ADR-0029): the basis above travels with every call, and a flat verdict is
        an honest &quot;no actionable edge&quot;, never a fabricated call.
      </p>
    </section>
  )
}

interface BasisListProps {
  label: string
  items: string[]
  testId: string
}

function BasisList({ label, items, testId }: BasisListProps): JSX.Element {
  return (
    <div className={styles.basisBlock} data-testid={testId}>
      <h4 className={styles.basisLabel}>{label}</h4>
      {items.length === 0 ? (
        <p className={styles.muted}>none</p>
      ) : (
        <ul className={styles.basisList}>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface BasisFactsProps {
  label: string
  facts: Record<string, BasisValue> | null
  testId: string
}

/** Renders a flat scalar-fact record (walk-forward stats, forecast
 * probabilities). `null` means the leg was not part of this (flat) basis —
 * said out loud rather than hidden. */
function BasisFacts({ label, facts, testId }: BasisFactsProps): JSX.Element {
  return (
    <div className={styles.basisBlock} data-testid={testId}>
      <h4 className={styles.basisLabel}>{label}</h4>
      {facts === null ? (
        <p className={styles.muted}>not part of this basis</p>
      ) : (
        <dl className={styles.factList}>
          {Object.entries(facts).map(([key, value]) => (
            <div key={key} className={styles.factRow}>
              <dt>{key}</dt>
              <dd>{formatBasisValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

function formatBasisValue(value: BasisValue): string {
  if (value === null) return '—'
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : CONVICTION_FORMAT.format(value)
  }
  return String(value)
}

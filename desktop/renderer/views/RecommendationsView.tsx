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
import { GlossaryTerm } from '../components/GlossaryTerm'
import { formatDateTime } from '../lib/format'
import type { BasisValue, FusionCheck, Recommendation } from '../types/events'
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

/**
 * Friendly names for the forecast feature-set tiers (Plan 0066, ADR-0057). The
 * key is the opaque `feature_set_id` content hash the sidecar ships in
 * `basis.forecast` — mirrored here from `src/market_analyser/forecast/features.py`
 * (`FEATURE_SET_ID` / `FEATURE_SET_ID_V2` / `FEATURE_SET_ID_V2_DEEP`). These are
 * frozen per ADR-0057; if the feature tuples ever change the hashes change too,
 * and an unmapped id degrades gracefully to showing the raw hash (see
 * `ForecastBasis`). The renderer can't compute the hash, so the mirror is the
 * only client-side path to a readable tier name.
 */
const FEATURE_SET_LABELS: Record<string, string> = {
  '49c020d0794fd2a7': 'v1 (OHLCV only)',
  '2fb15f47d51cbafa': 'v2-full',
  '3d8643321ac2cec3': 'v2-deep',
}

/** The two forecast-basis keys (Plan 0066) rendered as a readable tier line
 * rather than raw scalar rows — pulled out of the generic fact list. */
const TIER_KEYS = new Set(['feature_set_id', 'fallback_reason'])

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
          <dt>
            <GlossaryTerm termKey="direction">Direction</GlossaryTerm>
          </dt>
          <dd
            data-testid="recommendation-direction"
            data-direction={recommendation.direction}
            className={styles[`dir_${recommendation.direction}`]}
          >
            {DIRECTION_LABEL[recommendation.direction]}
          </dd>
        </div>

        <div className={styles.row}>
          <dt>
            <GlossaryTerm termKey="conviction">Conviction</GlossaryTerm>
          </dt>
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
                <dt>
                  <GlossaryTerm termKey="entry_zone">Entry zone</GlossaryTerm> (advisory)
                </dt>
                <dd data-testid="recommendation-entry">
                  {PRICE_FORMAT.format(recommendation.entry_zone[0])} –{' '}
                  {PRICE_FORMAT.format(recommendation.entry_zone[1])}
                </dd>
              </div>
            )}
            {recommendation.stop != null && (
              <div className={styles.row}>
                <dt>
                  <GlossaryTerm termKey="stop">Stop</GlossaryTerm> (advisory)
                </dt>
                <dd data-testid="recommendation-stop">
                  {PRICE_FORMAT.format(recommendation.stop)}
                </dd>
              </div>
            )}
            {recommendation.targets.length > 0 && (
              <div className={styles.row}>
                <dt>
                  <GlossaryTerm termKey="targets">
                    Target{recommendation.targets.length === 1 ? '' : 's'}
                  </GlossaryTerm>{' '}
                  (advisory)
                </dt>
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

      {recommendation.basis.checks.length > 0 && (
        <ChecksTable checks={recommendation.basis.checks} />
      )}

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
          <ForecastBasis facts={recommendation.basis.forecast ?? null} testId="basis-forecast" />
        </div>
      </section>

      <p className={styles.disclaimer}>
        Labeled advisory (ADR-0029): the basis above travels with every call, and a flat verdict is
        an honest &quot;no actionable edge&quot;, never a fabricated call.
      </p>
    </section>
  )
}

interface ChecksTableProps {
  checks: FusionCheck[]
}

/** The full fusion trace (Plan 0063, ADR-0058), rendered quietly below the
 * rationale — never behind an expansion, so a flat verdict's failed gates are
 * as legible as a call's passed ones. Pass/fail is a word ("pass"/"FAIL"),
 * never color alone; an absent threshold/actual (a recorded fact with no pass
 * bar) renders as a dash. A plain table: nothing here is interactive. */
function ChecksTable({ checks }: ChecksTableProps): JSX.Element {
  return (
    <section className={styles.checks} aria-label="Fusion checks">
      <h3 className={styles.sectionTitle}>Every gate checked</h3>
      <table className={styles.checksTable} data-testid="recommendation-checks">
        <thead>
          <tr>
            <th scope="col">leg</th>
            <th scope="col">check</th>
            <th scope="col">threshold</th>
            <th scope="col">actual</th>
            <th scope="col">result</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((check) => (
            <tr key={`${check.leg}:${check.check}`} data-passed={check.passed}>
              <td className={styles.checkLeg}>
                <GlossaryTerm termKey={check.leg}>{check.leg}</GlossaryTerm>
              </td>
              <td>{check.check}</td>
              <td className={styles.checkValue}>{formatBasisValue(check.threshold ?? null)}</td>
              <td className={styles.checkValue}>{formatBasisValue(check.actual ?? null)}</td>
              <td className={styles.checkResult} data-passed={check.passed}>
                {check.passed ? 'pass' : 'FAIL'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.checksNote}>
        The trace records the fusion&apos;s decision; a directional call means every gate passed.
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
              <dt>
                <GlossaryTerm termKey={key}>{key}</GlossaryTerm>
              </dt>
              <dd>{formatBasisValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

interface ForecastBasisProps {
  facts: Record<string, BasisValue> | null
  testId: string
}

/**
 * The forecast leg of the basis (Plan 0066, ADR-0057). Same scalar-fact
 * rendering as `BasisFacts`, but the two tier keys (`feature_set_id`,
 * `fallback_reason`) are lifted out into a readable line — "Forecast ran on the
 * v2-deep feature set" plus the fallback sentence when a richer tier was
 * skipped — so a reader sees which model backed the call without decoding a
 * hash. When neither key is present the block renders exactly as it did before
 * this plan (the remaining scalars in a plain fact list). Read-only: no
 * interactive element, per the ADR-0025/0029 no-action posture.
 */
function ForecastBasis({ facts, testId }: ForecastBasisProps): JSX.Element {
  if (facts === null) {
    return (
      <div className={styles.basisBlock} data-testid={testId}>
        <h4 className={styles.basisLabel}>Forecast</h4>
        <p className={styles.muted}>not part of this basis</p>
      </div>
    )
  }

  const featureSetId = typeof facts.feature_set_id === 'string' ? facts.feature_set_id : null
  const fallbackReason = typeof facts.fallback_reason === 'string' ? facts.fallback_reason : null
  const otherFacts = Object.entries(facts).filter(([key]) => !TIER_KEYS.has(key))

  return (
    <div className={styles.basisBlock} data-testid={testId}>
      <h4 className={styles.basisLabel}>Forecast</h4>
      {featureSetId !== null && (
        <p className={styles.forecastTier} data-testid="forecast-tier">
          {FEATURE_SET_LABELS[featureSetId] !== undefined
            ? `Forecast ran on the ${FEATURE_SET_LABELS[featureSetId]} feature set.`
            : `Forecast ran on feature set ${featureSetId}.`}
        </p>
      )}
      {fallbackReason !== null && (
        <p className={styles.forecastFallback} data-testid="forecast-fallback">
          {fallbackReason}
        </p>
      )}
      {otherFacts.length > 0 && (
        <dl className={styles.factList}>
          {otherFacts.map(([key, value]) => (
            <div key={key} className={styles.factRow}>
              <dt>
                <GlossaryTerm termKey={key}>{key}</GlossaryTerm>
              </dt>
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

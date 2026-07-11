/**
 * Forecast view (Plan 0037 phase 2, ADR-0030 / ADR-0054).
 *
 * A reactive, read-only surface: it renders the latest
 * `MultiHorizonForecastResult` the agent produced via the
 * `forecast.completed v1` SSE event (routed through `App`'s single
 * `useEventStream`, Zod-validated in the dispatcher). One block per horizon —
 * each trained, walk-forward-validated, and baseline-gated independently, so
 * "edge at 1 bar, no edge at 21 bars" renders as exactly that, side by side.
 *
 * The defining constraint (ADR-0030's honest-uncertainty invariant, an
 * acceptance criterion of this plan, not polish):
 *   - a horizon that did not beat its baseline shows an explicit "no edge
 *     over baseline" state and NO probability bars — never a fabricated
 *     number;
 *   - a probability only reads as conviction when the edge is `clear` AND the
 *     probability itself is decisively away from chance; a 0.52 — or any
 *     probability under a `marginal` edge — renders quietly.
 *
 * A forecast is a CONDITION (a calibrated probability), never a
 * recommendation — no action language, no levels, nothing to click. The one
 * sanctioned interactive element is the "Why" expand/collapse (Plan 0063):
 * a native details/summary disclosing the validated drivers, input
 * freshness, and the explanation artifact's path — a provenance fact
 * rendered as plain text, never a link (the renderer never touches the
 * filesystem).
 */
import { GlossaryTerm } from '../components/GlossaryTerm'
import { formatDateTime } from '../lib/format'
import { t } from '../lib/i18n'
import { enumLabel } from '../lib/reasonCodes'
import type {
  ExplanationSummary,
  HorizonForecast,
  MultiHorizonForecastResult,
  RegimeForecast,
  RegimeState,
  VolatilityForecast,
} from '../types/events'
import styles from './ForecastView.module.css'

interface Props {
  /** The latest direction forecast, or `null` before any `forecast.completed`. */
  forecast: MultiHorizonForecastResult | null
  /** The latest volatility forecast (Plan 0077 phase 6), or `null`/absent before
   * any `volatility_forecast.completed`. */
  volatility?: VolatilityForecast | null
  /** The latest regime forecast (Plan 0077 phase 6), or `null`/absent before any
   * `regime_forecast.completed`. */
  regime?: RegimeForecast | null
}

const PROB_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const SKILL_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
})

/** Per-bar realised volatility is a small fraction (~0.01–0.10); show four
 * places so a 0.0143 does not collapse to 0.01. */
const VOL_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
})

/** The regime taxonomy in a fixed, deterministic display order (down → sideways
 * → up, quiet before volatile) so the transition bars never reorder run-to-run. */
const REGIME_ORDER: readonly RegimeState[] = [
  'down_quiet',
  'down_volatile',
  'sideways_quiet',
  'sideways_volatile',
  'up_quiet',
  'up_volatile',
]

/** A probability reads as emphatic only when it is decisively away from
 * chance — and even then only under a `clear` edge (see `barEmphasis`). A
 * calibrated 0.52 is a whisper, not a verdict. */
const EMPHASIS_PROB_THRESHOLD = 0.6

/** Emphasis for one probability value: `strong` only under a clear edge AND a
 * decisive probability; everything else — marginal edge, near-chance value —
 * stays `quiet` (the plan's marginal-is-not-conviction acceptance criterion). */
function barEmphasis(block: HorizonForecast, prob: number): 'strong' | 'quiet' {
  return block.edge_strength === 'clear' && prob >= EMPHASIS_PROB_THRESHOLD ? 'strong' : 'quiet'
}

/** "1 bar" / "5 bars" — the wire's unit is bars of the result's timeframe. */
function horizonLabel(horizonBars: number): string {
  return `${horizonBars} bar${horizonBars === 1 ? '' : 's'}`
}

/**
 * The Forecast panel composes three independent, non-directional-vs-directional
 * forecast kinds (Plan 0077 phase 6): the existing direction forecast, plus the
 * volatility and regime forecasts. Each arrives on its own SSE event at its own
 * time, so each renders independently with its own state — and none is shown as a
 * confident number when it did not beat its baseline (ADR-0070's honest
 * uncertainty). The panel is empty only until the FIRST of the three arrives.
 */
export function ForecastView({ forecast, volatility, regime }: Props): JSX.Element {
  if (forecast === null && volatility == null && regime == null) {
    return (
      <section className={styles.view} aria-label={t('forecast.panelLabel')}>
        <p className={styles.empty} data-testid="forecast-empty">
          {t('forecast.emptyState')}
        </p>
      </section>
    )
  }

  return (
    <section className={styles.view} aria-label={t('forecast.panelLabel')}>
      <p className={styles.conditionBanner} data-testid="forecast-condition-note" role="note">
        {t('forecast.conditionBannerLead')} <strong>{t('forecast.conditionBannerStrong')}</strong>{' '}
        {t('forecast.conditionBannerTail')}
      </p>
      {forecast !== null && <DirectionForecastSection forecast={forecast} />}
      {volatility != null && <VolatilitySection volatility={volatility} />}
      {regime != null && <RegimeSection regime={regime} />}
    </section>
  )
}

function DirectionForecastSection({
  forecast,
}: {
  forecast: MultiHorizonForecastResult
}): JSX.Element {
  // All blocks of one call share the same feature set; the series list lives
  // on each trained block's provenance. An empty list = the OHLCV-only v1
  // fallback — stated out loud, never silent (ADR-0054). `fallback_reason`
  // (Plan 0061) says WHY a v1 fallback happened; it is call-level too (the
  // tool computes it once per call), so the first block's copy speaks for all.
  const firstProvenance = forecast.horizons.find((b) => b.provenance != null)?.provenance ?? null
  const seriesInputs = firstProvenance?.series_inputs ?? []
  const fallbackReason = firstProvenance?.fallback_reason ?? null

  // Plan 0063: each trained block carries its own explanation summary (the
  // drivers genuinely differ per horizon); the artifact path is call-level —
  // every block names the same file, so the first one speaks for all. An
  // envelope without explanations renders exactly the pre-0063 panel.
  const explainedBlocks = forecast.horizons.flatMap((block) =>
    block.provenance?.explanation != null
      ? [{ horizonBars: block.horizon_bars, explanation: block.provenance.explanation }]
      : [],
  )
  const artifactPath =
    explainedBlocks.map(({ explanation }) => explanation.artifact).find((path) => path != null) ??
    null
  // Plan 0069 phase 5: the fixed association-not-causation disclaimer is rendered
  // from the sidecar's `disclaimer_code` (every explained block carries the same
  // one), not hardcoded chrome — so it localizes with the rest of the panel.
  const disclaimerCode = explainedBlocks[0]?.explanation.disclaimer_code ?? 'disclaimer.importance'

  return (
    <section
      className={styles.kindSection}
      aria-label={t('forecast.viewLabel')}
      data-testid="forecast-direction-section"
    >
      <h3 className={styles.kindTitle}>{t('forecast.viewLabel')}</h3>

      <header className={styles.header}>
        <h2 className={styles.title} data-testid="forecast-title">
          <span className={styles.symbol}>{forecast.symbol}</span>
          <span className={styles.dot} aria-hidden="true">
            ·
          </span>
          <span className={styles.timeframe}>{forecast.timeframe}</span>
        </h2>
        <p className={styles.asOf} data-testid="forecast-as-of">
          {t('forecast.asOf')} {formatDateTime(forecast.as_of_bar_ts)} UTC{' '}
          {t('forecast.asOfSuffix')}
        </p>
        <p className={styles.featureSet} data-testid="forecast-feature-set">
          <GlossaryTerm termKey="feature_set_id">{t('forecast.featureSet')}</GlossaryTerm>{' '}
          <code>{forecast.feature_set_id}</code>
          {seriesInputs.length === 0 ? (
            <span className={styles.muted}> {t('forecast.featuresPriceOnly')}</span>
          ) : (
            <span className={styles.muted}>
              {' '}
              {t('forecast.exogenousSeries')} {seriesInputs.map((s) => s.series_id).join(', ')}
            </span>
          )}
          {fallbackReason != null && (
            <span className={styles.muted} data-testid="forecast-fallback-reason">
              {' '}
              — {fallbackReason}
            </span>
          )}
        </p>
      </header>

      {explainedBlocks.length > 0 && (
        <details className={styles.why} data-testid="forecast-why">
          <summary className={styles.whySummary}>{t('forecast.whySummary')}</summary>
          <div className={styles.whyBody}>
            {explainedBlocks.map(({ horizonBars, explanation }) => (
              <WhyDrivers key={horizonBars} horizonBars={horizonBars} explanation={explanation} />
            ))}
            {seriesInputs.length > 0 && (
              <div className={styles.whyGroup} data-testid="forecast-why-freshness">
                <h4 className={styles.whyGroupTitle}>{t('forecast.inputFreshness')}</h4>
                <ul className={styles.freshnessList}>
                  {seriesInputs.map((series) => (
                    <li key={series.series_id}>
                      <code>{series.series_id}</code>{' '}
                      {series.last_point_ts != null ? (
                        <>
                          {t('forecast.freshestPoint', {
                            ts: formatDateTime(new Date(series.last_point_ts * 1000).toISOString()),
                          })}{' '}
                          UTC
                        </>
                      ) : (
                        t('forecast.noObservablePoint')
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {artifactPath != null && (
              <p className={styles.whyArtifact} data-testid="forecast-why-artifact">
                {t('forecast.artifactLead')} <code>{artifactPath}</code>{' '}
                {t('forecast.artifactTail')}
              </p>
            )}
            <p className={styles.whyDisclaimer}>{t(disclaimerCode)}</p>
          </div>
        </details>
      )}

      <div className={styles.blocks} data-testid="forecast-blocks">
        {forecast.horizons.map((block) => (
          <HorizonBlock key={block.horizon_bars} block={block} />
        ))}
      </div>

      <p className={styles.disclaimer}>{t('forecast.disclaimer')}</p>
    </section>
  )
}

/**
 * The volatility forecast (Plan 0077 phase 6): a predicted realised-volatility
 * band, its deterministic baseline, and the honest beats-baseline verdict. When
 * the model did NOT beat baseline (the common case — phase 4), the model band is
 * withheld and the baseline reading is surfaced as the honest estimate; zero
 * fabricated precision. A magnitude, never a direction.
 */
function VolatilitySection({ volatility }: { volatility: VolatilityForecast }): JSX.Element {
  const v = volatility
  const trusted = v.beats_baseline && v.predicted_vol != null
  const baselineKindLabel =
    v.baseline_kind != null ? enumLabel('vol_baseline', v.baseline_kind) : null
  const val = v.validation
  return (
    <section
      className={styles.kindSection}
      aria-label={t('forecast.volatilityLabel')}
      data-testid="forecast-volatility-section"
    >
      <h3 className={styles.kindTitle}>{t('forecast.volatilityTitle')}</h3>
      <p className={styles.kindLede}>{t('forecast.volatilityLede')}</p>
      <p className={styles.asOf}>
        <span className={styles.symbol}>{v.symbol}</span> {v.timeframe} {t('forecast.asOf')}{' '}
        {formatDateTime(v.as_of_bar_ts)} UTC {horizonLabel(v.horizon_bars)} {t('forecast.ahead')}
      </p>

      <div className={styles.block} data-strength={trusted ? 'clear' : 'no_edge'}>
        {trusted ? (
          <dl className={styles.metrics} data-testid="volatility-predicted">
            <div className={styles.metricRow}>
              <dt className={styles.metricLabel}>{t('forecast.predictedVol')}</dt>
              <dd className={styles.metricValue}>
                {VOL_FORMAT.format(v.predicted_vol ?? 0)}
                <span className={styles.muted}> {t('forecast.perBarVol')}</span>
              </dd>
            </div>
            {v.band != null && (
              <div className={styles.metricRow}>
                <dt className={styles.metricLabel}>{t('forecast.volBand')}</dt>
                <dd className={styles.metricValue} data-testid="volatility-band">
                  {VOL_FORMAT.format(v.band[0])} – {VOL_FORMAT.format(v.band[1])}
                </dd>
              </div>
            )}
          </dl>
        ) : (
          <p className={styles.noEdge} data-testid="volatility-no-edge">
            <strong>{t('forecast.volNoEdgeStrong')}</strong> {t('forecast.volNoEdgeBody')}
          </p>
        )}

        {v.baseline_vol != null && (
          <dl className={styles.metrics}>
            <div className={styles.metricRow}>
              <dt className={styles.metricLabel}>
                {t('forecast.baselineVol')}
                {baselineKindLabel != null && (
                  <span className={styles.muted}> ({baselineKindLabel})</span>
                )}
              </dt>
              <dd className={styles.metricValue} data-testid="volatility-baseline">
                {VOL_FORMAT.format(v.baseline_vol)}
              </dd>
            </div>
          </dl>
        )}

        <p className={styles.skillLine} data-testid="volatility-score">
          {t('forecast.outOfSample')}{' '}
          <span className={styles.skillValue}>{t('forecast.qlike')}</span>{' '}
          <span className={styles.skillValue}>
            {val.model_qlike != null
              ? SKILL_FORMAT.format(val.model_qlike)
              : t('forecast.unscored')}
          </span>{' '}
          {t('forecast.vs')} {t('forecast.baseline')}{' '}
          <span className={styles.skillValue}>
            {val.baseline_qlike != null
              ? SKILL_FORMAT.format(val.baseline_qlike)
              : t('forecast.unscored')}
          </span>
          {v.score_margin != null && (
            <span className={styles.muted}>
              {' '}
              ({t('forecast.margin')} {v.score_margin >= 0 ? '+' : ''}
              {SKILL_FORMAT.format(v.score_margin)})
            </span>
          )}
          <span className={styles.muted}>
            {' '}
            · {val.n_scored} {t('forecast.scoredBars')} {t('forecast.across')} {val.n_splits}{' '}
            {t('forecast.folds')}
          </span>
        </p>
      </div>

      <p className={styles.disclaimer}>{t('forecast.volDisclaimer')}</p>
    </section>
  )
}

/**
 * The regime forecast (Plan 0077 phase 6): the trailing rule-based current
 * regime and, when the transition model beat its persistence baseline, the full
 * next-period distribution over the six-state taxonomy (all six bars, current
 * marked). When it did NOT beat persistence (the common case — phase 4), the
 * distribution is withheld and the honest expectation "the regime holds" is
 * stated. A condition, never a direction.
 */
function RegimeSection({ regime }: { regime: RegimeForecast }): JSX.Element {
  const r = regime
  const trusted = r.beats_baseline && r.transition_probs != null
  const currentLabel =
    r.current_regime != null
      ? enumLabel('regime', r.current_regime)
      : t('recommendations.regimeUndefined')
  const val = r.validation
  return (
    <section
      className={styles.kindSection}
      aria-label={t('forecast.regimeLabel')}
      data-testid="forecast-regime-section"
    >
      <h3 className={styles.kindTitle}>{t('forecast.regimeTitle')}</h3>
      <p className={styles.kindLede}>{t('forecast.regimeLede')}</p>
      <p className={styles.asOf}>
        <span className={styles.symbol}>{r.symbol}</span> {r.timeframe} {t('forecast.asOf')}{' '}
        {formatDateTime(r.as_of_bar_ts)} UTC {horizonLabel(r.horizon_bars)} {t('forecast.ahead')}
      </p>

      <div className={styles.block} data-strength={trusted ? 'clear' : 'no_edge'}>
        <dl className={styles.metrics}>
          <div className={styles.metricRow}>
            <dt className={styles.metricLabel}>{t('forecast.currentRegime')}</dt>
            <dd className={styles.metricValue} data-testid="regime-current">
              {currentLabel}
            </dd>
          </div>
        </dl>

        {trusted ? (
          <div data-testid="regime-transition">
            <h4 className={styles.whyGroupTitle}>
              {t('forecast.regimeTransitionHeading', { horizon: horizonLabel(r.horizon_bars) })}
            </h4>
            <dl className={styles.probList}>
              {REGIME_ORDER.map((state) => (
                <RegimeBar
                  key={state}
                  label={enumLabel('regime', state)}
                  prob={r.transition_probs?.[state] ?? 0}
                  isCurrent={state === r.current_regime}
                />
              ))}
            </dl>
          </div>
        ) : (
          <p className={styles.noEdge} data-testid="regime-no-edge">
            <strong>{t('forecast.regimeNoEdgeStrong')}</strong> {t('forecast.regimeNoEdgeBody')}
          </p>
        )}

        <p className={styles.skillLine} data-testid="regime-score">
          {t('forecast.outOfSample')}{' '}
          <span className={styles.skillValue}>{t('forecast.brier')}</span>{' '}
          <span className={styles.skillValue}>
            {val.model_brier != null
              ? SKILL_FORMAT.format(val.model_brier)
              : t('forecast.unscored')}
          </span>{' '}
          {t('forecast.vs')} {t('forecast.persistence')}{' '}
          <span className={styles.skillValue}>
            {val.persistence_brier != null
              ? SKILL_FORMAT.format(val.persistence_brier)
              : t('forecast.unscored')}
          </span>
          {r.score_margin != null && (
            <span className={styles.muted}>
              {' '}
              ({t('forecast.margin')} {r.score_margin >= 0 ? '+' : ''}
              {SKILL_FORMAT.format(r.score_margin)})
            </span>
          )}
          <span className={styles.muted}>
            {' '}
            · {val.n_scored} {t('forecast.scoredBars')} {t('forecast.across')} {val.n_splits}{' '}
            {t('forecast.folds')}
          </span>
        </p>
      </div>

      <p className={styles.disclaimer}>{t('forecast.regimeDisclaimer')}</p>
    </section>
  )
}

interface RegimeBarProps {
  label: string
  prob: number
  isCurrent: boolean
}

/** One regime's next-period probability as a quiet, direction-agnostic bar
 * (neutral fill — regime is not bullish/bearish). The current regime is marked
 * so "sticky" is legible. */
function RegimeBar({ label, prob, isCurrent }: RegimeBarProps): JSX.Element {
  return (
    <div className={styles.probRow}>
      <dt className={styles.probLabel}>
        {label}
        {isCurrent && <span className={styles.muted}> {t('forecast.regimeCurrentTag')}</span>}
      </dt>
      <dd className={styles.probCell}>
        <span className={styles.track} aria-hidden="true">
          <span
            className={styles.fill}
            data-kind="flat"
            style={{ width: `${(prob * 100).toFixed(1)}%` }}
          />
        </span>
        <span className={styles.probValue} data-emphasis="quiet">
          {PROB_FORMAT.format(prob)}
        </span>
      </dd>
    </div>
  )
}

interface WhyDriversProps {
  horizonBars: number
  explanation: ExplanationSummary
}

/** One horizon's ordered top drivers as quiet horizontal bars: magnitude is
 * relative to the horizon's own strongest driver. A negative or zero
 * importance draws no bar (permutation importance can dip below zero on
 * noise); the number is still shown. */
function WhyDrivers({ horizonBars, explanation }: WhyDriversProps): JSX.Element {
  const drivers = explanation.top_drivers
  const maxImportance = Math.max(0, ...drivers.map((driver) => driver.importance))
  return (
    <div className={styles.whyGroup} data-testid={`forecast-why-drivers-${horizonBars}`}>
      <h4 className={styles.whyGroupTitle}>
        {horizonLabel(horizonBars)} {t('forecast.driversHeadingAhead')}{' '}
        <GlossaryTerm termKey="permutation_importance">{t('forecast.topDrivers')}</GlossaryTerm>
      </h4>
      {drivers.length === 0 ? (
        <p className={styles.muted}>{t(explanation.note_code ?? 'note.no_scored_folds')}</p>
      ) : (
        <ol className={styles.driverList}>
          {drivers.map((driver) => (
            <li key={driver.feature} className={styles.driverRow}>
              <GlossaryTerm termKey={driver.feature}>
                <code className={styles.driverName}>{driver.feature}</code>
              </GlossaryTerm>
              <span className={styles.track} aria-hidden="true">
                <span
                  className={styles.driverFill}
                  style={{ width: `${driverWidth(driver.importance, maxImportance)}%` }}
                />
              </span>
              <span className={styles.driverValue}>{SKILL_FORMAT.format(driver.importance)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function driverWidth(importance: number, maxImportance: number): number {
  if (maxImportance <= 0 || importance <= 0) return 0
  return (importance / maxImportance) * 100
}

interface HorizonBlockProps {
  block: HorizonForecast
}

function HorizonBlock({ block }: HorizonBlockProps): JSX.Element {
  const { validation } = block
  const hasProbabilities =
    validation.beats_baseline &&
    block.prob_up != null &&
    block.prob_down != null &&
    block.prob_flat != null

  return (
    <article
      className={styles.block}
      data-testid={`forecast-block-${block.horizon_bars}`}
      data-strength={block.edge_strength}
      aria-label={t('forecast.blockAriaLabel', { horizon: horizonLabel(block.horizon_bars) })}
    >
      <header className={styles.blockHeader}>
        <h3 className={styles.blockTitle}>
          {horizonLabel(block.horizon_bars)} {t('forecast.ahead')}
        </h3>
        <span
          className={styles.edgeBadge}
          data-testid={`forecast-edge-${block.horizon_bars}`}
          data-strength={block.edge_strength}
        >
          <GlossaryTerm termKey="edge_strength">
            {enumLabel('edge_strength', block.edge_strength)}
          </GlossaryTerm>
        </span>
      </header>

      {hasProbabilities ? (
        <dl className={styles.probList} data-testid={`forecast-probs-${block.horizon_bars}`}>
          <ProbBar
            block={block}
            label={t('forecast.directionUp')}
            kind="up"
            prob={block.prob_up ?? 0}
          />
          <ProbBar
            block={block}
            label={t('forecast.directionDown')}
            kind="down"
            prob={block.prob_down ?? 0}
          />
          <ProbBar
            block={block}
            label={t('forecast.directionFlat')}
            kind="flat"
            prob={block.prob_flat ?? 0}
          />
        </dl>
      ) : (
        <p className={styles.noEdge} data-testid={`forecast-no-edge-${block.horizon_bars}`}>
          <strong>{t('forecast.noEdgeStrong')}</strong> {t('forecast.noEdgeBody')}
        </p>
      )}

      <p className={styles.skillLine} data-testid={`forecast-skill-${block.horizon_bars}`}>
        {t('forecast.outOfSample')}{' '}
        <GlossaryTerm termKey="skill">{t('forecast.skill')}</GlossaryTerm>{' '}
        <span className={styles.skillValue}>
          {validation.skill != null
            ? SKILL_FORMAT.format(validation.skill)
            : t('forecast.unscored')}
        </span>{' '}
        {t('forecast.vs')}{' '}
        <GlossaryTerm termKey="baseline_skill">{t('forecast.baseline')}</GlossaryTerm>{' '}
        <span className={styles.skillValue}>
          {validation.baseline_skill != null
            ? SKILL_FORMAT.format(validation.baseline_skill)
            : t('forecast.unscored')}
        </span>
        {block.edge_margin != null && (
          <span className={styles.muted}>
            {' '}
            (<GlossaryTerm termKey="edge_margin">{t('forecast.margin')}</GlossaryTerm>{' '}
            {block.edge_margin >= 0 ? '+' : ''}
            {SKILL_FORMAT.format(block.edge_margin)})
          </span>
        )}
        <span className={styles.muted}>
          {' '}
          · {validation.n_scored}{' '}
          <GlossaryTerm termKey="n_scored">{t('forecast.scoredBars')}</GlossaryTerm>{' '}
          {t('forecast.across')} {validation.n_splits}{' '}
          <GlossaryTerm termKey="n_splits">{t('forecast.folds')}</GlossaryTerm>
        </span>
      </p>

      {block.provenance != null ? (
        <p
          className={styles.provenance}
          data-testid={`forecast-provenance-${block.horizon_bars}`}
          title={t('forecast.provenanceTitle', {
            model: block.provenance.model_version,
            libs: Object.entries(block.provenance.lib_versions)
              .map(([lib, version]) => `${lib} ${version}`)
              .join(', '),
            seed: block.provenance.seed,
          })}
        >
          {t('forecast.provenanceModelPrefix')}{' '}
          <code>{block.provenance.model_version.slice(0, 12)}…</code> {t('forecast.trainedThrough')}{' '}
          {formatDateTime(block.provenance.training_cutoff)} UTC
        </p>
      ) : (
        <p className={styles.provenance} data-testid={`forecast-provenance-${block.horizon_bars}`}>
          {t('forecast.noModelTrained')}
        </p>
      )}
    </article>
  )
}

interface ProbBarProps {
  block: HorizonForecast
  label: string
  kind: 'up' | 'down' | 'flat'
  prob: number
}

function ProbBar({ block, label, kind, prob }: ProbBarProps): JSX.Element {
  const emphasis = barEmphasis(block, prob)
  return (
    <div className={styles.probRow}>
      <dt className={styles.probLabel}>
        <GlossaryTerm termKey={`prob_${kind}`}>{label}</GlossaryTerm>
      </dt>
      <dd className={styles.probCell}>
        <span className={styles.track} aria-hidden="true">
          <span
            className={styles.fill}
            data-kind={kind}
            style={{ width: `${(prob * 100).toFixed(1)}%` }}
          />
        </span>
        <span
          className={styles.probValue}
          data-testid={`forecast-prob-${kind}-${block.horizon_bars}`}
          data-emphasis={emphasis}
        >
          {PROB_FORMAT.format(prob)}
        </span>
      </dd>
    </div>
  )
}

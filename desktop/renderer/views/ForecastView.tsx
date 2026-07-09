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
import type {
  ExplanationSummary,
  HorizonForecast,
  MultiHorizonForecastResult,
} from '../types/events'
import styles from './ForecastView.module.css'

interface Props {
  /** The latest forecast, or `null` before any `forecast.completed` event. */
  forecast: MultiHorizonForecastResult | null
}

const PROB_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const SKILL_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
})

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

const EDGE_LABEL: Record<HorizonForecast['edge_strength'], string> = {
  no_edge: 'no edge over baseline',
  marginal: 'marginal edge',
  clear: 'clear edge',
}

export function ForecastView({ forecast }: Props): JSX.Element {
  if (forecast === null) {
    return (
      <section className={styles.view} aria-label={t('forecast.viewLabel')}>
        <p className={styles.empty} data-testid="forecast-empty">
          {t('forecast.emptyState')}
        </p>
      </section>
    )
  }

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

  return (
    <section className={styles.view} aria-label={t('forecast.viewLabel')}>
      <p className={styles.conditionBanner} data-testid="forecast-condition-note" role="note">
        {t('forecast.conditionBannerLead')} <strong>{t('forecast.conditionBannerStrong')}</strong>{' '}
        {t('forecast.conditionBannerTail')}
      </p>

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
            <p className={styles.whyDisclaimer}>{t('forecast.whyDisclaimer')}</p>
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
        <p className={styles.muted}>{t('forecast.noScoredFolds')}</p>
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
          <GlossaryTerm termKey="edge_strength">{EDGE_LABEL[block.edge_strength]}</GlossaryTerm>
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

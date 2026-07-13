/**
 * Structural-trend badge (Plan 0092 phase 6, ADR-0084).
 *
 * Shows the price-action `structural_trend` (up / down / range) as a distinct,
 * separately-labeled read — the ADR-0084 posture that this second trend lens sits
 * *beside* the indicator trend and may legitimately differ from it. Beneath the
 * trend it lists the structure terms currently present (HH/HL/LH/LL, BOS/CHoCH),
 * each a `<GlossaryTerm>` so hovering explains it. Renders nothing when the bars
 * carry no confirmed structure yet.
 */
import { GlossaryTerm } from './GlossaryTerm'
import { t } from '../lib/i18n'
import type {
  MarketStructureResult,
  StructureEventKind,
  StructureLabel,
} from '../lib/marketStructure'
import styles from './MarketStructureBadge.module.css'

interface Props {
  structure: MarketStructureResult
}

const TREND_GLYPH: Record<MarketStructureResult['structuralTrend'], string> = {
  up: '↑',
  down: '↓',
  range: '↔',
}
const LABELS: readonly StructureLabel[] = ['HH', 'HL', 'LH', 'LL']
const EVENTS: readonly StructureEventKind[] = ['BOS', 'CHoCH']

export function MarketStructureBadge({ structure }: Props): JSX.Element | null {
  if (structure.labeledPivots.length === 0 && structure.events.length === 0) return null

  const presentLabels = new Set(structure.labeledPivots.map((lp) => lp.label))
  const presentEvents = new Set(structure.events.map((e) => e.kind))
  const trend = structure.structuralTrend

  return (
    <div className={styles.badge} data-testid="market-structure-badge">
      <span className={styles.title}>{t('chart.structure.label')}</span>
      <span className={`${styles.trend} ${styles[trend]}`}>
        {TREND_GLYPH[trend]} {t(`chart.structure.trend.${trend}`)}
      </span>
      <span className={styles.terms}>
        {LABELS.filter((l) => presentLabels.has(l)).map((l) => (
          <GlossaryTerm key={l} termKey={l.toLowerCase()} className={styles.term}>
            {l}
          </GlossaryTerm>
        ))}
        {EVENTS.filter((e) => presentEvents.has(e)).map((e) => (
          <GlossaryTerm key={e} termKey={e === 'CHoCH' ? 'choch' : 'bos'} className={styles.term}>
            {e}
          </GlossaryTerm>
        ))}
      </span>
    </div>
  )
}

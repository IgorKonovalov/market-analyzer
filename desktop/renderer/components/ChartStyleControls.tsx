/**
 * Chart-style controls (Plan 0068 phase 3, ADR-0062). A per-element colour +
 * (for line elements) line-width editor for the **currently-active theme**, plus
 * a global "Reset chart style". Writes go through the `chartStyle` store, so a
 * mounted chart reacts live via its phase-2 subscription — no props, no callbacks.
 *
 * Per-theme by design (a green legible on dark isn't on light): the controls edit
 * whichever theme the chart is currently showing (the effective theme), with a
 * clear "Editing <Light|Dark>" label. Switch the theme in Appearance to edit the
 * other set. Default colours come from the `:root` theme tokens (read off
 * `document.documentElement`), so an unset element shows its current drawn colour.
 */
import { useSyncExternalStore } from 'react'

import {
  CHART_STYLE_ELEMENTS,
  MAX_LINE_WIDTH,
  MIN_LINE_WIDTH,
  getCandleType,
  getChartStyleOverrides,
  isLineElement,
  resetChartStyle,
  resolveChartStyle,
  setCandleType,
  setElementOverride,
  subscribeChartStyle,
  type CandleSeriesType,
  type ChartStyleElement,
} from '../lib/chartStyle'
import {
  getStoredTheme,
  resolveEffective,
  subscribeEffective,
  type EffectiveTheme,
} from '../lib/theme'
import { t } from '../lib/i18n'
import styles from './ChartStyleControls.module.css'

const ELEMENT_LABELS: Record<ChartStyleElement, string> = {
  candleUp: 'Candle up',
  candleDown: 'Candle down',
  volume: 'Volume',
  volumeMa: 'Volume MA',
  vwap: 'VWAP',
  obv: 'OBV',
  ema: 'EMA',
  sma: 'SMA',
  markerBullish: 'Bullish marker',
  markerBearish: 'Bearish marker',
  markerNeutral: 'Neutral marker',
}

const WIDTH_OPTIONS: readonly number[] = Array.from(
  { length: MAX_LINE_WIDTH - MIN_LINE_WIDTH + 1 },
  (_, i) => MIN_LINE_WIDTH + i,
)

const CANDLE_TYPE_OPTIONS: ReadonlyArray<{ value: CandleSeriesType; label: string }> = [
  { value: 'candles', label: 'Candles' },
  { value: 'bars', label: 'OHLC bars' },
  { value: 'line', label: 'Line' },
  { value: 'area', label: 'Area' },
]

/** Line/area render as a single line, so the candle up/down colour controls are
 * inert (there is no up vs down). The stored `candleUp` colour still drives the
 * single line colour, so it's set from Candles/OHLC mode. */
function isSingleLineType(type: CandleSeriesType): boolean {
  return type === 'line' || type === 'area'
}

/** The effective theme (light/dark) the chart is currently showing — the set the
 * controls edit. Re-renders on an explicit theme change or an OS flip in system
 * mode (the same subscription the chart uses). */
function useEffectiveTheme(): EffectiveTheme {
  return useSyncExternalStore(
    subscribeEffective,
    () => resolveEffective(getStoredTheme()),
    () => 'light',
  )
}

/** Subscribe to store mutations so the controls re-read the resolved values after
 * any override / reset. The snapshot is the overrides object (a fresh reference
 * per mutation, stable otherwise), so this drives a re-render only on a change. */
function useChartStyleOverrides(): ReturnType<typeof getChartStyleOverrides> {
  return useSyncExternalStore(subscribeChartStyle, getChartStyleOverrides, getChartStyleOverrides)
}

export function ChartStyleControls(): JSX.Element {
  const theme = useEffectiveTheme()
  useChartStyleOverrides() // re-render when an override, reset, or candle-type lands
  const themeName = theme === 'dark' ? 'Dark' : 'Light'
  const candleType = getCandleType()
  const singleLine = isSingleLineType(candleType)
  // Resolve current display values (defaults ⊕ overrides) for the active theme off
  // the live :root tokens. Re-runs each render; the two subscriptions above make
  // sure a theme flip or an override triggers one.
  const resolved = resolveChartStyle(document.documentElement, theme)

  return (
    <div className={styles.root}>
      <div className={styles.field}>
        <span className={styles.fieldLabel} id="candle-type-label">
          {t('chartStyle.candleTypeLabel')}
        </span>
        <div
          className={styles.segmented}
          role="radiogroup"
          aria-labelledby="candle-type-label"
          data-testid="candle-type-control"
        >
          {CANDLE_TYPE_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={styles.segment}
              data-active={candleType === opt.value}
              data-testid={`candle-type-option-${opt.value}`}
            >
              <input
                type="radio"
                name="candle-type"
                className={styles.segmentInput}
                value={opt.value}
                checked={candleType === opt.value}
                onChange={() => setCandleType(opt.value)}
              />
              {opt.label}
            </label>
          ))}
        </div>
        {singleLine && (
          <p className={styles.note} data-testid="candle-type-note">
            {t('chartStyle.lineAreaNotePre')}
            <strong>{t('chartStyle.candleUp')}</strong>
            {t('chartStyle.lineAreaNotePost')}
          </p>
        )}
      </div>

      <p className={styles.editing} aria-live="polite" data-testid="chart-style-editing-theme">
        {t('chartStyle.editingPre')}
        <strong>{themeName}</strong>
        {t('chartStyle.editingPost')}
      </p>
      <div className={styles.grid}>
        {CHART_STYLE_ELEMENTS.map((element) => {
          const color = resolved.colors[element]
          const colorId = `chart-style-${element}-color`
          const widthId = `chart-style-${element}-width`
          // Candle up/down are inert for line/area (no up vs down). Disabled, not
          // hidden, so the roster stays stable; the note above explains why.
          const colorDisabled = singleLine && (element === 'candleUp' || element === 'candleDown')
          return (
            <div key={element} className={styles.row}>
              <label className={styles.label} htmlFor={colorId}>
                {ELEMENT_LABELS[element]}
              </label>
              <div className={styles.controls}>
                <input
                  id={colorId}
                  type="color"
                  className={styles.colorInput}
                  value={color}
                  disabled={colorDisabled}
                  onChange={(e) => setElementOverride(theme, element, { color: e.target.value })}
                  data-testid={`chart-style-color-${element}`}
                />
                <span className={styles.hex} aria-hidden="true">
                  {color}
                </span>
                {isLineElement(element) && (
                  <span className={styles.widthField}>
                    <label className={styles.widthLabel} htmlFor={widthId}>
                      {t('chartStyle.widthLabel')}
                    </label>
                    <select
                      id={widthId}
                      className={styles.widthSelect}
                      value={resolved.widths[element]}
                      onChange={(e) =>
                        setElementOverride(theme, element, { lineWidth: Number(e.target.value) })
                      }
                      data-testid={`chart-style-width-${element}`}
                    >
                      {WIDTH_OPTIONS.map((w) => (
                        <option key={w} value={w}>
                          {w}
                        </option>
                      ))}
                    </select>
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
      <button
        type="button"
        className={styles.reset}
        onClick={() => resetChartStyle()}
        data-testid="chart-style-reset"
      >
        {t('chartStyle.resetButton')}
      </button>
    </div>
  )
}

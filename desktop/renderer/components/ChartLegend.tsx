/**
 * Inline chart legend (Plan 0096 phase 2) — the primary layer control.
 *
 * A top-left on-chart panel that lists every active layer (indicator overlays,
 * the OBV strip, candlestick-marker groups, price lines, trendline groups) with
 * its colour swatch, label, and live last-bar value, plus a hover row of actions:
 * hide/show (the swatch button toggles visibility), a style gear that expands a
 * compact colour/width editor inline for styleable series, and — on user-owned
 * overlays only (ADR-0077 provenance) — a remove control. The relocated
 * `+ Indicator` add-form (from the LayersPanel) lives in the header.
 *
 * Purely presentational + renderer-owned: it calls back into the chart's
 * existing toggle / add / remove / highlight paths and writes chart-style
 * overrides through the `chartStyle` store. No sidecar call, no wire.
 */
import { useState, useSyncExternalStore } from 'react'

import { AddOverlayForm } from './AddOverlayForm'
import { GlossaryTerm } from './GlossaryTerm'
import type { ChartLayer } from './LayersPanel'
import { CLEAN_PRESET_NAME, type ChartPreset } from '../lib/chartPresets'
import { OBV_LAYER_ID } from '../lib/chartSeries'
import {
  MAX_LINE_WIDTH,
  MIN_LINE_WIDTH,
  getChartStyleOverrides,
  isLineElement,
  resolveChartStyle,
  setElementOverride,
  subscribeChartStyle,
  type ChartStyleElement,
} from '../lib/chartStyle'
import {
  getStoredTheme,
  resolveEffective,
  subscribeEffective,
  type EffectiveTheme,
} from '../lib/theme'
import { t } from '../lib/i18n'
import type { OverlaySpec } from '../types/events'
import styles from './ChartLegend.module.css'

export interface ChartLegendProps {
  layers: ChartLayer[]
  /** Live last-bar value per layer id (indicator overlays + OBV). */
  values: ReadonlyMap<string, string>
  onToggle: (id: string) => void
  /** Hover-to-highlight (Plan 0067 phase 3): fired with a group's `highlightKey`
   * on enter, `null` on leave. Rows without a `highlightKey` never call it. */
  onHighlight?: (key: string | null) => void
  /** Add a user overlay (ADR-0077). Present only when the chart carries a
   * (symbol, timeframe) — enables the `+ Indicator` header form. */
  onAddOverlay?: (spec: OverlaySpec) => void
  /** Remove a user overlay by layer id (wired to the remove control on
   * `removable` rows only; agent rows are hide-only). */
  onRemove?: (id: string) => void
  /** Available presets (built-ins + user-saved), in selector order (Plan 0096
   * phase 3). */
  presets?: ChartPreset[]
  /** The applied preset name, or `null` when the layout has diverged (Custom). */
  activePreset?: string | null
  /** Apply a preset into the current (symbol, timeframe). Present only when the
   * chart is keyed; absent ⇒ the preset selector is hidden. */
  onApplyPreset?: (preset: ChartPreset) => void
  /** Save the current layout as a named preset. */
  onSavePreset?: (name: string) => void
}

/** Localised display name for a built-in preset; user presets show verbatim. */
function presetDisplayName(preset: ChartPreset): string {
  if (!preset.builtIn) return preset.name
  switch (preset.name) {
    case CLEAN_PRESET_NAME:
      return t('chartLegend.preset.clean')
    case 'Trend':
      return t('chartLegend.preset.trend')
    case 'Mean-reversion':
      return t('chartLegend.preset.meanReversion')
    case 'Patterns':
      return t('chartLegend.preset.patterns')
    default:
      return preset.name
  }
}

const WIDTH_OPTIONS: readonly number[] = Array.from(
  { length: MAX_LINE_WIDTH - MIN_LINE_WIDTH + 1 },
  (_, i) => MIN_LINE_WIDTH + i,
)

/** The chart-style element a legend row edits, or `null` when the layer has no
 * single styleable element (supertrend/bbands/ichimoku/markers/trendlines/
 * price-lines are omitted — they have no gear). */
function styleElementForLayer(layer: ChartLayer): ChartStyleElement | null {
  if (layer.id === OBV_LAYER_ID) return 'obv'
  if (layer.kind === 'overlay') {
    if (layer.glossaryKey === 'ema') return 'ema'
    if (layer.glossaryKey === 'sma') return 'sma'
    if (layer.glossaryKey === 'vwap') return 'vwap'
  }
  return null
}

/** The effective (light/dark) theme the chart is showing — the set the inline
 * style controls edit, mirroring `ChartStyleControls`. */
function useEffectiveTheme(): EffectiveTheme {
  return useSyncExternalStore(
    subscribeEffective,
    () => resolveEffective(getStoredTheme()),
    () => 'light',
  )
}

/** Compact inline colour (+ width) editor for one legend row's series. Writes
 * through the `chartStyle` store so the mounted chart reacts live (Plan 0068). */
function LegendRowSettings({ element }: { element: ChartStyleElement }): JSX.Element {
  const theme = useEffectiveTheme()
  // Re-read resolved values after any override / theme flip.
  useSyncExternalStore(subscribeChartStyle, getChartStyleOverrides, getChartStyleOverrides)
  const resolved = resolveChartStyle(document.documentElement, theme)
  const color = resolved.colors[element]
  return (
    <div className={styles.settings} data-testid={`legend-settings:${element}`}>
      <label className={styles.settingsField}>
        <span className={styles.settingsLabel}>{t('chartStyle.colorLabel')}</span>
        <input
          type="color"
          className={styles.colorInput}
          value={color}
          onChange={(e) => setElementOverride(theme, element, { color: e.target.value })}
          data-testid={`legend-color:${element}`}
        />
      </label>
      {isLineElement(element) && (
        <label className={styles.settingsField}>
          <span className={styles.settingsLabel}>{t('chartStyle.widthLabel')}</span>
          <select
            className={styles.widthSelect}
            value={resolved.widths[element]}
            onChange={(e) =>
              setElementOverride(theme, element, { lineWidth: Number(e.target.value) })
            }
            data-testid={`legend-width:${element}`}
          >
            {WIDTH_OPTIONS.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}

export function ChartLegend({
  layers,
  values,
  onToggle,
  onHighlight,
  onAddOverlay,
  onRemove,
  presets,
  activePreset = null,
  onApplyPreset,
  onSavePreset,
}: ChartLegendProps): JSX.Element | null {
  const [showAdd, setShowAdd] = useState(false)
  const [showSave, setShowSave] = useState(false)
  const [saveName, setSaveName] = useState('')
  // The row whose inline style editor is open (one at a time), or null.
  const [openSettings, setOpenSettings] = useState<string | null>(null)

  // Nothing to show and nothing to add ⇒ render nothing (Clean chart with no
  // overlays still lists the OBV row + the add control, so this is rare).
  if (layers.length === 0 && onAddOverlay === undefined) return null

  const submitSave = (e: React.FormEvent): void => {
    e.preventDefault()
    const name = saveName.trim()
    if (name === '') return
    onSavePreset?.(name)
    setSaveName('')
    setShowSave(false)
  }

  return (
    <div
      className={styles.legend}
      aria-label={t('chartLegend.ariaLabel')}
      data-testid="chart-legend"
    >
      {onApplyPreset !== undefined && presets !== undefined && (
        <div className={styles.presetBar}>
          <label className={styles.presetLabel} htmlFor="chart-preset-select">
            {t('chartLegend.presetLabel')}
          </label>
          <select
            id="chart-preset-select"
            className={styles.presetSelect}
            value={activePreset ?? ''}
            onChange={(e) => {
              const preset = presets.find((p) => p.name === e.target.value)
              if (preset) onApplyPreset(preset)
            }}
            data-testid="preset-select"
          >
            {activePreset === null && (
              <option value="" data-testid="preset-custom-option">
                {t('chartLegend.presetCustom')}
              </option>
            )}
            {presets.map((preset) => (
              <option key={preset.name} value={preset.name}>
                {presetDisplayName(preset)}
              </option>
            ))}
          </select>
          {onSavePreset !== undefined && (
            <button
              type="button"
              className={styles.presetSave}
              onClick={() => setShowSave((v) => !v)}
              aria-expanded={showSave}
              data-testid="preset-save-toggle"
            >
              {t('chartLegend.savePreset')}
            </button>
          )}
        </div>
      )}
      {showSave && onSavePreset !== undefined && (
        <form className={styles.saveForm} onSubmit={submitSave} data-testid="preset-save-form">
          <input
            className={styles.saveInput}
            type="text"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder={t('chartLegend.presetNamePlaceholder')}
            aria-label={t('chartLegend.presetNamePlaceholder')}
            data-testid="preset-name-input"
          />
          <button type="submit" className={styles.presetSave} data-testid="preset-save-submit">
            {t('chartLegend.savePreset')}
          </button>
        </form>
      )}
      {onAddOverlay !== undefined && (
        <div className={styles.header}>
          <button
            type="button"
            className={styles.addToggle}
            onClick={() => setShowAdd((v) => !v)}
            aria-expanded={showAdd}
            data-testid="legend-add-toggle"
          >
            {t('layers.addIndicator')}
          </button>
          {showAdd && <AddOverlayForm onAdd={onAddOverlay} />}
        </div>
      )}
      <ul className={styles.rows}>
        {layers.map((layer) => {
          const styleElement = styleElementForLayer(layer)
          const value = values.get(layer.id)
          const highlightable = layer.highlightKey !== undefined
          return (
            <li
              key={layer.id}
              className={styles.row}
              data-hidden={!layer.visible}
              data-testid={`legend-row:${layer.id}`}
              onMouseEnter={
                highlightable ? () => onHighlight?.(layer.highlightKey ?? null) : undefined
              }
              onMouseLeave={highlightable ? () => onHighlight?.(null) : undefined}
            >
              <div className={styles.rowMain}>
                <button
                  type="button"
                  className={styles.visToggle}
                  onClick={() => onToggle(layer.id)}
                  aria-pressed={layer.visible}
                  aria-label={t('layers.toggleAria', { layerName: layer.label })}
                  data-testid={`legend-toggle:${layer.id}`}
                >
                  <span
                    className={styles.swatch}
                    style={{ backgroundColor: layer.color }}
                    data-testid={`legend-swatch:${layer.id}`}
                    aria-hidden="true"
                  />
                </button>
                <span className={styles.label}>
                  {layer.glossaryKey ? (
                    <GlossaryTerm termKey={layer.glossaryKey}>{layer.label}</GlossaryTerm>
                  ) : (
                    layer.label
                  )}
                </span>
                {value !== undefined && (
                  <span className={styles.value} data-testid={`legend-value:${layer.id}`}>
                    {value}
                  </span>
                )}
                {layer.count !== undefined && (
                  <span className={styles.count} data-testid={`legend-count:${layer.id}`}>
                    {layer.count}
                  </span>
                )}
                <span className={styles.actions}>
                  {styleElement !== null && (
                    <button
                      type="button"
                      className={styles.actionButton}
                      onClick={() => setOpenSettings((cur) => (cur === layer.id ? null : layer.id))}
                      aria-expanded={openSettings === layer.id}
                      aria-label={t('chartLegend.settingsAria', { layerName: layer.label })}
                      data-testid={`legend-settings-toggle:${layer.id}`}
                    >
                      ⚙
                    </button>
                  )}
                  {layer.removable === true && onRemove !== undefined && (
                    <button
                      type="button"
                      className={styles.actionButton}
                      onClick={() => onRemove(layer.id)}
                      aria-label={t('layers.removeAria', { layerName: layer.label })}
                      data-testid={`legend-remove:${layer.id}`}
                    >
                      ×
                    </button>
                  )}
                </span>
              </div>
              {openSettings === layer.id && styleElement !== null && (
                <LegendRowSettings element={styleElement} />
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * Pure main-series + colour helpers for `CandlestickChart` (Plan 0072 phase 8
 * decomposition — lifted verbatim out of the component, no behaviour change).
 *
 * Covers: the four render modes of the main price series (candlestick / bar /
 * line / area — Plan 0068 phase 4), their creation / colour-application / data-
 * push, the flat `ChartColors` view several effects read, the two agent-overlay
 * style resolvers, and the always-on volume/OBV price-scale constants. All pure
 * (no React, no chart lifecycle) — the component owns creation order and refs.
 */
import { AreaSeries, BarSeries, CandlestickSeries, LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, LineWidth } from 'lightweight-charts'

import { toLightweightBar } from '../api/client'
import { overlayColorFor, overlayStyleElement } from './overlays'
import type { CandleSeriesType, ResolvedChartStyle } from './chartStyle'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

// Default width for an agent overlay line whose kind has no styleable width
// (i.e. not ema/sma) — matches the literal the chart used before Plan 0068.
export const DEFAULT_OVERLAY_LINE_WIDTH = 2 as LineWidth

// Always-on volume series (Plan 0027 phase 3), each derived client-side from
// `bars`. The histogram + its MA sit on their own bottom-band price scale on the
// price pane; VWAP rides the main price scale. OBV moved to its own REAL pane
// (Plan 0095 phase 2, v5 `addPane()`) — see the pane registry in `lib/panes.ts`.
// Volume stays a price-pane `scaleMargins` band (still supported in v5) because
// volume-under-price is the standard idiom.
export const PRICE_SCALE_ID = 'right' // the default price (candlestick) scale
export const VOLUME_SCALE_ID = 'volume'
// OBV's price scale — now a per-pane overlay scale on the OBV pane (Plan 0095 ph2),
// not a `scaleMargins` band on the price pane. Kept named so OBV stays a
// distinguishable always-on derived series rather than an agent overlay.
export const OBV_SCALE_ID = 'obv'
// Candles occupy the upper band of the price pane; volume hugs its bottom. With
// OBV now on its own pane, the price pane reclaims the space OBV used to band.
export const PRICE_SCALE_MARGINS = { top: 0.05, bottom: 0.2 }
export const VOLUME_SCALE_MARGINS = { top: 0.82, bottom: 0 }
// Height (px) of the OBV pane below the price pane (Plan 0095 phase 2).
export const OBV_PANE_HEIGHT = 110
// Stable id for the OBV pane in the `lib/panes.ts` registry.
export const OBV_PANE_ID = 'obv'
// Stable layers-legend id for the always-on OBV series (Plan 0076 phase 2). Unlike
// the agent overlays (`overlay:<kind>:<period>`) OBV is a standalone derived
// series, so it gets its own `series:` namespace; toggling this row hides the
// series in place (its pane is retained).
export const OBV_LAYER_ID = 'series:obv'

// Stable layers-legend id for the whole market-structure marker set (Plan 0092 /
// ADR-0084): HH/HL/LH/LL labels + BOS/CHoCH markers + the structural-trend badge,
// toggled as one layer. Off by default (Plan 0096 phase 3 — a Clean chart).
export const MARKET_STRUCTURE_LAYER_ID = 'structure'

export interface ChartColors {
  text: string
  border: string
  candleUp: string
  candleDown: string
  volume: string
  volumeMa: string
  vwap: string
  obv: string
  markerClicked: string
  markerBullish: string
  markerBearish: string
  markerNeutral: string
}

/** Flatten a resolved chart style into the flat colour view several effects read
 * (the styleable colours ⊕ the non-overridable chrome). Every drawn colour now
 * resolves through the chart-style store (Plan 0068 phase 2, ADR-0062): styles.css
 * theme tokens are the defaults, the user's per-theme overrides layer on top, and
 * lightweight-charts is handed fully-resolved strings (it can't resolve `var()`). */
export function chartColorsFrom(style: ResolvedChartStyle): ChartColors {
  return { ...style.colors, ...style.chrome }
}

/** An agent overlay line's resolved colour: ema/sma read their styleable entry
 * (honouring the user's override); any other kind keeps the registry colour. */
export function overlayStyleColor(spec: OverlaySpec, style: ResolvedChartStyle): string {
  const element = overlayStyleElement(spec)
  return element ? style.colors[element] : overlayColorFor(spec)
}

/** An agent overlay line's resolved width: ema/sma read their styleable width;
 * any other kind keeps the default overlay width. */
export function overlayStyleWidth(spec: OverlaySpec, style: ResolvedChartStyle): LineWidth {
  const element = overlayStyleElement(spec)
  return (element ? style.widths[element] : DEFAULT_OVERLAY_LINE_WIDTH) as LineWidth
}

// The main price series across the four render modes (Plan 0068 phase 4). A
// candle-type change rebuilds the chart (the series type is fixed at creation),
// so the whole creation effect re-runs with a fresh series of this type.
export type MainSeries = ISeriesApi<'Candlestick' | 'Bar' | 'Line' | 'Area'>

/** The `__test_chart_render__` kind reported for the main series of each type. */
export function mainSeriesKind(type: CandleSeriesType): string {
  switch (type) {
    case 'bars':
      return 'bar'
    case 'line':
      return 'line'
    case 'area':
      return 'area'
    case 'candles':
    default:
      return 'candlestick'
  }
}

/** Create the main price series for the chosen render type. Colours are applied
 * separately by `applyMainColors` so creation and the restyle effect share one
 * colour source. Line/area ride the main price scale (as candles do by default). */
export function createMainSeries(chart: IChartApi, type: CandleSeriesType): MainSeries {
  const common = { priceLineVisible: false, lastValueVisible: false }
  switch (type) {
    case 'bars':
      return chart.addSeries(BarSeries, {})
    case 'line':
      return chart.addSeries(LineSeries, { priceScaleId: PRICE_SCALE_ID, lineWidth: 2, ...common })
    case 'area':
      return chart.addSeries(AreaSeries, { priceScaleId: PRICE_SCALE_ID, lineWidth: 2, ...common })
    case 'candles':
    default:
      return chart.addSeries(CandlestickSeries, {})
  }
}

/** Apply the resolved colours to the main series for its render type. Candles/bars
 * take up/down (+ wick/border) colours; line/area have no up/down concept, so the
 * single line colour maps from `candleUp` (ADR-0062: the up/down/wick controls are
 * inert then, and the Settings UI disables them). */
export function applyMainColors(
  series: MainSeries,
  type: CandleSeriesType,
  colors: ChartColors,
): void {
  if (type === 'bars') {
    ;(series as ISeriesApi<'Bar'>).applyOptions({
      upColor: colors.candleUp,
      downColor: colors.candleDown,
    })
  } else if (type === 'line') {
    ;(series as ISeriesApi<'Line'>).applyOptions({ color: colors.candleUp })
  } else if (type === 'area') {
    ;(series as ISeriesApi<'Area'>).applyOptions({
      lineColor: colors.candleUp,
      topColor: colors.candleUp,
      bottomColor: 'transparent',
    })
  } else {
    ;(series as ISeriesApi<'Candlestick'>).applyOptions({
      upColor: colors.candleUp,
      downColor: colors.candleDown,
      wickUpColor: colors.candleUp,
      wickDownColor: colors.candleDown,
      borderUpColor: colors.candleUp,
      borderDownColor: colors.candleDown,
    })
  }
}

/** Push the bars onto the main series in the shape its render type expects:
 * OHLC for candles/bars, a single `value` (close) for line/area. */
export function setMainData(series: MainSeries, type: CandleSeriesType, bars: Bar[]): void {
  if (type === 'line' || type === 'area') {
    ;(series as ISeriesApi<'Line'>).setData(
      bars.map((b) => {
        const d = toLightweightBar(b)
        return { time: d.time, value: d.close }
      }),
    )
  } else {
    ;(series as ISeriesApi<'Candlestick'>).setData(bars.map(toLightweightBar))
  }
}

export interface OverlayEntry {
  spec: OverlaySpec
  series: ISeriesApi<'Line'>
}

export function overlayKey(spec: OverlaySpec): string {
  return `${spec.kind}:${spec.period ?? 'na'}`
}

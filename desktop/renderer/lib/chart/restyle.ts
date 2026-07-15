/**
 * applyRestyle — re-apply an EXISTING chart's colours + line widths in place on a
 * theme flip or a chart-style store mutation, via `applyOptions` (no remount).
 * Folded verbatim out of `useChartRestyle` (Plan 0098 phase 3, ADR-0092) — no
 * behaviour change. Colour AND width both flow here, so a colour or width override
 * lands in place on any mounted chart; idempotent with the creation values.
 *
 * The OBV series is owned by `useObvPane` (still external) and passed in; everything
 * else is the controller's own.
 */
import type { IChartApi, ISeriesApi, LineWidth } from 'lightweight-charts'

import {
  applyMainColors,
  chartColorsFrom,
  overlayStyleColor,
  overlayStyleWidth,
  type MainSeries,
  type OverlayEntry,
} from '../chartSeries'
import { resolveChartStyle } from '../chartStyle'
import type { CandleSeriesType } from '../chartStyle'
import type { EffectiveTheme } from '../theme'

type SupertrendPair = { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }

export interface RestyleParams {
  chart: IChartApi | null
  mainSeries: MainSeries | null
  container: HTMLDivElement | null
  candleType: CandleSeriesType
  theme: EffectiveTheme
  volumeSeries: ISeriesApi<'Histogram'> | null
  volumeMaSeries: ISeriesApi<'Line'> | null
  vwapSeries: ISeriesApi<'Line'> | null
  obvSeries: ISeriesApi<'Line'> | null
  overlaySeries: Map<string, OverlayEntry>
  supertrendSeries: Map<string, SupertrendPair>
}

export function applyRestyle({
  chart,
  mainSeries,
  container,
  candleType,
  theme,
  volumeSeries,
  volumeMaSeries,
  vwapSeries,
  obvSeries,
  overlaySeries,
  supertrendSeries,
}: RestyleParams): void {
  if (container === null || chart === null || mainSeries === null) return
  const style = resolveChartStyle(container, theme)
  const colors = chartColorsFrom(style)
  chart.applyOptions({
    layout: { textColor: colors.text },
    grid: {
      vertLines: { color: colors.border },
      horzLines: { color: colors.border },
    },
  })
  applyMainColors(mainSeries, candleType, colors)
  volumeSeries?.applyOptions({ color: colors.volume })
  volumeMaSeries?.applyOptions({
    color: colors.volumeMa,
    lineWidth: style.widths.volumeMa as LineWidth,
  })
  vwapSeries?.applyOptions({
    color: colors.vwap,
    lineWidth: style.widths.vwap as LineWidth,
  })
  obvSeries?.applyOptions({
    color: colors.obv,
    lineWidth: style.widths.obv as LineWidth,
  })
  for (const { spec, series } of overlaySeries.values()) {
    series.applyOptions({
      color: overlayStyleColor(spec, style),
      lineWidth: overlayStyleWidth(spec, style),
    })
  }
  // Supertrend's two masked series recolour from the bull/bear tokens in place.
  for (const { up, down } of supertrendSeries.values()) {
    up.applyOptions({ color: colors.markerBullish })
    down.applyOptions({ color: colors.markerBearish })
  }
}

import { buildChartLayers } from './layersLegend'
import { chartColorsFrom } from './chartSeries'
import { resolveChartStyle } from './chartStyle'
import { readTrendlineColors } from './trendlines'
import type { CandlestickPatternGroup } from './candleGroups'
import type { OverlaySpec, TrendlineSpec } from '../types/events'

const div = document.createElement('div')
const style = resolveChartStyle(div, 'light')
const colors = chartColorsFrom(style)
const trendlineColors = readTrendlineColors(div)

function build(overrides: Partial<Parameters<typeof buildChartLayers>[0]>) {
  return buildChartLayers({
    overlays: [],
    candleGroups: [],
    enabledCandleGroups: new Set(),
    visibleTrendlines: [],
    hidden: new Set(),
    style,
    colors,
    trendlineColors,
    ...overrides,
  })
}

const EMA: OverlaySpec = { kind: 'ema', period: 20 } as OverlaySpec
const R1: OverlaySpec = {
  kind: 'price_line',
  price: 100,
  label: 'R1',
  role: 'resistance',
} as OverlaySpec
const HAMMER_GROUP: CandlestickPatternGroup = {
  key: 'hammer|bullish_marker',
  pattern: 'hammer',
  kind: 'bullish_marker',
  count: 3,
  latestTs: '2026-04-10T00:00:00+00:00',
}

describe('buildChartLayers', () => {
  it('emits an overlay row, a candlestick master + group row, and a price-line row in order', () => {
    const rows = build({
      overlays: [EMA, R1],
      candleGroups: [HAMMER_GROUP],
      enabledCandleGroups: new Set(['hammer|bullish_marker']),
    })
    const ids = rows.map((r) => r.id)
    expect(ids).toEqual([
      'overlay:ema:20',
      'candles-master',
      'candles:hammer|bullish_marker',
      'pline:R1',
    ])
    // The group row carries its instance count + highlight key + enabled visibility.
    const group = rows.find((r) => r.id === 'candles:hammer|bullish_marker')
    expect(group).toMatchObject({ count: 3, highlightKey: 'hammer|bullish_marker', visible: true })
  })

  it('marks a row hidden when its id is in the hidden set', () => {
    const rows = build({ overlays: [EMA], hidden: new Set(['overlay:ema:20']) })
    expect(rows.find((r) => r.id === 'overlay:ema:20')?.visible).toBe(false)
  })

  it('groups trendlines by (pattern, state) with a count', () => {
    const t = (pattern: string): TrendlineSpec =>
      ({ pattern, style: 'solid', x1: 0, y1: 0, x2: 1, y2: 1 }) as unknown as TrendlineSpec
    const rows = build({ visibleTrendlines: [t('head_shoulders'), t('head_shoulders')] })
    const trend = rows.filter((r) => r.kind === 'trendline')
    expect(trend).toHaveLength(1)
    expect(trend[0].count).toBe(2)
  })

  it('omits the candlestick master when there are no marker groups', () => {
    const rows = build({ overlays: [EMA] })
    expect(rows.some((r) => r.id === 'candles-master')).toBe(false)
  })
})

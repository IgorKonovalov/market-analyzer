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
    hasObv: false,
    hasMarketStructure: false,
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

  // Plan 0082 phase 4 (ADR-0077): an overlay whose overlayKey is in the user
  // layer is removable; an agent overlay (absent from that set) is hide-only.
  it('marks a user-originated overlay row removable, an agent one not', () => {
    const SMA: OverlaySpec = { kind: 'sma', period: 50 } as OverlaySpec
    const rows = build({ overlays: [EMA, SMA], userOverlayKeys: new Set(['ema:20']) })
    expect(rows.find((r) => r.id === 'overlay:ema:20')?.removable).toBe(true)
    expect(rows.find((r) => r.id === 'overlay:sma:50')?.removable).toBe(false)
  })

  it('defaults every overlay to non-removable when no user keys are supplied', () => {
    const rows = build({ overlays: [EMA] })
    expect(rows.find((r) => r.id === 'overlay:ema:20')?.removable).toBe(false)
  })

  // Plan 0076 phase 2: the always-on OBV strip gets a single toggleable row.
  it('emits an OBV row (label, obv colour, visible) when hasObv, after the overlays', () => {
    const rows = build({ overlays: [EMA], hasObv: true })
    const obv = rows.find((r) => r.id === 'series:obv')
    expect(obv).toMatchObject({ label: 'OBV', color: colors.obv, kind: 'series', visible: true })
    // Ordered right after the indicator overlays.
    expect(rows.map((r) => r.id)).toEqual(['overlay:ema:20', 'series:obv'])
    // OBV is a standalone derived series — no glossary key yet.
    expect(obv?.glossaryKey).toBeUndefined()
  })

  it('omits the OBV row when hasObv is false', () => {
    expect(build({ hasObv: false }).some((r) => r.id === 'series:obv')).toBe(false)
  })

  it('marks the OBV row hidden when its id is in the hidden set', () => {
    const rows = build({ hasObv: true, hidden: new Set(['series:obv']) })
    expect(rows.find((r) => r.id === 'series:obv')?.visible).toBe(false)
  })

  // Plan 0096: market structure is one toggleable row, off by default (hidden).
  it('emits a Market structure row when hasMarketStructure, hidden by default', () => {
    const rows = build({ hasMarketStructure: true, hidden: new Set(['structure']) })
    const structure = rows.find((r) => r.id === 'structure')
    expect(structure).toMatchObject({ kind: 'series', visible: false })
  })

  it('omits the Market structure row when hasMarketStructure is false', () => {
    expect(build({ hasMarketStructure: false }).some((r) => r.id === 'structure')).toBe(false)
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

  // Plan 0085: the group's pattern token keys the glossary tooltip, so the legend
  // row discloses the pattern's explanation via <GlossaryTerm>.
  it('keys a candlestick group row to the glossary by its pattern token', () => {
    const rows = build({
      candleGroups: [HAMMER_GROUP],
      enabledCandleGroups: new Set(['hammer|bullish_marker']),
    })
    expect(rows.find((r) => r.id === 'candles:hammer|bullish_marker')?.glossaryKey).toBe('hammer')
  })

  it('sets no glossary key on a patternless (agent-highlight) candlestick group', () => {
    const nullGroup: CandlestickPatternGroup = {
      key: 'null|bullish_marker',
      pattern: null,
      kind: 'bullish_marker',
      count: 1,
      latestTs: '2026-04-10T00:00:00+00:00',
    }
    const rows = build({
      candleGroups: [nullGroup],
      enabledCandleGroups: new Set(['null|bullish_marker']),
    })
    expect(rows.find((r) => r.id === 'candles:null|bullish_marker')?.glossaryKey).toBeUndefined()
  })
})

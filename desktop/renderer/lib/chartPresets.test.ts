/**
 * Plan 0096 phase 3: chart presets (ADR-0089).
 *
 * Pins the four built-ins, that hiddenForPreset resolves each built-in's
 * overlay+visibility intent over a representative layer set, and that
 * saveCurrentAsPreset round-trips a user preset through localStorage while
 * rejecting a built-in-name collision.
 */
import {
  BUILT_IN_PRESETS,
  CLEAN_PRESET_NAME,
  allPresets,
  getUserPresetsSnapshot,
  hiddenForPreset,
  saveCurrentAsPreset,
} from './chartPresets'
import { OBV_LAYER_ID } from './chartSeries'
import type { ChartLayer } from '../components/LayersPanel'

const LAYERS: ChartLayer[] = [
  { id: 'overlay:ema:20', label: 'EMA 20', color: '#000', kind: 'overlay', visible: true },
  { id: 'series:obv', label: 'OBV', color: '#000', kind: 'series', visible: true },
  { id: 'candles-master', label: 'Candles', color: '#000', kind: 'marker', visible: true },
  {
    id: 'trendlines:tri|solid',
    label: 'Triangle',
    color: '#000',
    kind: 'trendline',
    visible: true,
    highlightKey: 'tri|solid',
  },
  { id: 'pline:R1', label: 'R1', color: '#000', kind: 'price_line', visible: true },
]

const preset = (name: string) => BUILT_IN_PRESETS.find((p) => p.name === name)!

afterEach(() => {
  try {
    window.localStorage.clear()
  } catch {
    /* ignore */
  }
})

it('ships four built-ins with Clean first', () => {
  expect(BUILT_IN_PRESETS.map((p) => p.name)).toEqual([
    'Clean',
    'Trend',
    'Mean-reversion',
    'Patterns',
  ])
  expect(BUILT_IN_PRESETS[0].name).toBe(CLEAN_PRESET_NAME)
})

it('Clean hides every non-base layer', () => {
  const hidden = hiddenForPreset(preset('Clean'), LAYERS)
  for (const layer of LAYERS) expect(hidden.has(layer.id)).toBe(true)
})

it('Trend keeps its declared overlays + price lines and hides OBV/candles/trendlines', () => {
  // Trend declares ema 20 among its overlays, matching the layer id present here.
  const hidden = hiddenForPreset(preset('Trend'), LAYERS)
  expect(hidden.has('overlay:ema:20')).toBe(false)
  expect(hidden.has(OBV_LAYER_ID)).toBe(true)
  expect(hidden.has('candles-master')).toBe(true)
  expect(hidden.has('trendlines:tri|solid')).toBe(true)
  expect(hidden.has('pline:R1')).toBe(false)
})

it('Patterns shows candlesticks/trendlines/price lines and hides OBV + overlays', () => {
  const hidden = hiddenForPreset(preset('Patterns'), LAYERS)
  expect(hidden.has('candles-master')).toBe(false)
  expect(hidden.has('trendlines:tri|solid')).toBe(false)
  expect(hidden.has('pline:R1')).toBe(false)
  expect(hidden.has(OBV_LAYER_ID)).toBe(true)
  expect(hidden.has('overlay:ema:20')).toBe(true)
})

it('saveCurrentAsPreset round-trips through localStorage and lists after the built-ins', () => {
  saveCurrentAsPreset('My layout', [{ kind: 'ema', period: 9 }], {
    obv: true,
    candlesticks: false,
    trendlines: false,
    priceLines: true,
  })
  const snap = getUserPresetsSnapshot()
  expect(snap.map((p) => p.name)).toContain('My layout')
  const all = allPresets(snap)
  expect(all[all.length - 1].name).toBe('My layout')
  const raw = JSON.parse(window.localStorage.getItem('ma.chartPresets') ?? '[]') as unknown[]
  expect(raw[0]).toMatchObject({
    name: 'My layout',
    overlays: [{ kind: 'ema', period: 9 }],
    show: { obv: true, priceLines: true },
  })
})

it('rejects a user preset whose name collides with a built-in', () => {
  saveCurrentAsPreset('Clean', [], {
    obv: false,
    candlesticks: false,
    trendlines: false,
    priceLines: false,
  })
  expect(getUserPresetsSnapshot().some((p) => p.name === 'Clean')).toBe(false)
})

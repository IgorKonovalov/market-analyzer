/**
 * Per-pane label (Plan 0105 phase 4, ADR-0100 rule 1).
 *
 * v5.2.0 has no pane-title API (`IPaneApi` exposes `setHeight` /
 * `attachPrimitive` / `getHTMLElement` but no `title`), so each managed
 * sub-pane's name is drawn by us: a small text primitive attached to the pane's
 * PRIMARY series, so it lives and dies with the pane — the ADR-0100 risk-note
 * preference over a `getHTMLElement()` overlay, which would have to re-attach
 * and re-position across pane resize and the candle-type chart rebuild. The
 * label text derives from the indicator's client-side identity (never fetched).
 *
 * The text colour is a fixed neutral grey legible on both themes (the pane
 * hooks deliberately don't re-run on a theme flip — their series hues are
 * static per ADR-0062 — so a theme-resolved label colour would go stale).
 */
import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  Time,
} from 'lightweight-charts'

import type { OverlayKind } from '../types/events'

/** Short display name per managed sub-pane: the oscillator / money-flow kinds
 * (`useOscillatorPanes`) plus the OBV pane (`useObvPane`). An unknown kind
 * humanises to its upper-cased token so a future pane still gets a label. */
const PANE_LABELS: Partial<Record<OverlayKind | 'obv', string>> = {
  obv: 'OBV',
  stochastic: 'Stochastic',
  stoch_rsi: 'Stoch RSI',
  cci: 'CCI',
  williams_r: 'Williams %R',
  roc: 'ROC',
  mfi: 'MFI',
  cmf: 'CMF',
  ad_line: 'A/D line',
  rsi: 'RSI',
  macd: 'MACD hist',
}

/** The label a managed sub-pane shows for its kind. */
export function paneLabelFor(kind: OverlayKind | 'obv'): string {
  return PANE_LABELS[kind] ?? kind.toUpperCase()
}

// Neutral grey that stays legible on both the light and dark chart backgrounds.
const PANE_LABEL_COLOR = '#787b86'
const PANE_LABEL_FONT = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
const PANE_LABEL_X = 8
const PANE_LABEL_Y = 4

// The minimal slice of the canvas target we use (same local-typing rationale as
// spans.ts / trendlines.ts / divergences.ts).
interface MediaCoordinateScope {
  context: CanvasRenderingContext2D
}
interface PaneLabelDrawTarget {
  useMediaCoordinateSpace(callback: (scope: MediaCoordinateScope) => void): void
}

class PaneLabelRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly text: string) {}

  draw(target: PaneLabelDrawTarget): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context
      ctx.save()
      ctx.font = PANE_LABEL_FONT
      ctx.fillStyle = PANE_LABEL_COLOR
      ctx.textAlign = 'left'
      ctx.textBaseline = 'top'
      ctx.fillText(this.text, PANE_LABEL_X, PANE_LABEL_Y)
      ctx.restore()
    })
  }
}

class PaneLabelView implements IPrimitivePaneView {
  constructor(private readonly text: string) {}

  // Above the series line so the name stays readable when the line crosses the
  // pane's top-left corner.
  zOrder(): PrimitivePaneViewZOrder {
    return 'top'
  }

  renderer(): IPrimitivePaneRenderer {
    return new PaneLabelRenderer(this.text)
  }
}

/**
 * The persistent pane-name primitive. Attach to the pane's primary series at
 * creation (`series.attachPrimitive(new PaneLabelPrimitive(paneLabelFor(kind)))`)
 * — disposal rides the series (pane removal / `chart.remove()`), so labels
 * survive pan/zoom and the candle-type rebuild for free.
 */
export class PaneLabelPrimitive implements ISeriesPrimitive<Time> {
  private readonly views: PaneLabelView[]

  constructor(readonly text: string) {
    this.views = [new PaneLabelView(text)]
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views
  }
}

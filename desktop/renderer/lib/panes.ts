/**
 * Stable-id → pane registry over lightweight-charts v5 panes (Plan 0095 phase 2).
 *
 * v5 replaced the v4 `scaleMargins`-band fake-pane trick with a real panes API
 * (`chart.addPane()` / `chart.removePane(index)` / `chart.panes()`), but panes are
 * addressed by positional index. Higher layers (the Plan 0091 oscillator /
 * money-flow panes) want to create, reuse, and tear down a *named* pane without
 * tracking indices themselves — and v5 reindexes the remaining panes when one is
 * removed. This registry owns that bookkeeping.
 *
 * Managed panes live contiguously starting at `basePane` (default 1, i.e. just
 * below the price pane at index 0). Callers place series on a pane via v5's
 * `chart.addSeries(SeriesDefinition, options, paneIndex)` using the index returned
 * from `ensure`, and size a pane via `pane(id)?.setHeight(px)`.
 *
 * Assumes every pane at or after `basePane` is registry-managed (no external pane
 * manipulation), which holds for this app: pane 0 is the price pane, everything
 * below it is a registry pane.
 */
import type { IChartApi, IPaneApi, Time } from 'lightweight-charts'

export class PaneRegistry {
  /** Registered ids in pane order; index `i` maps to chart pane `basePane + i`. */
  private readonly order: string[] = []

  constructor(
    private readonly chart: IChartApi,
    private readonly basePane = 1,
  ) {}

  /** Create the pane for `id` if absent (reuse if present); return its pane index. */
  ensure(id: string): number {
    const at = this.order.indexOf(id)
    if (at !== -1) return this.basePane + at
    this.chart.addPane()
    this.order.push(id)
    return this.basePane + this.order.length - 1
  }

  /** Remove the pane for `id` (no-op if absent). v5 reindexes the panes below it;
   * the registry re-maps the remaining ids to their new indices. */
  remove(id: string): void {
    const at = this.order.indexOf(id)
    if (at === -1) return
    this.chart.removePane(this.basePane + at)
    this.order.splice(at, 1)
  }

  has(id: string): boolean {
    return this.order.includes(id)
  }

  /** The pane index for `id`, or null if not registered. */
  paneIndex(id: string): number | null {
    const at = this.order.indexOf(id)
    return at === -1 ? null : this.basePane + at
  }

  /** The `IPaneApi` for `id` (for `setHeight`/`getHeight`), or null if absent. */
  pane(id: string): IPaneApi<Time> | null {
    const idx = this.paneIndex(id)
    if (idx === null) return null
    return this.chart.panes()[idx] ?? null
  }
}

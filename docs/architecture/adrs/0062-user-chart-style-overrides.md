# ADR-0062 — User chart-style overrides: a unified typed style store over the theme tokens

> **Status:** proposed (accepts at Plan 0068's close)
> **Date:** 2026-07-08
> **Related plan(s):** [0068-chart-style-settings](../plans/0068-chart-style-settings.md)
> **Relates to:** [ADR-0039](0039-renderer-theming-localstorage.md) (renderer-owned presentation prefs in localStorage — this extends it), [ADR-0006](0006-persistence-layout.md) (config.json — this carves another presentation pref out of it), [ADR-0049](0049-chart-trendline-overlay-primitive.md) / [ADR-0061](0061-trendline-pattern-identity-and-colour.md) (trendline colour — deliberately out of scope here)

## Context

Every colour the candlestick chart draws is a CSS theme token (`--chart-up`, `--marker-bullish`, `--overlay-vwap`, …), defined per theme (light/dark) in `renderer/styles.css` and read off the DOM at runtime because lightweight-charts hands strings straight to canvas and cannot resolve `var()` (`readChartColors` / `overlaySeriesColor` in `CandlestickChart.tsx`). Line widths are hard-coded literals (`lineWidth: 1`/`2`); the series render type is fixed (`addCandlestickSeries`). There is **no per-user override layer** — a user cannot recolour a line, thicken it, or switch to OHLC bars.

Adding user styling forces three decisions that could each go either way:

1. **Where do the preferences live?** ADR-0006 puts user config in the sidecar's `config.json`. But ADR-0039 already carved presentation prefs (theme) out to renderer `localStorage`, precisely because they are pure-presentation, must apply without a sidecar round-trip, and must survive the sidecar being down or mid-attach (ADR-0016). Chart style is the same category.
2. **How do overrides reach the canvas?** The chart resolves colours from DOM tokens today. Overrides could be injected as inline CSS custom properties (reusing that read path) with a separate channel for the two non-token properties (width, series-type) — or every property could flow through one typed object the chart reads directly.
3. **What granularity, and does a colour depend on the theme?** A green tuned for a dark background is not the green for a light one; and the agent pushes overlay *instances* (EMA-50, EMA-200) that do not persist across sessions.

These constraints — pre-existing per-theme token palette, canvas that can't read `var()`, two properties that aren't colours at all, ephemeral overlay instances — are what make this a decision rather than a default.

## Decision

We will persist chart-style overrides in renderer **`localStorage` under the key `ma.chartStyle`** (extending ADR-0039's convention; **no** `config.json`, route, or IPC), and resolve them through a **single typed style store** (`renderer/lib/chartStyle.ts`) that mirrors `theme.ts`'s get/set/subscribe shape. The store's `resolveChartStyle(container, effectiveTheme)` reads the theme's **default** palette from the DOM tokens (styles.css stays the default-palette source of truth), layers the user's **per-theme** overrides on top, fills line-width defaults, and returns a fully-resolved concrete `ResolvedChartStyle` object. `CandlestickChart` consumes that object everywhere it currently calls `readChartColors` / `overlaySeriesColor` and everywhere it hard-codes a `lineWidth`; it subscribes to the store and re-applies changes in place via `applyOptions`. Overrides are keyed by **element type**, not instance (all EMA lines share one colour). Three properties are user-settable: **colour** and **line width** (per theme, per element) and the **candle series-type** (`candles` / `bars` / `line` / `area`) as a single theme-independent chart mode. A candle series-type change **rebuilds the chart instance** (the series type is fixed at series creation in lightweight-charts).

Scope: the fixed built-in roster (candle up/down, volume + volume-MA, VWAP, OBV, bullish/bearish/neutral markers) plus the agent overlay line types (EMA, SMA). Supertrend, price-lines, and the pattern-span band derive from the bull/bear/neutral marker tokens, so overriding those marker colours recolours them for free. **Trendline pattern colours are out of scope** — Plan 0067 / ADR-0061 owns the automatic pattern-type palette; user override of it is not part of this decision.

## Consequences

### Positive
- **Consistent with the established presentation-pref home.** One convention (renderer `localStorage`) now covers theme *and* chart style; both work while the sidecar is down or attaching.
- **One source of truth for every drawn property.** Colour, width, and series-type all resolve through `resolveChartStyle` — a maintainer reads one function to know what the chart draws, instead of a token read here and a literal there.
- **styles.css stays the default palette.** The store holds only *overrides* + resolution; the per-theme default tokens (and Plan 0067's future pattern tokens) are untouched, so theme flips and the theme system keep working unchanged.
- **Ephemeral-safe.** Type-keyed overrides survive the agent re-pushing overlays; nothing depends on an instance that may not exist next session.
- **No new sidecar surface.** No route, IPC, Zod schema, or migration for a pure presentation concern.

### Negative
- **The color read path is rewritten, not wrapped.** `readChartColors` / `overlaySeriesColor` are replaced by the store's resolver; every colour consumer in `CandlestickChart` changes call site. Larger blast radius than injecting CSS-var overrides would have been — the deliberate cost of one uniform model (Plan 0068's chosen option B).
- **Presentation config splits further.** ADR-0039 already noted functional config (config.json) vs presentation prefs (localStorage); this deepens that split with a second `localStorage` key. A maintainer must know both homes exist.
- **Not portable.** Per-OS-profile, per-install; a style set does not follow the user to another machine or survive a profile wipe. Acceptable for a single-user desktop app; called out so a future sync/export need reopens it.
- **Candle series-type change is a full rebuild.** Switching to line/area recreates the chart instance and loses the current zoom/pan; primitives (span band, trendline) and markers must re-attach. Acceptable because the switch is rare and deliberate, but it is the one heavy operation and must be tested for primitive survival + no context leak.
- **Some controls are inert in line/area mode.** Line and area series have no up/down/wick colours, so those colour controls do nothing when a non-candle type is selected; the settings UI must annotate/disable them rather than silently ignore.

### Neutral
- With no overrides stored, `resolveChartStyle` returns exactly today's token-derived values and default widths, so the change is purely additive for a user who never opens the control.
- Line width is modelled per theme for uniformity even though width legibility rarely depends on the background; a user typically sets it once.

## Alternatives considered

### Alternative A — Persist in the sidecar's `config.json`
Store the style set as a config field, read/written over a new route through the typed client. Rejected for the same reasons ADR-0039 rejected it for theme: it couples a presentation concern to sidecar availability (no styling while attaching/offline), and adds a route + IPC + validation surface for pure CSS/canvas state. The persistence question was settled on the localStorage precedent.

### Alternative B — Inject CSS-variable overrides + a small store for width/series-type
Write colour overrides as inline custom properties scoped to the active theme so the existing `readChartColors` path picks them up unchanged (near-zero churn), and carry only the two non-token properties in a small typed store. Rejected in favour of one unified store: it splits the mental model (colours via the cascade, width/type via a store), and per-theme inline-var re-injection on every theme flip is its own moving part. The unified model was chosen deliberately for a single resolution path, accepting the larger colour-read-path rewrite.

### Alternative C — Per-instance overrides (this EMA-50, that EMA-200)
Let the user colour each drawn line individually. Rejected: overlays are agent-pushed and ephemeral (keyed by `kind:period`), so a per-instance override references geometry that may not exist next session — a fragile, ever-growing, mostly-dangling override set. Type-keyed overrides give the user the control they asked for ("recolour the EMAs") without pinning to transient instances.

### Alternative D — A single theme-independent colour per element
One chosen colour applies in both light and dark. Rejected: a colour legible on a dark background can be near-invisible on a light one (the exact reason the token palette is already per-theme); a single value would reintroduce the legibility problem the theme system exists to solve. Overrides are per theme.

## Notes

Paired with Plan 0068; flips to `accepted` at that plan's close ceremony if the store + localStorage approach holds. Deliberately disjoint from Plan 0067 / ADR-0061 (trendline pattern colour): different tokens, different rows — the two can land in either order.

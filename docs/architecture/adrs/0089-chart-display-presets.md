# ADR-0089 — Chart display presets (renderer-owned layer-composition container)

> **Status:** proposed (Plan 0096 accepts at close)
> **Date:** 2026-07-13
> **Related plan(s):** [0096-chart-and-app-declutter](../plans/0096-chart-and-app-declutter.md) (accepts this ADR at close)
> **Related ADRs:** [ADR-0077](0077-user-originated-display-overlays.md) (user-originated display overlays — the store presets compose, **refined here**), [ADR-0062](0062-user-chart-style-overrides.md) (user chart-style overrides — the base-series-form pref presets deliberately leave alone), [ADR-0039](0039-renderer-theming-localstorage.md) (renderer-owned display prefs in `ma.*` localStorage — the persistence convention reused), [ADR-0023](0023-technical-analysis-surface.md) (client-side indicator math — why this is display, not data), [ADR-0015](0015-claude-code-primary-control-surface.md) (agent is the primary control surface — untouched), [ADR-0088](0088-lightweight-charts-v5-panes.md) (v5 panes — the render substrate)

## Context

The chart opens with **everything on**: Supertrend, Bollinger, the five-line Ichimoku cloud, OBV, and every detected chart-pattern trendline and candlestick marker drawn simultaneously. The result is unreadable — the signal-to-ink ratio is underwater — and there is no one-action way to say "give me a clean chart." This ADR is the durable half of the [Plan 0096](../plans/0096-chart-and-app-declutter.md) declutter effort.

Three renderer-owned display stores already exist, each established by a prior ADR, and they do **not** currently compose:

1. **User indicator overlays** — [ADR-0077](0077-user-originated-display-overlays.md), persisted per-`(symbol, timeframe)` in `localStorage['ma.userOverlays']` and sticky across agent redraws.
2. **Chart style** — [ADR-0062](0062-user-chart-style-overrides.md), a global per-theme store of colours / line-widths / base-series `candleType` in `localStorage['ma.chartStyle']`.
3. **Layer visibility** — the show/hide state of each drawn layer, held today as an **ephemeral React `hidden` set inside `CandlestickChart` that is not persisted** and resets on every remount. So even "I turned OBV off" is forgotten the next time the chart mounts.

The user asked for **named presets — built-in plus save-your-own — with a clean default**. That is not a fourth ad-hoc toggle; it is a decision about (a) what a preset *is* made of, (b) how it relates to the already-sticky per-`(symbol, timeframe)` overlay store, and (c) what has to change for a preset to even be expressible — because "Clean = OBV off" is a *visibility* statement, and visibility is the one piece of display state that isn't persisted today.

The ownership question is **not** open: [ADR-0077](0077-user-originated-display-overlays.md) already recorded the load-bearing test — *client-computed + no sidecar call + no domain-state change ⇒ renderer-owned display preference*. A preset is pure composition of existing renderer-side state; it fetches nothing, calls no tool, changes no domain state. It sits squarely on the display-preference side of that line. What warrants an ADR is the **shape of the new container** and two genuine forks (global vs per-instrument; compose-existing-stores vs snapshot-drawn-state) that future maintainers will want to see reasoned through.

## Decision

We introduce a renderer-owned **chart-preset**: a named bundle of display state defined as `{ overlays: OverlaySpec[], visibility: Record<layerId, boolean> }`. Presets are **global** — a reusable *intent* ("Trend layout"), not keyed by symbol or timeframe. We ship four **built-in** presets as code constants — **Clean** (base candles + volume only), **Trend**, **Mean-reversion**, **Patterns** — and persist **user-saved** presets in `localStorage['ma.chartPresets']`, following the [ADR-0039](0039-renderer-theming-localstorage.md) `ma.*` convention (bounded/pruned like the ADR-0077 store).

Nuance that defines how presets behave:

- **Applied, not pinned.** Applying a preset **writes** its overlays + visibility into the *current* `(symbol, timeframe)`'s sticky state — the existing [ADR-0077](0077-user-originated-display-overlays.md) `ma.userOverlays` bucket plus the newly-persisted visibility layer. After applying, the user may tweak freely and normal stickiness remembers the result; the preset selector shows the applied name until the layout diverges, then reads **"Custom."** A preset is a starting point, not a live binding.
- **Visibility becomes persisted display state.** The ephemeral `hidden` set is promoted to a **persisted per-`(symbol, timeframe)` layer** (same species as overlays), because a preset must be able to say "OBV off" and have that survive a remount. This is the load-bearing new mechanism; presets sit on top of it.
- **Clean is the default.** A chart with no prior sticky state renders **Clean** — base candles + volume, nothing else. This is the anti-clutter payoff: open a symbol → restrained chart; indicators, chart-pattern trendlines, and candlestick markers appear only when a preset or an explicit legend-add asks for them.
- **The base-series form is out of scope.** `candleType` (candles / bars / line / area) stays the global [ADR-0062](0062-user-chart-style-overrides.md) `ma.chartStyle` pref; presets **do not** flip it, so switching presets never surprise-changes your candles into a line. (A possible followup: opt-in `candleType` in user-saved presets.)

Presets **never cross the wire, issue no sidecar call, and change no domain state** — pure renderer composition, on the display side of the [ADR-0077](0077-user-originated-display-overlays.md) line. This **refines** ADR-0077/0062/0039 the same way each refined ADR-0015; the rule "the agent is the sole surface for data, analysis, backtests, and any sidecar/domain command" is untouched.

## Consequences

### Positive

- **One-action clean chart.** "Clean" as the default and as a selectable preset gives the user the screenshot-3 restraint on open, and a one-click reset from any clutter.
- **Visibility finally persists.** Promoting the `hidden` set to a persisted per-`(symbol, timeframe)` layer fixes a standing papercut (toggles forgotten on remount) independent of presets.
- **Reuse over new primitives.** Presets compose the existing overlay registry / reconcile path ([ADR-0077](0077-user-originated-display-overlays.md)) and the `ma.*` persistence convention ([ADR-0039](0039-renderer-theming-localstorage.md)). No new drawing machinery, no new sidecar surface, no event type.
- **Built-in + saveable satisfies the ask** without a conversational round-trip: the user captures a personal workflow as a named layout and re-applies it across instruments.
- **The display/control boundary stays intact and gets another data point.** "Can the UI do X?" remains answerable by the ADR-0077 test.

### Negative

- **More persisted display state to reason about.** A fourth concern (presets) plus promoting visibility to persisted means the renderer now juggles overlays + style + visibility + presets. This is real cost, paid in renderer code and mental model.
- **"Applied, not pinned" can confuse.** The selector dropping to "Custom" after a single tweak is intentional but must be legible, or it reads as the preset "not sticking."
- **Global-preset / per-instrument-store asymmetry.** Applying "Clean" on `BTC-USD 1d` does not touch `ETH-USD 1d` until re-applied there. This is deliberate (presets are intents, buckets are memory) but is a model the user must internalise.
- **Persisted visibility accumulates storage.** Another per-`(symbol, timeframe)` `localStorage` entry that must be bounded/pruned and degrade gracefully on full storage (the [ADR-0039](0039-renderer-theming-localstorage.md) pattern).

### Neutral

- **The wire contract is unchanged.** `OverlaySpec`, the `chart.*` events, and the SSE vocabulary are untouched; presets never leave the renderer.
- **Agent overlays are unaffected.** They still merge, dedupe, and persist exactly as ADR-0077 specified; a preset simply seeds the user side of that merge.
- **Symmetric to ADR-0062/0077.** Those let the user choose *how a series is styled* and *which indicator series exist*; this lets the user save and re-apply *a named layout of both*. Same owner (renderer), same convention, same "survives agent redraw" property.

## Alternatives considered

### Alternative A — No presets; rely on the existing per-`(symbol, timeframe)` sticky store only

Keep only the ADR-0077 sticky store and let the user toggle layers per instrument. Rejected because it never offers a one-action "clean reset" and cannot express a reusable named intent ("Trend layout") across instruments — the user explicitly asked for named, saveable presets, and the sticky store is memory, not intent.

### Alternative B — Built-in presets only (no user save)

Ship a fixed Clean/Trend/Mean-reversion/Patterns set with no save-current. Rejected because the user asked to save their own layout; a fixed set cannot capture a personal workflow and would push the user back to manual re-toggling or a conversational round-trip.

### Alternative C — Presets keyed per-`(symbol, timeframe)`

Make a preset belong to an instrument. Rejected because that is precisely the sticky store we already have ([ADR-0077](0077-user-originated-display-overlays.md)), renamed — it would multiply without bound and defeats the point that a preset is a reusable intent applied *across* instruments.

### Alternative D — Presets as an independent snapshot of drawn series, bypassing the overlay store

Persist a raw snapshot of "what's currently drawn" as the preset, sidestepping the ADR-0077 overlay registry. Rejected because it creates a second source of truth for what's on the chart and reintroduces the merge/provenance/dedup problems ADR-0077 just solved. Composing over the existing stores keeps a single reconcile path.

## Notes

- The paired [Plan 0096](../plans/0096-chart-and-app-declutter.md) implements this alongside the rest of the declutter (inline legend, segmented timeframe, collapsible right dock, navigation collapse).
- The reusable artifact here is the **visibility-persistence promotion**: once layer visibility is a persisted per-`(symbol, timeframe)` layer, presets are a thin composition on top of it plus the ADR-0077 overlay store. A future "drawing tools" plan (deferred out of Plan 0096) is orthogonal — it adds a new primitive layer, not a new display-pref container.

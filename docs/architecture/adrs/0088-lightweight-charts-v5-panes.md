# ADR-0088 — Upgrade lightweight-charts v4 → v5 for a real multi-pane charting substrate

> **Status:** proposed (accepts at [Plan 0095](../plans/0095-lightweight-charts-v5-migration.md) close — the behavior-preserving swap + first real `addPane()` consumer land green)
> **Date:** 2026-07-13
> **Related plan(s):** [0095-lightweight-charts-v5-migration](../plans/0095-lightweight-charts-v5-migration.md) (implements + accepts this ADR); unblocks [0091-momentum-divergence-moneyflow-layer](../plans/0091-momentum-divergence-moneyflow-layer.md) phases 6–8 and [0092-price-structure-and-levels](../plans/0092-price-structure-and-levels.md) overlay/annotation phases
> **Related ADRs:** updates the chart-engine substrate of [ADR-0049](0049-chart-trendline-overlay-primitive.md) / [ADR-0061](0061-trendline-pattern-identity-and-colour.md) (the `ISeriesPrimitive` trendline path), [ADR-0062](0062-user-chart-style-overrides.md) (the resolved-style series-creation call sites), [ADR-0077](0077-user-originated-display-overlays.md) (the user-overlay `addLineSeries` reconcile) and [ADR-0045](0045-candlestick-pattern-span-delivery.md) (the span primitive) — **none is superseded**; reaffirms the CSP/security posture of [ADR-0008](0008-electron-shell-conventions.md); pins under [ADR-0012](0012-dependency-cooldown.md) (cooldown) + [ADR-0013](0013-pin-direct-dependencies.md) (exact pin)

## Context

The renderer pins **`lightweight-charts@4.2.3`** (`desktop/package.json`). The 4.2.x line has **no panes API**: a chart is a single pane, and every "sub-pane" the app draws today is faked with an **overlay price scale plus `scaleMargins` bands** on that one pane. `desktop/renderer/lib/chartSeries.ts:26-34` documents the mechanism verbatim — candles occupy the top band (`PRICE_SCALE_MARGINS = {top:0.05, bottom:0.4}`), volume hugs the bottom (`VOLUME_SCALE_MARGINS = {top:0.82, bottom:0}`), OBV gets a strip in between (`OBV_SCALE_MARGINS = {top:0.62, bottom:0.22}`). Three bands on one pane is legible.

[Plan 0091](../plans/0091-momentum-divergence-moneyflow-layer.md) breaks that model. Its UI phases require **five oscillator sub-panes** (Stochastic %K/%D, Stochastic RSI, CCI, Williams %R, ROC) and **three money-flow sub-panes** (MFI, Chaikin Money Flow, A/D line) — eight-plus independently-scaled panes, each with its own baseline (0–100 for MFI/Stoch, zero-centered for CMF, cumulative for A/D), stacked below the price pane. `dev` has landed the backend (phases 1–5, commits `7347392…f4e004b`); `ui-builder` hit the wall on phase 6: stacking eight-plus `scaleMargins` bands on a single pane is unusable — each band collapses to a sliver, the bands share one price axis and crosshair, and per-band autoscale fights its neighbours. Plan 0091's own Risk section (line 145) anticipated this: "oscillator panes may need a framework that doesn't exist yet."

lightweight-charts **v5** introduces a first-class **panes API** (`chart.addPane()`, `IPaneApi`, and a `paneIndex` argument on series creation) that is exactly this framework — real, independently-scaled panes with a shared time axis and a single synchronized crosshair. Adopting it is a **major-version dependency upgrade** with a breaking series-creation API, so it is a decision, not a default.

The forces:

- **The pane requirement is real and recurring**, not one-off: Plan 0091 needs eight-plus panes now, and every future oscillator/sub-pane feature inherits the same ceiling on v4.
- **v5's breaking change touches every series-creation site.** v5 replaces the per-type constructors (`addCandlestickSeries`, `addLineSeries`, `addHistogramSeries`, `addAreaSeries`, `addBarSeries`) with one unified `chart.addSeries(SeriesDefinition, options, paneIndex?)`. The codebase creates series in ~a dozen places (main price series across four render modes, agent overlays, Bollinger Bands, Ichimoku, Supertrend, volume, volume-MA, VWAP, OBV, forming bar, scan series).
- **We have invested infrastructure that must survive the swap** — the `ISeriesPrimitive` span band (ADR-0045) and trendline overlay (ADR-0049/0061), the resolved-style series creation (ADR-0062), and the user-overlay reconcile (ADR-0077). The upgrade must preserve every one of these, not rebuild them.
- **Dependency discipline applies** (ADR-0012/0013): a major bump is an exact-pin manifest edit plus lockfile in one commit, gated by the 14-day cooldown.

## Decision

We will **upgrade `lightweight-charts` from `4.2.3` to `5.x` (target the current `latest`, `5.2.0`), pinned exactly**, and adopt its real panes API as the substrate for multi-oscillator layout. The upgrade lands as a **standalone precursor plan (Plan 0095)** — a behavior-preserving engine swap — *before* Plan 0091's UI phases and Plan 0092's overlay phases build on it.

### Why a standalone precursor plan, not an amendment to 0091

The migration is **independent of momentum/divergence semantics** and has blast radius across *every* existing chart feature (candles, bars, line/area, volume, VWAP, OBV, overlays, Bollinger, Ichimoku, Supertrend, spans, trendlines). Folding an engine swap into 0091's oscillator phases would entangle "did the chart engine change break anything?" with "are the new indicators correct?" in one review. Separating them means:

- Plan 0095's Mode-4 review answers exactly one question — **does the existing chart render identically on v5?** — provable by the existing jest suites going green after a mechanical call-site migration.
- Plan 0091 phases 6–8 (and Plan 0092's overlay/annotation phases) then build on a **proven, stable** panes API rather than swapping engines mid-feature.
- The dependency bump gets its own reviewable commit naming the version (the ADR-0013 discipline), not buried in a feature phase.

### Migration strategy (the breaking-API surface)

1. **Unified series creation.** Every `chart.add<Type>Series(opts)` becomes `chart.addSeries(<Type>Series, opts, paneIndex?)`, importing the series-definition objects (`CandlestickSeries`, `LineSeries`, `HistogramSeries`, `AreaSeries`, `BarSeries`) from `lightweight-charts`. Call sites: `lib/chartSeries.ts` (`createMainSeries` — all four render modes), `hooks/useOverlaySeries.ts` (ema/sma), `hooks/useBbandsSeries.ts`, `hooks/useIchimokuSeries.ts`, `hooks/useSupertrendSeries.ts`, `hooks/useFormingBar.ts`, `hooks/useChartScans.ts`, and the volume/volume-MA/VWAP/OBV series in `components/CandlestickChart.tsx`. This is the bulk of the churn and is **atomic** — a partial migration does not compile, so the bump + all series sites + primitive types land in one commit.
2. **Real panes for new stacked content.** `chart.addPane()` returns an `IPaneApi` (`.setHeight()`, `.moveTo()`, `.getStretchFactor()`); a series is placed on a pane via the third `paneIndex` argument of `addSeries` (panes auto-create up to the requested index), and `series.moveToPane(i)` relocates one. Plan 0095 proves the API by moving the **existing OBV strip** from its `scaleMargins` band onto a real pane (pane 1) behind a small reusable pane helper (`lib/panes.ts`); Plan 0091 ph6–8 and Plan 0092 then add panes 2..N.
3. **Volume stays a price-pane overlay band.** Volume-under-price is the near-universal chart idiom, and `scaleMargins` overlay scales **remain supported in v5**. To keep 0095 a true no-visual-change swap, volume + volume-MA + VWAP stay on the price pane exactly as today; only OBV (a standalone indicator strip) migrates to a real pane. Moving volume to its own pane is explicitly out of scope, revisitable later.
4. **Primitive type renames.** v5 generalized primitives to attach to panes as well as series, dropping the `Series` prefix from several primitive-facing types. `lib/spans.ts` and `lib/trendlines.ts` import `ISeriesPrimitivePaneView` / `ISeriesPrimitivePaneRenderer` / `SeriesPrimitivePaneViewZOrder` (see `spans.ts:15-23`); these map to the v5 names (verify against the shipped `5.2.0` typings — the `ISeriesPrimitive` attach path, `series.attachPrimitive`, `paneViews()`, `zOrder()`, and `autoscaleInfoProvider` are all retained; only the type imports move). No primitive is rebuilt; the pixel-math (`computeSpanRects`, trendline coordinate mapping) is untouched.
5. **Watermark / removed options.** v5 moves the watermark from a chart option to a plugin (`createTextWatermark`) and renames a few option fields. The codebase uses no watermark; Plan 0095 phase 1 audits the typecheck output for any other removed-option breakage and fixes each at its call site (typecheck is the exhaustive gate here).
6. **Candle series-type rebuild still holds.** ADR-0062's "switching the main series render type rebuilds the chart instance" contract is unchanged — the rebuild now recreates the series via `addSeries(<Type>Series, …)`, but the rebuild semantics, the resolved-style application (`applyMainColors`), and primitive re-attach are identical.

### Impact on the named ADRs — call-site updates, no decision reversed

- **ADR-0062** (user chart-style overrides): `createMainSeries` / `applyMainColors` / the four-mode rebuild move to `addSeries`. The single-typed-style-store decision, per-theme overrides, inert controls in line/area mode, and rebuild-on-candle-type-change all **stand unchanged**. Implementation-substrate update only.
- **ADR-0049 / ADR-0061** (trendline primitive + colour-by-pattern): the `ISeriesPrimitive` path survives; only the primitive type imports change (point 4). The geometry, dedupe, `hitTest`, grouped legend, and colour model are untouched.
- **ADR-0077** (user-originated overlays): `useOverlaySeries`'s `addLineSeries` becomes `addSeries(LineSeries, …)`. The renderer-owned-display-preference classification and the `ma.userOverlays` store are untouched.
- **ADR-0045** (pattern span band): same primitive-type-import update as the trendline primitive; behavior identical.
- **None of these is superseded** — this ADR records an engine upgrade beneath decisions that all still hold. Each related ADR keeps its `accepted` status; this ADR is the pointer that their series-creation/primitive call surface moved to v5.

### CSP / security posture (ADR-0008) — unaffected, reaffirmed

lightweight-charts is a **bundled npm dependency** rendering to a `<canvas>`: it issues no network requests, loads no remote fonts/tiles, spawns no workers from URLs, and uses no `eval`. The double-CSP (`default-src 'self'`; `connect-src 'self' http://127.0.0.1:*`) is **unchanged** by v5 — no new `connect-src`, `script-src`, `worker-src`, or `img-src` relaxation is required. The upgrade is invisible to the security boundary; ADR-0008's defaults stand verbatim.

### Dependency discipline (ADR-0012 / ADR-0013)

Pin **`lightweight-charts` at an exact `5.x`** in `desktop/package.json` (no `^`/`~`), targeting `5.2.0` (current npm `latest`). The **14-day cooldown is auto-enforced** at `pnpm install` by `minimumReleaseAge: 20160` in `pnpm-workspace.yaml` (ADR-0012): if `5.2.0` is younger than 14 days at install time, resolution refuses it and Plan 0095 pins the newest 5.x that satisfies the window — no per-package allowlist. Manifest edit + `pnpm install` lockfile land in **one commit** naming the version (ADR-0013). No range operators, dev or runtime.

## Consequences

### Positive
- **A real multi-pane substrate.** Eight-plus independently-scaled oscillator/money-flow panes with a shared time axis and one synchronized crosshair — the exact capability Plan 0091 needs, and a ceiling lifted for every future sub-pane feature.
- **One series-creation API.** `addSeries(Def, opts, paneIndex)` unifies the five per-type constructors; a maintainer reads one call shape instead of five.
- **Smaller renderer bundle.** v5's series definitions are tree-shakeable — only the series types actually imported ship, versus v4 bundling all constructors.
- **De-risks two plans.** 0091 ph6–8 and 0092's overlay phases build on a proven engine; the swap's own regression risk is isolated in 0095's review.
- **Investment preserved.** The span + trendline primitives, resolved-style creation, and user-overlay reconcile all survive as call-site updates, not rewrites.

### Negative — the price we pay
- **Large, atomic blast radius.** Every series-creation site changes in one commit (a partial migration doesn't compile). The mitigation is that the change is *mechanical* and the existing jest + typecheck suites are the exhaustive regression net — but the diff is wide and must be reviewed as "prove nothing visual changed."
- **A major-version bump on the chart engine.** v5 is newer to this codebase; subtle option/behavior differences (autoscale defaults, price-scale margins interplay with real panes, crosshair-over-panes) may surface only at runtime. Mitigation: Plan 0095 phase 3 is a human visual smoke across every existing chart feature before 0091 builds on it.
- **Primitive-type churn we don't fully control.** The v5 primitive type names must be confirmed against the shipped `5.2.0` typings, not assumed. Mitigation: typecheck is the gate; the plan verifies against the installed types, not memory.
- **Test suites shift to the v5 call shape.** Suites asserting `addCandlestickSeries`/`addLineSeries` were called (or driving the `__test_chart_render__` hook) update to the `addSeries` form. Expected, enumerated churn — the asserted *outcomes* (which series kinds render, in which pane) are unchanged.

### Neutral
- **The wire contract is untouched.** v5 is renderer-internal: no sidecar change, no `OverlaySpec`/`TrendlineSpec`/event schema change, no payload-version bump. The upgrade is invisible to the Python side and to the agent.
- **Volume's visual layout is deliberately unchanged** (price-pane bottom band), so the only visible delta 0095 introduces is OBV gaining its own real pane — a small, tested improvement, not a regression.

## Alternatives considered

### Alternative A — Stay on v4; stack more `scaleMargins` bands
Keep 4.2.3 and add five oscillator + three money-flow bands to the single pane. Rejected: eight-plus bands on one pane each collapse to an unreadable sliver, share one price axis and crosshair, and fight each other's autoscale — the exact wall `ui-builder` hit. `scaleMargins` was always a two-to-three-band stopgap (its own code comment calls it "the plan's documented v4 mechanism / OBV fallback"), not a multi-oscillator layout.

### Alternative B — Stay on v4; stack a second `IChartApi` below the price chart
Render oscillators in a second lightweight-charts instance beneath the price chart, syncing time scales manually. Rejected: it reintroduces the manual `subscribeVisibleTimeRangeChange` sync loop and a second crosshair that ADR-0049 chose primitives specifically to avoid, and two chart instances double the lifecycle/resize/theming surface — more moving parts than a version bump, for a worse result.

### Alternative C — Stay on v4; draw oscillators on a separate absolutely-positioned HTML canvas
Paint the oscillator panes onto our own canvas outside lightweight-charts. Rejected for the same reason ADR-0049 rejected it for trendlines: it would not track pan/zoom without bespoke coordinate math and a manual visible-range subscription — reinventing the coordinate system the library already owns.

### Alternative D — Switch to a different charting library
Replace lightweight-charts entirely (e.g. a general canvas/WebGL charting lib or a React charting framework). Rejected: it discards the substantial invested infrastructure — the span band, the trendline primitive with `hitTest`, the resolved-style store, the overlay reconcile, the four render modes — and is a multi-plan rewrite to solve a problem a same-family major bump solves. v5 is the continuity path; a library switch is not on the table for a panes requirement.

## Notes
- Proposed alongside Plan 0095; accepts at that plan's close if the behavior-preserving swap + the first real `addPane()` consumer (OBV on its own pane) land green with no visual regression across the existing chart features.
- The exact pinned version is decided at install by the cooldown gate, not fixed here: target `5.2.0`, fall back to the newest cooldown-eligible 5.x if resolution refuses it. Dev confirms the pin against `pnpm install`, never from memory (the project version-hygiene rule).

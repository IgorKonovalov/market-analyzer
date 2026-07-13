# ADR-0092 — Imperative `ChartController` core; the React chart component is a thin adapter

> **Status:** proposed
> **Date:** 2026-07-13
> **Related plan(s):** [0098-chart-controller-refactor](../plans/0098-chart-controller-refactor.md) (accepts this ADR at its close)

## Context

`CandlestickChart.tsx` is the desktop app's most-touched renderer file and its recurring god component. Plan 0072 phase 8 decomposed it once (1455 → 706 lines) by extracting per-concern **hooks** and pure `lib/` modules. That decomposition had no ADR of its own — it was one phase of a remediation audit — and it left the component as the **orchestrator**: it still owns every chart ref and wires every feature. Within ~seven months it regrew to 905 lines, because the hooks-orchestration model has no structural brake on regrowth. Each new indicator family (Bollinger Bands, Ichimoku, oscillator panes, cross-pane divergences) added its own `useRef`s, its own lines in the 158-line creation effect, its own hook call in the render body, and its own effect dependencies.

The measurable god-ness is coordination surface, not raw line count: 22 `useRef`s created in the component and passed *by reference* into hooks (`chartRef` into 9 hooks, `seriesRef` into 5); a creation effect doing ~8 jobs with a hand-written 17-ref teardown; cross-hook ordering constraints expressed as comments ("`useDivergences` MUST run after `useOscillatorPanes`") plus a bridging memo (`requiredOscillatorKinds`) that exists only to thread one hook's output into another's input.

lightweight-charts is an **imperative** library (create chart, add series, add pane, attach primitive, feed data, dispose). Since Plan 0095 / ADR-0088 the app drives it on v5 with a real `PaneRegistry` (`lib/panes.ts`) — itself a small imperative controller for panes. The forces: (1) the imperative wiring genuinely needs a single owner that enforces attach-ordering; (2) React hooks are a poor home for that owner because ordering-between-hooks is fragile and the wiring can only be tested by rendering the whole component in jsdom; (3) whatever we choose must be behavior-preserving and must not become the next god artifact.

## Decision

We will extract a plain-TypeScript **`ChartController`** (no React) that owns the imperative lightweight-charts surface — the `IChartApi` instance, the main and always-on series, the `PaneRegistry`, the overlay/oscillator reconcilers, and the primitives — and exposes a **declarative API** (`mount`/`dispose`, `setBars`, `setCandleType`, `setOverlays`, `setOscillators`, `setTrendlines`, `setIchimoku`, `setDivergences`, `setMarkers`, `setQuote`, `setTimeframeAxis`, `restyle`). Attach-ordering and cross-feature dependencies (e.g. a divergence's oscillator pane must exist before its segment is fed) live **inside** the controller as structure, not as comments in a render body. The controller is composed of small focused sub-units — a series registry, an overlay reconciler, an oscillator-pane reconciler, a primitive hub, and a restyle controller — so the facade delegates rather than accumulating logic. The React component (`CandlestickChart.tsx`) becomes a thin adapter: it constructs the controller in one effect, forwards declarative props through a handful of effects, keeps only the hooks that produce React state and JSX (gestures, tooltip, scans, legend, candle-marker groups, user-overlay handlers) — each consuming the controller through **one handle** instead of many raw refs — and renders. The pure `lib/` math/geometry modules are unchanged and are consumed by the controller.

## Consequences

### Positive
- **Regrowth has a structural brake.** Adding an indicator family becomes a controller method + one forward effect, not new refs + creation-effect lines + a hook call + effect deps in a god component. The component stops being the place features are wired.
- **The imperative wiring is unit-testable headless.** Series creation, overlay/pane reconcile, primitive feed, restyle-in-place, and dispose teardown can be asserted against the controller with the existing lightweight-charts jest mocks — without rendering the React component in jsdom. This is coverage the app could previously only get through a full component render.
- **Ordering becomes structural.** The divergence→oscillator-pane dependency and the attach-at-mount primitive discipline (the Plan 0064 stranding fix) are enforced inside the controller, not by hook call order + comments.
- **The ref web collapses.** ~22 component refs become one `controllerRef`; the 158-line creation effect becomes `controller.mount(...)`.

### Negative
- **A large, high-risk refactor of the hottest renderer file**, in hard contention with three other chart-file plans (0092/0096/0097). It buys no user-visible feature — the entire value is internal quality and future velocity, which is a real cost to justify now.
- **A new abstraction to learn.** Contributors must know that imperative chart work goes in the controller and only React-state/JSX concerns stay in the component. A misfiled concern (imperative logic creeping back into the component, or React state leaking into the controller) reintroduces the coupling.
- **Behavior-drift risk behind a green suite.** The `__test_chart_render__` hook proves series presence, not rendered geometry; the refactor leans on keeping every spec green + headless controller specs + a human visual smoke to catch pixel drift.
- **The facade could become a new god class** if sub-units aren't kept honest. Mitigated by the composition rule (facade delegates; a facade over ~250 lines is a smell) but it is a standing discipline, not a guarantee.

### Neutral
- Several `hooks/use*Series` / `use*` files are deleted and their logic moves into `lib/chart/`; the hook count drops but the total code is comparable. Pure `lib/` modules are untouched.
- No wire, event, schema, or CSP change — renderer-internal, so the ADR-0008 security posture is unaffected.

## Alternatives considered

### Alternative A — Targeted relief, keep the hooks-orchestration model
Extract the creation effect into a `useChartInstance` hook and bundle the 22 refs into one `ChartHandles` object, leaving the per-family hooks in place. Rejected because it patches the fragile core but leaves the regrowth mechanism intact — adding an indicator still edits the component. This is essentially the Plan 0072 approach again, and Plan 0072 is precisely why the component regrew; repeating it would buy another ~seven months, not a structural fix.

### Alternative B — Declarative feature-registry / plugin table
Define one uniform `ChartFeature` descriptor (`createSeries`/`feed`/`teardown`/`tooltip`/`legend`) and drive every indicator family from a table the component iterates, so adding a feature is a table entry. Rejected because overlays (main-pane line series), oscillator panes (own v5 pane), primitives (attached drawings on the logical scale), price lines, and markers have genuinely different lifecycles and coordinate systems; forcing them through one interface yields a lowest-common-denominator abstraction that leaks. It is the most extensible option but only pays off if many more heterogeneous families are coming — over-engineering for this app's trajectory. The controller's typed per-family methods keep each family's real shape.

### Alternative C — React context + self-registering feature child components
A `ChartProvider` owning the chart, with `<OverlayLayer/>`, `<OscillatorLayer/>`, `<DivergenceLayer/>` children that self-register via context effects. Rejected because lightweight-charts is imperative and order-sensitive; expressing "panes before divergence primitives" as inter-child effect ordering is more fragile than a single imperative owner, and the current code already fights exactly this (ordering-as-comments). It also keeps the wiring inside React, preserving the untestable-without-render problem.

## Notes

- Builds directly on Plan 0095 / [ADR-0088](0088-lightweight-charts-v5-panes.md): the v5 `PaneRegistry` is the first imperative chart sub-controller and becomes a component of `ChartController`.
- Formalizes the structure the Plan 0072 phase-8 decomposition left implicit (that decomposition had no ADR).
- Behavior-preservation is validated by the same three-layer net as Plans 0072 and 0095: keep the renderer jest suite green per phase, add headless controller specs, gate the close on a human visual smoke.

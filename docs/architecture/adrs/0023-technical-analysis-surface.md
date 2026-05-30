# ADR-0023 — Technical-analysis surface lives in `src/market_analyser/analysis/`

> **Status:** accepted (2026-05-30, at Plan 0018 close)
> **Date:** 2026-05-24
> **Related plan(s):** [0018-technical-analysis-surface](../plans/0018-technical-analysis-surface.md)

## Context

The `market-analyst` skill advertises candlestick-pattern detection (doji, hammer, engulfing, morning/evening star, …), indicator stance (RSI, MACD, Bollinger, Supertrend, …), and trend/momentum/support-resistance classification. **None of that code exists.** `CLAUDE.md` reserves `src/market_analyser/analysis/` for "patterns, indicators surface — to be authored," and `project-context.md` lists `analysis/indicators.py` as "Future (no plan yet; analyst dep)." The skill is shipping promises against an empty package.

The only indicator math we have today lives *inside individual strategy modules* — `rsi.py` computes Wilder's RSI inline, `macd.py` computes MACD inline, and so on. That is exactly the "don't ad-hoc shared infrastructure in a single skill" anti-pattern `CLAUDE.md` warns about: the math is trapped where only one consumer can reach it, and a second consumer (the analyst, a multi-timeframe view, a volume scanner) has no home to import from.

The leverage is high: a full-symbol technical read, candlestick scans, multi-timeframe alignment, volume scanners, and Bollinger-squeeze/rating scans all sit on top of one shared indicator/pattern computation layer. We cannot plan any of those until that layer exists.

The cross-cutting non-negotiable that makes this a *decision* rather than a no-brainer: **no lookahead bias.** A condition reported at bar `i` may only see `bars[0..=i]`. Indicators must be trailing, not centered. Whatever shape this layer takes, that invariant has to be enforced *at the layer*, not rediscovered by every caller.

## Decision

We will create `src/market_analyser/analysis/` as the **canonical home** for pure, trailing, deterministic technical-analysis functions: indicators (`indicators.py`), candlestick patterns (`patterns.py`), and a composed condition snapshot (`snapshot.py`). Every function is pure (same bars in → same output out, no I/O, no module-level mutable state, no wall-clock or RNG), written in plain Python with **no pandas, no numpy, and no third-party TA library**, consistent with [ADR-0009](0009-rewrite-data-layer-in-house.md) (own the data/analysis layer) and [ADR-0013](0013-pin-direct-dependencies.md) (dependency parsimony).

The anti-lookahead invariant is enforced here: every indicator returns a series aligned to the input bars where `result[i]` is computed only from `bars[0..=i]` (undefined leading bars are `None`, the convention `rsi.py` already uses), and every pattern detected at bar `i` reads only `bars[0..=i]`. This is the single anti-lookahead seam for all non-strategy analysis; callers (analyst tools, multi-timeframe, scanners) consume it and inherit the property for free.

We will **not** refactor the existing strategy modules onto this surface in the introducing plan. The strategies' inline indicator math is pinned by the backtest determinism golden tests ([ADR-0018](0018-backtest-result-schema.md)); reconciling them carries real regression risk that should not ride along with the foundational build-out. `analysis/` is nonetheless the mandated home for every **new** indicator consumer, and strategy reconciliation onto it (output-byte-identical, behind the golden tests) is recorded as a followup.

## Consequences

### Positive
- The `market-analyst` skill stops promising against nonexistent code — it gets a real surface to compute against.
- Multi-timeframe alignment, volume scanners, and Bollinger/rating scans (Plan 0021 and beyond) have a foundation to build on instead of each re-implementing indicators.
- One enforced anti-lookahead seam for all analysis, separately testable, rather than the invariant being re-litigated per caller.
- Pure-Python + no pandas/numpy keeps the dependency surface small and sidesteps the Windows pandas-wheel build pain the upstream documents in its own README troubleshooting section.

### Negative
- **Temporary duplication.** Until the reconciliation followup lands, RSI/MACD/Bollinger math exists both in `analysis/indicators.py` and inside the strategy modules. Two copies can drift. We accept this deliberately to protect the determinism golden tests, and we track the reconciliation so it does not become permanent.
- **We own indicator correctness.** Pure-Python ADX, Supertrend, and ATR are fiddly (Wilder smoothing, true-range edge cases). Bugs here propagate to every analyst report. Mitigation: per-indicator unit tests with hand-worked fixtures and an explicit anti-lookahead test (truncating the bar series must not change earlier outputs).

### Neutral
- Candlestick-pattern detection is inherently heuristic (body/shadow ratios, thresholds). The thresholds become project constants we own and tune; there is no "correct" reference to validate against, only internal consistency.

## Alternatives considered

### Alternative A — Use a third-party TA library (`ta`, `pandas-ta`, or TA-Lib)
Rejected. `ta`/`pandas-ta` pull in pandas + numpy (heavy, and pandas wheels are the documented Windows failure mode in the upstream we are diverging from); TA-Lib needs a C build that is painful on Windows. All three would put indicator behavior outside our control, complicating the determinism guarantee we need for reproducible analysis and backtests. [ADR-0009](0009-rewrite-data-layer-in-house.md) already committed us to owning this layer.

### Alternative B — Refactor the six strategy modules onto the new surface now
Rejected for the introducing plan. The strategies' signal output is pinned byte-for-byte by the Plan 0008 determinism golden tests; any change to the shared indicator math (even a numerically equivalent refactor) risks shifting a signal and tripping those tests. The reconciliation is worth doing, but as a controlled, output-preserving followup with its own regression net — not bundled into the foundational build.

### Alternative C — Leave indicators in the strategy modules; let the analyst import strategy internals
Rejected. This couples the analyst (and every future scanner) to strategy module internals, inverting the dependency direction and entrenching the very anti-pattern this ADR exists to undo. Strategy modules should be leaf consumers of shared analysis, not the library.

## Notes
- The candlestick pattern vocabulary mirrors the set the `market-analyst` SKILL.md already names, so the skill's description and the code line up at landing.
- `as_of`-style historical replay needs no special handling at this layer: because every function is trailing, truncating the input bar series to `bars[0..=as_of]` is sufficient — the truncation happens in the data fetch, not here.

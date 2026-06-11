# ADR-0050 — Short-selling in the strategy + backtest contract

> **Status:** accepted (Plan 0053 close 2026-06-11)
> **Date:** 2026-06-08
> **Related plan(s):** 0053-short-selling-support (implements); 0054-chart-pattern-breakout-strategy (first consumer)
> **Related ADRs:** amends [0004](0004-strategy-interface.md) (strategy interface — long-only `SignalKind`); extends [0018](0018-backtest-result-schema.md) (engine semantics + determinism golden); relates [0024](0024-extended-backtest-metrics.md) (metrics) and [0025](0025-trade-execution-feasibility.md) (no-real-money posture)

## Context

[ADR-0004](0004-strategy-interface.md) defined the strategy contract: a pure `generate_signals(bars, params) -> Sequence[Signal]`, where `SignalKind` is `enter_long` / `exit_long` and shorts are explicitly *reserved but not implemented*. The backtest engine (Plan 0008) executes long-only end to end: `signals_to_trades` opens a position at the next bar's open, and `_build_equity_curve` compounds long P&L (`units = cash / entry`, valued at the exit price). The live-signal evaluator (Plan 0026, `backtest/live_signal.py`) and every shipped strategy assume this single-direction-up model.

Plan 0052's classical chart patterns include inherently **bearish** setups — head & shoulders, double top, rising wedge, a descending-triangle down-break. During the Plan 0052/0054 interview the user chose to trade these **symmetrically** (open shorts) rather than use the long-only "bullish enters / bearish exits" mapping. That requires the contract and engine to model short positions — and short support is broadly useful (any strategy may want a directional-down view), not specific to chart patterns, so it is a capability decision in its own right rather than a detail buried in a strategy.

The constraint that makes this a real decision: the engine is on the **financially-meaningful determinism path**. [ADR-0018](0018-backtest-result-schema.md) pins a byte-identical `BacktestResult` (modulo run provenance) via a cross-process golden fixture. Touching `signals_to_trades` and the equity curve invalidates that golden and bumps `ENGINE_VERSION`, so the change must be deliberate, symmetric, and re-verified — not bolted on.

## Decision

We will add **`enter_short`** and **`exit_short`** to `SignalKind` and generalize the engine's position model from flat/long to **flat / long / short**, with short P&L the exact mirror of long and **no borrow or financing cost** in v1 (frictionless, symmetric with how longs are modeled today).

- **Contract:** `SignalKind` gains `enter_short` / `exit_short`; the `Signal` shape is unchanged. The strategy Protocol and its docs note the position is single-direction at a time.
- **Engine:** `signals_to_trades` opens a short at the next bar's open on `enter_short` (while flat) and closes it on `exit_short` (while short); a short's realized P&L is `entry − exit` (the inverse of a long), charged the **same** transaction cost a long pays. The equity curve generalizes by signing the position direction (one code path, not a forked short path). The state machine is single-direction: flat → long *or* short → flat; **no simultaneous long+short, no pyramiding**. When a strategy emits a long-exit and a short-entry referencing the same bar, the documented order is **exit first, then enter** (flat between, on the same next-open), pinned by a test.
- **Determinism:** bump `ENGINE_VERSION`, regenerate the Plan 0008 golden fixture, and re-pin the cross-process determinism golden (`model_dump(exclude={"run_id","started_at","finished_at"})`). The live-signal evaluator (Plan 0026) gains the short states symmetrically.

## Consequences

### Positive
- Strategies can express the full directional view; the Plan 0054 chart-pattern strategy can trade bearish formations as shorts instead of leaving half the patterns untradeable.
- Broadly reusable — every existing and future strategy gains shorting for free.
- The symmetric `entry − exit` math keeps the engine a single signed code path rather than two parallel ones, which keeps it auditable and deterministic.

### Negative — the price we pay
- **Touches the determinism-critical path.** The `ENGINE_VERSION` bump invalidates the existing golden fixture; it must be regenerated and re-verified cross-process, and any persisted `BacktestRunSummary` semantics assuming long-only need a look. This is the careful part, and the reason this is its own plan.
- **Frictionless shorts overstate real short returns** — no borrow/financing cost means a backtest is optimistic for a hard-to-borrow name. A known, documented simplification (the user chose it for v1); revisited if/when it matters.
- **More state transitions to test.** flat/long/short has the same-bar exit-then-enter ordering, the never-pyramid invariant, and short-while-already-short (ignored) cases that long-only never had. Mitigation: each is pinned by an explicit test.

### Neutral
- The extended metrics (ADR-0024) operate on the equity curve / trade returns and are direction-agnostic — they need no change, only re-verification on a short-bearing run.

## Alternatives considered

### Alternative A — Long-only "bullish enters / bearish exits" mapping (no engine change)
Map bearish patterns to `exit_long` only, never opening a short. Rejected by the user in the Plan 0052/0054 interview: it leaves bearish setups untradeable as positions and can't express a down-view; the whole point of including H&S/double-top/rising-wedge in the traded set is to short them.

### Alternative B — Model shorts as a separate inverse synthetic series
Run a mirrored "inverted price" backtest for shorts. Rejected: duplicates the engine, drifts from the long path over time, and doubles the determinism surface — the opposite of the single signed code path.

### Alternative C — Include borrow/financing cost now
Charge a per-bar borrow rate while short. Rejected for v1: it needs a borrow-rate assumption and its own parameterization (and arguably its own ADR), and the user chose frictionless. Additive later — a per-bar carry charge slots into the same signed equity path.

## Notes

Shorts stay entirely within the no-real-money posture ([ADR-0025](0025-trade-execution-feasibility.md)): this is backtest/paper modeling only, no execution venue. The same-bar exit-then-enter ordering and the never-pyramid invariant are the two behaviors most likely to surprise a future reader — both are pinned by named tests in Plan 0053.

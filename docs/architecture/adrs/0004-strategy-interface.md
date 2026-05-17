# ADR-0004 — Strategy interface: typed function + declarative params

> **Status:** accepted
> **Date:** 2026-05-17
> **Related plan(s):** [0002-strategy-interface](../plans/0002-strategy-interface.md)

## Context

A strategy is the central object the `strategy-author` skill writes and the `backtester` skill consumes. We have not yet decided what one *is* in code. Three shapes are in play and the decision is hard to revert: every later layer (backtest engine, parameter UI, persistence of `Strategy` rows, walk-forward optimization) will hard-code the assumption.

The vendored backtest engine from `tradingview-mcp` (`core/services/backtest_service.py`) currently encodes each strategy as a private function plus a string key in `_STRATEGY_MAP`. The function signature is `(candles, **params) -> list[trade_dict]`. Strategy parameters (`oversold=40`, `period=14`, etc.) are positional defaults — they're not introspectable, not validated, and not surfaced anywhere a UI could find them. Adding a seventh strategy today means editing that file and the `_STRATEGY_LABELS` dict. There is no way for a user to author a strategy without editing core.

Three constraints shape the decision:

1. **Determinism is non-negotiable** (see `best-practices.md` — backtests must be byte-identical on re-run). Any state attached to a strategy across bars must be explicit and not leak between runs.
2. **The UI needs to render a parameter form** for every strategy without bespoke code per strategy. That means parameter metadata (name, type, default, range, label) must be programmatically discoverable.
3. **The `strategy-author` skill is an LLM agent.** It is much more reliable producing a small file with a `pydantic` model and a function than producing a class hierarchy with `__init__`, mutable attributes, and inheritance discipline.

## Decision

We will represent a strategy as **a single Python module** containing:

1. A `pydantic.BaseModel` subclass named `Params` declaring the strategy's tunable parameters (with types, defaults, and field-level constraints).
2. A pure function `generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]` that emits `Signal` events (`enter_long`, `exit_long`, future: `enter_short`) at bar indices.
3. A module-level `META: StrategyMeta` constant with `id`, `name`, `description`, `version`, and `timeframes` (which intervals the strategy supports).

The backtest engine — not the strategy — owns position state, capital, costs, equity curve, and metrics. The strategy is a **pure function from bars to signals**. It does not call the data layer, does not see capital, does not know its own commission. This is the swappable contract.

A small `StrategyProtocol` (a `typing.Protocol` with `META`, `Params`, and `generate_signals`) lives in `src/market_analyser/contracts/strategy.py`. The backtester depends on the protocol, not on any concrete strategy. Strategies are discovered by walking `src/market_analyser/strategies/*.py` and importing each module; no registry decorator, no plugin metaclass.

We rejected the class-based approach because it invites mutable instance state (the single biggest source of non-determinism in vendored code we've seen) and gives `strategy-author` more rope than it needs. We rejected a "fully declarative JSON/YAML config + interpreter" approach because it forces us to invent a DSL before we know what we need, and because `strategy-author` is already capable of writing typed Python.

## Consequences

### Positive

- **Determinism by construction.** The strategy function takes its full state in (bars + params) and returns its full state out (signals). There is no instance to leak prior-bar state into the next backtest.
- **Discoverable parameters.** The UI can call `Params.model_json_schema()` and render a form for any strategy without per-strategy code. `pydantic` validates inputs at the boundary.
- **Cheap LLM authoring.** `strategy-author` produces one file with a known shape. We can give it a template (`templates/strategy_stub.py`) and a single golden example. No "did the agent remember to call `super().__init__`" failures.
- **Decoupled from the backtest engine.** Three engines (event-driven, vectorized, walk-forward) can all consume `generate_signals` — they differ only in how they apply signals to positions and costs.
- **Easy to test in isolation.** A unit test for a strategy is `assert generate_signals(fixture_bars, Params()) == expected_signals`. No engine, no mocks.

### Negative

- **No instance state means no streaming online updates** within a backtest. A strategy that wants to maintain a rolling indicator can't cache it on `self` — it must recompute or rely on the caller to pass precomputed indicators in. For the indicators we have today (RSI, Bollinger, MACD, EMA, Supertrend, Donchian) this is fine; for a future Kalman filter or HMM strategy we may want to revisit. We accept this cost — the alternative (mutable instance state) is the worse trade.
- **Parameter sweeps construct many `Params` instances.** Cheap, but worth noting — `pydantic` v2 model construction is roughly 1-3 µs which is irrelevant relative to bar processing.
- **`Signal` is a new shape** that the vendored engine doesn't currently produce; the engine emits closed trades directly. We will write a thin adapter (`signals_to_trades`) that consumes `Signal`s and produces the existing trade dicts, so we don't have to rewrite metrics/equity-curve code in slice 1. See plan 0002 slice 3.
- **Strategy discovery by directory scan** is implicit. We accept this because it keeps the authoring story simple ("drop a file in `strategies/`"); the alternative (explicit registry) adds a step `strategy-author` would have to remember. We can add a registry later if collision/ordering becomes a real problem.

### Neutral

- `pydantic` becomes a hard runtime dependency for the backend (already expected per `project-context.md`).
- The six vendored strategies will be **rewritten as modules under the new contract** in plan 0002 slice 4, not vendored as-is. We are not preserving the `_run_rsi(candles, **_)` signature.

## Alternatives considered

### Alternative A — Strategy as a class

A `Strategy` ABC with `__init__(self, params)`, `on_bar(self, bar) -> Signal | None`, and instance attributes for rolling state. This is the canonical OO pattern (PyAlgoTrade, Backtrader, Zipline all use it).

Rejected because: (1) instance state is the dominant source of non-determinism we've seen in vendored trading code, and explicit state-in/state-out is cheaper to enforce than instance-state discipline; (2) `strategy-author` (an LLM) produces typed functions much more reliably than it produces class hierarchies with correct `__init__` plumbing; (3) class hierarchies pressure us toward inheritance ("`MeanReversionStrategy` extends `Strategy`") which is the wrong axis of reuse — strategies share *parameters and indicators*, not behavior contracts.

### Alternative B — Pure declarative config (JSON/YAML + interpreter)

Strategy is a JSON document; the engine has a built-in expression evaluator that interprets rules like `{"enter": "rsi < oversold"}`. No Python file per strategy.

Rejected because: we'd have to invent and maintain a DSL strong enough to express the existing six strategies (supertrend and Donchian channels involve non-trivial state). That's months of work to replicate what 80 lines of Python already do. The premise — "non-programmers can author strategies" — is also a non-goal: the user is technical and `strategy-author` writes the code. We'd be paying a DSL tax for a benefit we don't need.

### Alternative C — Keep the current `(candles, **params) -> trades` shape

Vendor the existing functions verbatim and just add a string registry.

Rejected because: (1) parameters aren't introspectable, so the UI can't render a parameter form without per-strategy code; (2) strategies emit closed trades, which conflates two responsibilities (signal generation + position management) and prevents the engine from doing things like shorting, stop-losses, or pyramiding without rewriting every strategy; (3) `**params` swallows typos silently — `oversold=40` and `oversld=40` are both legal calls.

## Notes

- The vendored `_STRATEGY_MAP` in `backtest_service.py:196-203` is our reference for the current state. We are explicitly *not* vendoring that pattern.
- See plan `0002-strategy-interface.md` for the implementation slices that realize this ADR.
- A second ADR may be needed once we decide how indicators are computed (in the strategy vs precomputed and passed in). That's a tradeoff between locality and reuse and is deferred — `generate_signals` can do either, the contract doesn't force it.

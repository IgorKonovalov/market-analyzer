# Strategy execution sequence

Companion to [plan 0002](../plans/0002-strategy-interface.md) and [ADR-0004](../adrs/0004-strategy-interface.md).

This diagram shows the *runtime* shape of a backtest under the new strategy contract — specifically the order of operations that prevents lookahead bias. The plan's inline diagram shows the static module relationships; this one shows what happens when you press "run".

```mermaid
sequenceDiagram
    autonumber
    participant UI as UI / CLI
    participant Eng as backtest.engine
    participant Strat as Strategy module<br/>(generate_signals)
    participant Adapt as backtest.adapter<br/>(signals_to_trades)
    participant Met as Metrics + equity

    UI->>Eng: run(strategy_id, params_dict, bars, costs)
    Eng->>Eng: Params.model_validate(params_dict)
    Note over Eng: Validation fails loudly here<br/>before any computation.

    Eng->>Strat: generate_signals(bars[0:N], params)
    Note right of Strat: PURE call.<br/>No I/O, no globals,<br/>no asyncio. Same bars +<br/>params ⇒ same signals.
    Strat-->>Eng: Sequence[Signal]<br/>each tagged with bar_index

    Eng->>Adapt: signals_to_trades(bars, signals)
    Note right of Adapt: Execution rule:<br/>signal at bar_index = i<br/>fills at bars[i+1].open.<br/>Last-bar signal is dropped.<br/>This is the anti-lookahead seam.
    Adapt-->>Eng: list[trade_dict]

    Eng->>Met: apply_costs + calc_metrics + equity_curve
    Met-->>Eng: BacktestResult
    Eng-->>UI: BacktestResult (JSON-serializable)
```

## Why the adapter, not the strategy, owns execution timing

The strategy says "at bar 42, enter long". It does **not** say "at bar 42's close, at price 100.50". The adapter is the only code that turns a signal into a fill, and it always executes at the *next* bar's open. This single rule is what prevents the most common lookahead bug in backtesting: a strategy reading `bars[i].close` to decide *and* fill at `bars[i].close`.

If we ever support intrabar fills (stop-loss triggers within a bar), they go in the adapter, not the strategy. The strategy contract stays small.

## Determinism contract enforced at each arrow

| Arrow                              | Determinism property                                                                                  |
|------------------------------------|-------------------------------------------------------------------------------------------------------|
| `UI → Eng`                         | `params_dict` is JSON; serialization is canonical (sorted keys).                                      |
| `Eng → Strat`                      | `bars` is an immutable `Sequence[Bar]` (frozen pydantic). The same object reference yields the same signals. |
| `Strat → Eng` (signals)            | Strategy is pure — no `random`, no `time.time()`, no module-level state. ADR-0004 mandates this.      |
| `Eng → Adapt`                      | Fill rule is fixed (next-bar open). Cost params are explicit.                                         |
| `Adapt → Met`                      | Floating-point reduction order is deterministic (single-threaded; same input order ⇒ same sum).      |

A re-run with the same `(strategy_id, params_dict, bars, costs)` produces a byte-identical `BacktestResult`. This is testable: hash the JSON output and assert across runs.

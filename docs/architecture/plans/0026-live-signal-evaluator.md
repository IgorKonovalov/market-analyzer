# 0026 — Live-signal evaluator

> **Status:** draft
> **Created:** 2026-05-30
> **Owner skill(s):** `backtester` (phase 1), `dev` (phase 2)
> **Related ADRs:** [ADR-0004](../adrs/0004-strategy-interface.md) (the pure `generate_signals` contract this evaluates), [ADR-0029](0029-advisory-recommendation-boundary.md) (the advisor will consume this primitive — but the evaluator itself stays a condition-reporter, not an advisor), [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md) (the MCP tool surface), [ADR-0007](../adrs/0007-market-data-provider.md) (bars come through the provider Protocol), [ADR-0018](../adrs/0018-backtest-result-schema.md) (the determinism contract — this plan documents the one wall-clock exception a *live* read carries)

## TL;DR

Today a strategy can only be evaluated *historically* (`run_backtest` over a closed window). There is no way to ask "what does strategy X say on the **current** bar of symbol Y, right now." This plan builds that missing primitive: a pure evaluation core in `backtest/` that runs a strategy's `generate_signals` over fresh bars and reports the **current signal state** — implied position (flat/long), the most recent signal (kind + bar + timestamp + reason), bars-since, and a "fresh signal fired on the last closed bar" flag — plus an `evaluate_signals` MCP tool that wires it to the data provider. First user-visible behavior: the agent calls `evaluate_signals(strategy_id="rsi", symbol="AAPL", timeframe="1d", range_start=..., params={...})` and gets back "currently flat; last signal exit_long 6 bars ago; no fresh signal on the last closed bar (2026-05-29)" — a factual condition read, not a recommendation. v1 is one strategy × one symbol; multi-symbol/multi-strategy fan-out and the in-app view are deliberately deferred to follow-up plans.

## Context & problem

The user's stated direction (2026-05-30) is to grow the app toward live signals, forecasting, and (advisory) trade recommendations. The two foundational decisions are captured in [ADR-0029](0029-advisory-recommendation-boundary.md) (advisor may recommend, not act) and [ADR-0030](0030-forecasting-subsystem.md) (causal forecasting). Both of those consume a primitive the codebase does not yet have: **the evaluation of a strategy against the current bar.**

What exists today (verified against the tree):

- Strategies are pure modules exporting `META`, `Params`, and `generate_signals(bars, params) -> Sequence[Signal]` ([ADR-0004](../adrs/0004-strategy-interface.md)); six are shipped (`rsi`, `macd`, `bollinger`, `donchian`, `ema_cross`, `supertrend`). `discover()` in `contracts/strategy.py` returns them keyed by `META.id`.
- A `Signal` is `{bar_index, kind (enter_long|exit_long), reason}` (`contracts/strategy.py`).
- `backtest/adapter.py::signals_to_trades` interprets a signal stream into closed `Trade`s under the execution-timing convention: **a signal at bar `i` is a decision at the close of `i`, executed at the open of `i+1`.**
- `run_backtest` (the MCP tool) composes `discover()[id]` → fetch bars → engine → persist → publish — entirely historical.

Two facts make "evaluate the current bar" a distinct primitive rather than a thin reuse of the backtest path, and they are the crux of this plan:

1. **The backtest adapter *drops* a signal on the last bar.** `signals_to_trades` ignores any signal with `bar_index > len(bars) - 2`, because in a historical series there is no `i+1` open to execute against. But in a *live* read, the bar after the last closed bar is simply *the future* — a fresh `enter_long` on the last closed bar is exactly the actionable "act at the next open when it arrives" signal the user cares about. Reusing the adapter would silently discard the single most important output. The evaluator must treat the last-closed-bar signal as live, not drop it.
2. **A live read is intrinsically wall-clock-dependent, and that must be isolated.** `generate_signals` is pure and deterministic given bars (the no-lookahead/determinism non-negotiables, [ADR-0018](../adrs/0018-backtest-result-schema.md)). The *only* time-dependence a live evaluation adds is deciding **which bars count as "closed" right now** — i.e. excluding the latest, still-forming bar. That is the same kind of now-only read [Plan 0019](0019-live-quote.md)'s `get_quote` is (it rejects `as_of`). The evaluator confines its wall-clock use to closed-bar selection and rejects any `as_of`-style parameter, so the financially-meaningful computation stays pure.

## Decision

We will add a **pure evaluation core** `backtest/live_signal.py::evaluate_signals(strategy_module, bars, *, now) -> SignalEvaluation` (`backtester`-owned, sibling to `signals_to_trades`) and an **`evaluate_signals` MCP tool** (`dev`-owned, in `api/mcp_tools/`) that resolves the strategy via `discover()`, fetches fresh bars through the provider/backfill path, and calls the core.

The core: takes the full fetched series and the current wall-clock instant `now` (injected as a parameter so the core itself stays testable and deterministic), **excludes a not-yet-closed latest bar** (a bar is closed when `event_ts + bar_duration <= now`), runs `generate_signals` over the **closed** bars only, then folds the resulting signal stream through a flat/long state machine to derive the **current implied position**, the **most recent signal**, **bars-since-last-signal**, and a **`fresh_signal`** flag (true iff the last signal's `bar_index` equals the last closed bar's index). It returns a `SignalEvaluation` pydantic model. It does **not** apply the `+1` execution offset for position state (that offset is about *price*, not *whether you hold*), and it does **not** drop the last-bar signal.

The tool: `evaluate_signals(strategy_id, symbol, timeframe, range_start, params)` — `timeframe` restricted to the currently-supported `Literal["1d", "1h"]`; `range_start` is the warm-up lookback (the caller must request enough history for the strategy's indicators to warm up); there is **no `range_end`** — the read always runs to the latest available bar (a now-read). It validates that `timeframe` is in the strategy's `META.timeframes`, validates `params` against the strategy's `Params` model, fetches bars via the same provider/coordinator path `get_ohlcv` uses (fetch-on-miss so the latest bar is fresh), calls the core with `now = datetime.now(UTC)`, and returns the `SignalEvaluation`. It rejects any `as_of` parameter and is read-only — no SSE publish, no persistence, no recommendation language.

This stays strictly a **condition-reporter** ([ADR-0029](0029-advisory-recommendation-boundary.md)): it reports what the strategy's signals *are*, never "buy/sell." Turning a signal into a recommendation is the advisor's job, a later plan. We rejected reusing `signals_to_trades` (it drops the live-critical last-bar signal and bakes in the historical execution offset) and rejected a multi-symbol/multi-strategy v1 (fan-out semantics and perf belong in a follow-up once the single primitive is proven).

## Architecture diagram

```mermaid
flowchart LR
    subgraph agent["MCP client (agent)"]
        call["evaluate_signals(strategy_id,<br/>symbol, timeframe, range_start, params)"]
    end
    subgraph sidecar["Python sidecar"]
        tool["evaluate_signals tool<br/>(api/mcp_tools/ — dev)"]
        disc["discover()[strategy_id]<br/>(contracts/strategy.py)"]
        prov["provider / backfill coordinator<br/>(fetch-on-miss, ADR-0007)"]
        core["evaluate_signals(core)<br/>backtest/live_signal.py — backtester<br/>= close-bar filter (now)<br/>+ generate_signals over closed bars<br/>+ flat/long replay<br/>+ freshness"]
        tool --> disc
        tool --> prov
        tool --> core
        disc --> core
        prov --> core
    end
    subgraph ext["External"]
        yahoo["Yahoo OHLCV"]
    end
    prov --> yahoo
    core --> result["SignalEvaluation<br/>(position, last_signal, fresh_signal,<br/>forming-bar flag)"]
    result --> call
```

## Implementation phases

### Phase 1 — Evaluation core + `SignalEvaluation` model

- **Owner skill:** `backtester`
- **What:** A pure `evaluate_signals(strategy_module, bars, *, now)` in `src/market_analyser/backtest/live_signal.py` and the `SignalEvaluation` pydantic model (in `backtest/types.py` or a new `backtest/live_signal.py`-local model), with the closed-bar filter, the flat/long replay, and the freshness flag. No data fetching, no MCP — a pure function over a supplied series + injected `now`.
- **Files touched:**
  - New `src/market_analyser/backtest/live_signal.py` (the core + a private `_bar_duration(timeframe)` helper covering `1d`/`1h`).
  - `src/market_analyser/backtest/types.py` (add `SignalEvaluation`) or keep the model local to `live_signal.py`.
  - New `tests/backtest/test_live_signal.py`.
- **Done when:**
  - `evaluate_signals(rsi_module, bars, now=...)` over a fixture where RSI is currently oversold-and-just-entered returns `current_position="long"`, `last_signal.kind="enter_long"` with `bar_index` = the last closed bar, `fresh_signal=True`, `bars_since_last_signal=0`.
  - A fixture where the last signal was several bars back returns `fresh_signal=False`, the correct `bars_since_last_signal`, and the position implied by folding the full signal stream.
  - A fixture whose latest bar is **still forming** (`event_ts + duration > now`) excludes that bar: `evaluated_through_ts` equals the last *closed* bar's `event_ts`, `latest_bar_excluded_as_forming=True`, and the signal computation does not see the forming bar. A fixture whose latest bar **is** closed sets `latest_bar_excluded_as_forming=False`.
  - A fixture with too few bars for indicator warm-up (empty signal stream) returns `current_position="flat"`, `last_signal=None`, `fresh_signal=False` — it does **not** raise.
  - A signal on the **last closed bar** is reported (NOT dropped) — the spec asserts the divergence from `signals_to_trades`, which would drop it. (This is the load-bearing behavioral claim of the plan; the spec must assert the value, not just that the function runs.)
  - The core is pure: same `(strategy_module, bars, now)` in → same `SignalEvaluation` out; no wall-clock read inside the core (it takes `now` as a param). `mypy --strict` clean.

### Phase 2 — `evaluate_signals` MCP tool

- **Owner skill:** `dev`
- **What:** A `register_evaluate_signals(server, *, provider, backfill_coordinator)` module under `api/mcp_tools/` (matching the Plan 0017 `register_*` pattern), wired into `mcp_app.create_mcp_components`. It resolves the strategy via `discover()`, validates timeframe ∈ `META.timeframes` and `params` against the strategy's `Params`, fetches bars through the provider/coordinator (fetch-on-miss, as `get_ohlcv` does), calls the phase-1 core with `now = datetime.now(UTC)`, and returns the `SignalEvaluation`.
- **Files touched:**
  - New `src/market_analyser/api/mcp_tools/evaluate_signals.py`.
  - `src/market_analyser/api/mcp_app.py` (one `register_evaluate_signals(...)` call + import — the thin-hub pattern from Plan 0017).
  - New `tests/api/test_evaluate_signals_tool.py`.
- **Done when:**
  - `server.call_tool("evaluate_signals", {strategy_id, symbol, timeframe, range_start, params})` over a seeded fake provider returns a `SignalEvaluation` whose fields match the phase-1 core run on the same bars.
  - An unknown `strategy_id` raises a `ValueError` (caller bug → surfaced as a tool error), not a 500.
  - A `timeframe` not in the strategy's `META.timeframes` raises `ValueError` with a message naming the supported set.
  - A `params` dict that violates the strategy's `Params` model (bad type / out-of-range / extra key, given `extra="forbid"`) raises a validation error at the boundary — it does not silently coerce.
  - Passing an `as_of` (or `range_end`) key is rejected — the tool's signature does not accept it, so an extra key fails under the strict input model.
  - The tool performs **no** SSE publish and **no** persistence (read-only); a test asserts the event bus is untouched.
  - `uv run pytest tests/api/ tests/backtest/` passes with no new skips/xfails; `mypy --strict` clean; the registered toolset still lists the pre-existing tools (no regression to the Plan 0017 hub).

## Data shapes

```python
# illustrative — not the final interface
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

class EvaluatedSignal(BaseModel):
    kind: Literal["enter_long", "exit_long"]
    bar_index: int          # index within the CLOSED-bar series
    event_ts: datetime      # the closed bar's timestamp (UTC)
    reason: str | None

class SignalEvaluation(BaseModel):
    strategy_id: str
    symbol: str
    timeframe: Literal["1d", "1h"]
    evaluated_through_ts: datetime           # event_ts of the last CLOSED bar used
    closed_bar_count: int                    # bars actually fed to generate_signals
    latest_bar_excluded_as_forming: bool     # True if a still-forming bar was dropped
    current_position: Literal["flat", "long"]  # implied by folding the signal stream
    last_signal: EvaluatedSignal | None      # most recent signal, or None if none fired
    bars_since_last_signal: int | None       # 0 == fired on the last closed bar
    fresh_signal: bool                       # last_signal fired on the last closed bar
```

Note: `bar_index` and `bars_since_last_signal` are relative to the **closed-bar** series (the forming bar, if any, is excluded before indexing), so they are stable across an intrabar re-call as long as no new bar has closed.

## Risks & open questions

- **Risk: wall-clock dependence leaks into the deterministic path.** Mitigation: the core takes `now` as a parameter and reads no clock itself; only the *tool* reads `datetime.now(UTC)`. `generate_signals` stays pure. This mirrors [Plan 0019](0019-live-quote.md)'s `get_quote` (a now-read that rejects `as_of`). The phase-1 purity spec pins this.
- **Risk: forming-bar detection needs bar-duration knowledge.** Today `_bar_duration` covers only `1d`/`1h` (the full `SUPPORTED_TIMEFRAMES`). When [Plan 0025](0025-timeframe-expansion.md) lands its `data/timeframes.py` registry, `_bar_duration` should defer to it rather than carry a private map. Captured as a followup; not a blocker (timeframe is `Literal["1d","1h"]` here).
- **Risk: the last-closed-bar signal gets silently dropped** (the exact bug a naive reuse of `signals_to_trades` would introduce). Mitigation: phase-1 done-when asserts the divergence explicitly with a fixture.
- **Risk: insufficient warm-up history yields an empty signal stream and reads as a bug.** Mitigation: the core treats "no signals" as a valid `flat` / `last_signal=None` result and never raises; the caller controls warm-up via `range_start`. Open question: should the tool warn when `closed_bar_count` is below a strategy-declared minimum? Deferred — strategies don't declare a minimum today; revisit if it bites.
- **Open question: timezone of the forming-bar boundary for daily bars.** `event_ts` is UTC; a daily bar's "close" is exchange-dependent, not 24h-after-open in all venues. v1 uses `event_ts + 1d <= now_utc` as the closed test, which is correct for the UTC-stamped daily bars the data layer produces today. If [Plan 0025](0025-timeframe-expansion.md)'s registry introduces session-aware durations, revisit. Noted, not solved.

## What this plan does NOT do

- **No recommendations.** It reports signal *conditions*, never buy/sell. The advisor that turns this into a recommendation is [ADR-0029](0029-advisory-recommendation-boundary.md)'s separate plan.
- **No fan-out.** One strategy × one symbol only. Multi-symbol watchlist scans ("which symbols have a fresh signal") and multi-strategy matrices are follow-up plans on top of this primitive.
- **No UI.** The in-app live-signal view is a deferred `ui-builder` plan; this plan's deliverable is the engine + MCP tool, so agent-driven use works immediately. The `SignalEvaluation` model is JSON-serializable so the future view + `gen-types` can consume it unchanged.
- **No forecasting and no new strategies.** It evaluates the existing six strategies as-is; [ADR-0030](0030-forecasting-subsystem.md) and `strategy-author` work are separate.
- **No SSE / persistence.** A live read is ephemeral; nothing is published or stored.
- **No short signals.** `SignalKind` is long-only today; `current_position` is `flat`/`long`. Shorts are a contract-level change out of scope here.

## Followups (after this lands)

- When [Plan 0025](0025-timeframe-expansion.md) lands, point `_bar_duration` at the `data/timeframes.py` registry and widen the tool's `timeframe` Literal accordingly.
- Multi-symbol scan / multi-strategy matrix wrapper plan (the fan-out this primitive enables).
- The in-app live-signal view (`ui-builder`), consuming `SignalEvaluation` via a renderer route + `gen-types`.
- (Architect populates further items from the Mode 4 review at close.)

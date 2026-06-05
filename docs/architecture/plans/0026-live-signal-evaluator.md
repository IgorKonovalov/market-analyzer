# 0026 — Live-signal evaluator

> **Status:** approved
> **Created:** 2026-05-30
> **Approved:** 2026-06-02
> **Owner skill(s):** `backtester` (phase 1), `dev` (phase 2), `ui-builder` (phase 3)
> **Related ADRs:** [ADR-0004](../adrs/0004-strategy-interface.md) (the pure `generate_signals` contract this evaluates), [ADR-0029](../adrs/0029-advisory-recommendation-boundary.md) (the advisor will consume this primitive — but the evaluator itself stays a condition-reporter, not an advisor), [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md) (the MCP tool surface), [ADR-0007](../adrs/0007-market-data-provider.md) (bars come through the provider Protocol), [ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) (the agent-primary / reactive-render model the UI phase follows — no form), [ADR-0017](../adrs/0017-live-ui-updates-via-sse.md) (the SSE event stream the viewer panel subscribes to), [ADR-0018](../adrs/0018-backtest-result-schema.md) (the determinism contract — this plan documents the one wall-clock exception a *live* read carries)

## TL;DR

Today a strategy can only be evaluated *historically* (`run_backtest` over a closed window). There is no way to ask "what does strategy X say on the **current** bar of symbol Y, right now." This plan builds that missing primitive: a pure evaluation core in `backtest/` that runs a strategy's `generate_signals` over fresh bars and reports the **current signal state** — implied position (flat/long), the most recent signal (kind + bar + timestamp + reason), bars-since, and a "fresh signal fired on the last closed bar" flag — plus an `evaluate_signals` MCP tool that wires it to the data provider. First user-visible behavior: the agent calls `evaluate_signals(strategy_id="rsi", symbol="AAPL", timeframe="1d", range_start=..., params={...})`, gets back "currently flat; last signal exit_long 6 bars ago; no fresh signal on the last closed bar (2026-05-29)" — a factual condition read, not a recommendation — **and the same evaluation appears in a live panel in the Electron viewer**, which renders it from a `signal.evaluated v1` SSE event (the agent-primary, reactive-render model of [ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) — no form; the panel reflects what the agent evaluated). v1 is one strategy × one symbol; multi-symbol/multi-strategy fan-out is deliberately deferred to a follow-up plan.

## Context & problem

The user's stated direction (2026-05-30) is to grow the app toward live signals, forecasting, and (advisory) trade recommendations. The two foundational decisions are captured in [ADR-0029](../adrs/0029-advisory-recommendation-boundary.md) (advisor may recommend, not act) and [ADR-0030](../adrs/0030-forecasting-subsystem.md) (causal forecasting). Both of those consume a primitive the codebase does not yet have: **the evaluation of a strategy against the current bar.**

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

The tool: `evaluate_signals(strategy_id, symbol, timeframe, range_start, params)` — `timeframe` accepts the full supported set the data layer now serves (`15m`/`1h`/`4h`/`1d`/`1w`), validated against the `data/timeframes.py` registry ([Plan 0025](0025-timeframe-expansion.md), landed); `range_start` is the warm-up lookback (the caller must request enough history for the strategy's indicators to warm up); there is **no `range_end`** — the read always runs to the latest available bar (a now-read). It validates that `timeframe` is in the strategy's `META.timeframes`, validates `params` against the strategy's `Params` model, fetches bars via the same provider/coordinator path `get_ohlcv` uses (fetch-on-miss so the latest bar is fresh), calls the core with `now = datetime.now(UTC)`, returns the `SignalEvaluation` to the MCP caller, **and publishes it as a `signal.evaluated v1` SSE event** ([ADR-0017](../adrs/0017-live-ui-updates-via-sse.md)) so the viewer panel can render it live. It accepts no `as_of`/`range_end` parameter — the read is always a now-read (a stray such key is ignored at the FastMCP boundary, never used; see the phase-2 [amendment](#amendments)); it persists nothing (a live read is ephemeral — the SSE event carries the full small payload inline, no GET route, no DB row) and emits no recommendation language. The MCP return value is the reliable contract; the SSE event is the opportunistic live nudge to a connected viewer (a no-op if none is connected, exactly like `show_chart`).

The viewer (phase 3): a **reactive panel** that subscribes to `signal.evaluated v1` on the existing event stream and renders the latest evaluation — strategy/symbol/timeframe, current position, last signal + bars-since, the `fresh_signal` flag, and the forming-bar/`evaluated_through_ts` honesty fields. It has **no form and no controls** — per [ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) the agent drives evaluation; the panel reflects it. It presents conditions, never a buy/sell call.

This stays strictly a **condition-reporter** ([ADR-0029](../adrs/0029-advisory-recommendation-boundary.md)): it reports what the strategy's signals *are*, never "buy/sell." Turning a signal into a recommendation is the advisor's job, a later plan. We rejected reusing `signals_to_trades` (it drops the live-critical last-bar signal and bakes in the historical execution offset); we rejected a form-driven renderer route for the view (it cuts against [ADR-0015](../adrs/0015-claude-code-primary-control-surface.md)'s shrink-the-control-surface grain — the reactive SSE panel matches `show_chart`/`run.completed`); and we rejected a multi-symbol/multi-strategy v1 (fan-out semantics and perf belong in a follow-up once the single primitive is proven).

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
    tool -->|publish signal.evaluated v1| bus["EventBus / SSE /events<br/>(ADR-0017)"]
    subgraph viewer["Electron viewer (ui-builder)"]
        panel["Live-signal panel<br/>(reactive; no form)<br/>subscribes signal.evaluated v1"]
    end
    bus --> panel
```

## Implementation phases

### Phase 1 — Evaluation core + `SignalEvaluation` model

- **Owner skill:** `backtester`
- **What:** A pure `evaluate_signals(strategy_module, bars, *, now)` in `src/market_analyser/backtest/live_signal.py` and the `SignalEvaluation` pydantic model (in `backtest/types.py` or a new `backtest/live_signal.py`-local model), with the closed-bar filter, the flat/long replay, and the freshness flag. No data fetching, no MCP — a pure function over a supplied series + injected `now`. Bar duration for the closed-bar test comes from the `data/timeframes.py` registry (`TimeframeSpec.duration`, [Plan 0025](0025-timeframe-expansion.md)), not a private map.
- **Files touched:**
  - New `src/market_analyser/backtest/live_signal.py` (the core; reads bar duration from the `data/timeframes.py` registry rather than hardcoding it).
  - `src/market_analyser/backtest/types.py` (add `SignalEvaluation`) or keep the model local to `live_signal.py`.
  - New `tests/backtest/test_live_signal.py`.
- **Done when:**
  - `evaluate_signals(rsi_module, bars, now=...)` over a fixture where RSI is currently oversold-and-just-entered returns `current_position="long"`, `last_signal.kind="enter_long"` with `bar_index` = the last closed bar, `fresh_signal=True`, `bars_since_last_signal=0`.
  - A fixture where the last signal was several bars back returns `fresh_signal=False`, the correct `bars_since_last_signal`, and the position implied by folding the full signal stream.
  - A fixture whose latest bar is **still forming** (`event_ts + duration > now`, where `duration` comes from the `data/timeframes.py` registry) excludes that bar: `evaluated_through_ts` equals the last *closed* bar's `event_ts`, `latest_bar_excluded_as_forming=True`, and the signal computation does not see the forming bar. A fixture whose latest bar **is** closed sets `latest_bar_excluded_as_forming=False`. Cover both a daily (`1d`) and a sub-daily (`1h` or `15m`) cadence so the registry-driven duration is exercised, not just the day case.
  - A fixture with too few bars for indicator warm-up (empty signal stream) returns `current_position="flat"`, `last_signal=None`, `fresh_signal=False` — it does **not** raise.
  - A signal on the **last closed bar** is reported (NOT dropped) — the spec asserts the divergence from `signals_to_trades`, which would drop it. (This is the load-bearing behavioral claim of the plan; the spec must assert the value, not just that the function runs.)
  - The core is pure: same `(strategy_module, bars, now)` in → same `SignalEvaluation` out; no wall-clock read inside the core (it takes `now` as a param). `mypy --strict` clean.

### Phase 2 — `evaluate_signals` MCP tool + `signal.evaluated v1` SSE event

- **Owner skill:** `dev`
- **What:** A `register_evaluate_signals(server, *, provider, backfill_coordinator, event_bus)` module under `api/mcp_tools/` (matching the Plan 0017 `register_*` pattern), wired into `mcp_app.create_mcp_components`. It resolves the strategy via `discover()`, validates timeframe ∈ `META.timeframes` and `params` against the strategy's `Params`, fetches bars through the provider/coordinator (fetch-on-miss, as `get_ohlcv` does), calls the phase-1 core with `now = datetime.now(UTC)`, returns the `SignalEvaluation` to the caller, and publishes a `signal.evaluated v1` event on the bus. Defines `SignalEvaluatedPayloadV1` in the neutral `events/` core (a thin envelope: `VERSION` + the `SignalEvaluation` payload inline). **Amended 2026-06-05** — the renderer-side TS for the payload is NOT produced here (see [Amendments](#amendments)): event payloads are not OpenAPI-route-bound, so `gen-types` cannot emit them; the TS mirror + parity guard is phase-3/`ui-builder` work, consistent with how every other event payload's TS lives in `desktop/renderer/types/events.ts`.
- **Files touched:**
  - New `src/market_analyser/api/mcp_tools/evaluate_signals.py`.
  - `src/market_analyser/events/__init__.py` — add `SignalEvaluatedPayloadV1`; register the `signal.evaluated` event type in `TYPE_REGISTRY` the way the chart events are registered. **Resolved:** [Plan 0028](done/0028-data-layer-boundary-hardening.md) ([ADR-0032](../adrs/0032-data-layer-no-api-dependency.md)) has landed, relocating the event bus/schema to the neutral `events/` core, so the payload goes there (no longer `api/events/`). Note this makes the `events` core depend on `backtest.types` (the inline-payload design's cost — see [Risks](#risks--open-questions)).
  - `src/market_analyser/api/mcp_app.py` (one `register_evaluate_signals(...)` call + import — the thin-hub pattern from Plan 0017; threads `provider`, `backfill_coordinator`, `event_bus`).
  - New `tests/api/test_evaluate_signals_tool.py`.
  - **No renderer-TS file here** (amended) — `gen-types` is OpenAPI-route-driven and event payloads are not route-bound, so it cannot emit `SignalEvaluatedPayloadV1`/`SignalEvaluation`. Phase 2 only verifies `gen-types --check` stays no-drift (a no-op, since no route is added); the TS mirror is phase-3 work.
- **Done when:**
  - `server.call_tool("evaluate_signals", {strategy_id, symbol, timeframe, range_start, params})` over a seeded fake provider returns a `SignalEvaluation` whose fields match the phase-1 core run on the same bars.
  - The same call publishes **exactly one** `signal.evaluated v1` envelope carrying that `SignalEvaluation` payload inline; a test subscribes to the bus and asserts the published payload equals the returned value. (Published once on success; not at all on any raise above the publish — same discipline as `run.completed`.)
  - An unknown `strategy_id` raises a `ValueError` (caller bug → surfaced as a tool error), not a 500, and publishes nothing.
  - A `timeframe` not in the strategy's `META.timeframes` raises `ValueError` with a message naming the supported set, and publishes nothing.
  - A `params` dict that violates the strategy's `Params` model (bad type / out-of-range / extra key, given `extra="forbid"`) raises a validation error at the boundary — it does not silently coerce — and publishes nothing.
  - The read is structurally a now-read with no lookahead-replay path: the tool declares no `as_of`/`range_end` parameter and always evaluates against `now = datetime.now(UTC)`. **Amended 2026-06-05 (was "an extra key is rejected"):** FastMCP's generated input model does not enforce `extra="forbid"` (its `ArgModelBase` config omits it and exposes no per-tool hook), so a stray `as_of`/`range_end` key is silently *ignored*, not rejected. The achievable guarantee — and what the test asserts — is the safety property: a call **with** an `as_of`/`range_end` key returns the **same** `SignalEvaluation` as the call without it (the key cannot influence the read). True fail-loud rejection would require making all tool input models strict, a cross-cutting change tracked as a [followup](#followups-after-this-lands).
  - The tool persists nothing (no DB row, no `runs/` artifact) — a test asserts no persistence side-effect.
  - `gen-types --check` reports **no drift** — a no-op here (no HTTP route surfaces the new payload, so the OpenAPI dump is unchanged); the renderer TS for the payload is added in phase 3, not regenerated here.
  - `uv run pytest tests/api/ tests/backtest/` passes with no new skips/xfails; `mypy --strict` clean; the registered toolset still lists the pre-existing tools (no regression to the Plan 0017 hub).

### Phase 3 — Reactive live-signal panel in the viewer

- **Owner skill:** `ui-builder`
- **What:** A React panel in the Electron renderer that subscribes to `signal.evaluated v1` on the existing event stream (`useEventStream`) and renders the latest `SignalEvaluation` — strategy/symbol/timeframe header, current position (flat/long), last signal (kind + bars-since + timestamp + reason), the `fresh_signal` flag, and the honesty fields (`evaluated_through_ts`, `latest_bar_excluded_as_forming`). No form, no controls — the panel reflects what the agent evaluated ([ADR-0015](../adrs/0015-claude-code-primary-control-surface.md)). It consumes the `SignalEvaluatedPayloadV1` shape from the renderer's hand-mirrored `events.ts` (pinned to the pydantic model by the `events.test.ts` parity guard — the project's established mechanism for SSE payload types, since they are not OpenAPI-route-bound and `gen-types` cannot emit them); no new sidecar fetch (the payload is inline in the event).
- **Files touched:**
  - `desktop/renderer/types/events.ts` — **add (amended; moved here from phase 2)** the `SignalEvaluatedPayloadV1` interface plus the nested `SignalEvaluation` / `EvaluatedSignal` shapes and a `SignalEvaluatedEnvelope` type, mirroring the pydantic models in `src/market_analyser/events/__init__.py` + `backtest/types.py`, following the existing hand-mirror pattern.
  - `desktop/renderer/types/events.test.ts` — **extend (amended)** the parity guard to dump and assert the new payload's fields / required set / literal sets (`current_position`, `kind`) against `model_json_schema()`, the same way the chart/run payloads are checked.
  - New `desktop/renderer/views/LiveSignalView.tsx` (+ `.module.css`) or a panel within an existing view — implementer's call per the renderer's current layout.
  - The renderer's event-stream handler / route registration (wherever `chart.show` and `run.completed` are dispatched) to route `signal.evaluated`.
  - New `desktop/renderer/views/LiveSignalView.test.tsx`.
- **Done when:**
  - With the renderer pointed at a sidecar (or a mocked event source), publishing a `signal.evaluated v1` event causes the panel to render the evaluation's fields; a Jest test drives a fixture envelope through the handler and asserts the rendered position, last-signal line, and `fresh_signal` state.
  - The empty/just-opened state (no event yet) renders a clear "no evaluation yet — ask the agent to evaluate a strategy" placeholder, not a broken/empty card.
  - A `latest_bar_excluded_as_forming=true` evaluation visibly surfaces that the latest bar was still forming (the honesty field is shown, not hidden), and a `fresh_signal=true` evaluation is visually distinct from a stale one.
  - The panel renders **conditions only** — no buy/sell/recommendation language anywhere in the component or its copy.
  - The `events.test.ts` parity guard passes for the new `SignalEvaluatedPayloadV1` (+ nested `SignalEvaluation`/`EvaluatedSignal`) — the TS mirror matches the pydantic `model_json_schema()` field names, required set, and literal sets. (This is the "no hand-rolled shape" guarantee in this codebase: the mirror cannot drift from the model without failing this test.)
  - `pnpm --filter ... test` (renderer Jest) passes with no new skips; the renderer typechecks (`tsc --noEmit`) against the `events.ts` types.

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
    timeframe: str                           # a supported timeframe (data/timeframes.py: 15m/1h/4h/1d/1w)
    evaluated_through_ts: datetime           # event_ts of the last CLOSED bar used
    closed_bar_count: int                    # bars actually fed to generate_signals
    latest_bar_excluded_as_forming: bool     # True if a still-forming bar was dropped
    current_position: Literal["flat", "long"]  # implied by folding the signal stream
    last_signal: EvaluatedSignal | None      # most recent signal, or None if none fired
    bars_since_last_signal: int | None       # 0 == fired on the last closed bar
    fresh_signal: bool                       # last_signal fired on the last closed bar
```

```python
# illustrative — the SSE envelope phase 2 publishes (mirrors ChartShowPayloadV1)
class SignalEvaluatedPayloadV1(BaseModel):
    VERSION = 1                      # class-level, as the chart payloads carry it
    evaluation: SignalEvaluation     # the full result, inline (small + ephemeral)
# published as event type "signal.evaluated"; the viewer subscribes and renders.
```

Note: `bar_index` and `bars_since_last_signal` are relative to the **closed-bar** series (the forming bar, if any, is excluded before indexing), so they are stable across an intrabar re-call as long as no new bar has closed. The full `SignalEvaluation` rides inline in the SSE event (it is small and not persisted), so the viewer needs no follow-up fetch — unlike `run.completed`, which carries a summary and the renderer fetches the large persisted `BacktestResult` via a GET route.

## Risks & open questions

- **Risk: wall-clock dependence leaks into the deterministic path.** Mitigation: the core takes `now` as a parameter and reads no clock itself; only the *tool* reads `datetime.now(UTC)`. `generate_signals` stays pure. This mirrors [Plan 0019](0019-live-quote.md)'s `get_quote` (a now-read that rejects `as_of`). The phase-1 purity spec pins this.
- **Risk: forming-bar detection needs bar-duration knowledge.** Resolved by reading `TimeframeSpec.duration` from the `data/timeframes.py` registry ([Plan 0025](0025-timeframe-expansion.md), landed) — no private duration map. The tool accepts the full supported set (`15m`/`1h`/`4h`/`1d`/`1w`); 4h is the in-house-resampled cadence, so confirm its `TimeframeSpec.duration` is the resampled `4h`, not the `1h` base, when computing the closed-bar boundary.
- **Risk: the last-closed-bar signal gets silently dropped** (the exact bug a naive reuse of `signals_to_trades` would introduce). Mitigation: phase-1 done-when asserts the divergence explicitly with a fixture.
- **Risk: insufficient warm-up history yields an empty signal stream and reads as a bug.** Mitigation: the core treats "no signals" as a valid `flat` / `last_signal=None` result and never raises; the caller controls warm-up via `range_start`. Open question: should the tool warn when `closed_bar_count` is below a strategy-declared minimum? Deferred — strategies don't declare a minimum today; revisit if it bites.
- **Open question: timezone of the forming-bar boundary for session-anchored bars.** `event_ts` is UTC; a daily/weekly bar's "close" is exchange-dependent, not a fixed span after open in all venues, and the same caveat now applies to the intraday cadences the widened scope admits. v1 uses `event_ts + TimeframeSpec.duration <= now_utc` as the closed test, which is correct for the UTC-stamped bars the data layer produces today. The `data/timeframes.py` registry carries fixed `timedelta` durations, not session-aware ones; if session-aware close times are introduced later, revisit. Noted, not solved.
- **Risk (amended 2026-06-05): the `events` core now depends on `backtest.types`.** Carrying the full `SignalEvaluation` inline in the payload (the deliberate no-GET-route design) means `events/__init__.py` imports `SignalEvaluation` from `backtest.types`, so the previously leaf-level events core gains a `backtest` dependency. Verified acyclic (no `backtest` module imports `events`; `data → events → backtest.types → contracts → data.types` has no back-edge). Accepted as the inline-payload cost; flagged for the Mode 4 review to confirm the layering is tolerable or to consider relocating `SignalEvaluation` to a more neutral home if it spreads.
- **Risk (amended 2026-06-05): the MCP input boundary does not reject unknown keys.** FastMCP's generated input model omits `extra="forbid"`, so a stray `as_of`/`range_end` is ignored rather than rejected. The anti-lookahead *safety* is unaffected (the tool is structurally a now-read), but the fail-loud nicety is absent. See the amended phase-2 done-when and the strict-input followup.

## What this plan does NOT do

- **No recommendations.** It reports signal *conditions*, never buy/sell. The advisor that turns this into a recommendation is [ADR-0029](../adrs/0029-advisory-recommendation-boundary.md)'s separate plan.
- **No fan-out.** One strategy × one symbol only. Multi-symbol watchlist scans ("which symbols have a fresh signal") and multi-strategy matrices are follow-up plans on top of this primitive. The viewer panel renders a single evaluation, not a grid.
- **No form-driven UI.** The viewer panel is reactive-only — it reflects the agent's evaluation via SSE ([ADR-0015](../adrs/0015-claude-code-primary-control-surface.md)). It has no strategy/symbol picker and no renderer route that triggers evaluation; the agent is the input. (A standalone explorable view was the rejected alternative.)
- **No forecasting and no new strategies.** It evaluates the existing six strategies as-is; [ADR-0030](../adrs/0030-forecasting-subsystem.md) and `strategy-author` work are separate.
- **No persistence.** A live read is ephemeral — nothing is stored (no DB row, no `runs/` artifact). The SSE event is published but not retained; reopening the viewer shows the empty state until the agent evaluates again.
- **No short signals.** `SignalKind` is long-only today; `current_position` is `flat`/`long`. Shorts are a contract-level change out of scope here.

## Followups (after this lands)

- (Resolved at approval — Plan 0025 has landed, so the tool reads durations from `data/timeframes.py` and accepts the full supported set from the outset.)
- Multi-symbol scan / multi-strategy matrix wrapper plan (the fan-out this primitive enables). That plan must decide how the viewer renders many evaluations (a grid) and whether a fan-out scan should publish one `signal.evaluated` per cell or a single batched event — v1's one-publish-per-eval is fine for a single evaluation but would spam the panel under fan-out.
- **Strict MCP tool input (amended 2026-06-05).** If we want stray/unknown tool arguments to fail loud rather than be silently ignored (e.g. an `as_of` on a now-read tool), that is a cross-cutting change to *all* tools' input contracts — overriding FastMCP's `ArgModelBase` to set `extra="forbid"`. It is ADR-sized (it changes the accepted-input contract for ~25 existing tools and could break callers that currently pass extra keys), so it belongs in its own ADR + plan, not bolted onto this one. Until then, now-read tools rely on the structural guarantee (no `as_of` param + always `datetime.now(UTC)`), not boundary rejection.
- (Architect populates further items from the Mode 4 review at close.)

## Amendments

- **2026-06-05 (mid-phase-2, dev escalation).** Two phase-2 done-when items described behavior the platform does not provide; corrected to match reality while preserving the plan's design intent (a pure now-read, no lookahead; a parity-pinned renderer type). Design decisions are unchanged — only the verification wording and the phase that owns the renderer TS.
  1. **`as_of`/`range_end` rejection → safety-property assertion.** FastMCP does not enforce `extra="forbid"` on tool inputs, so unknown keys are ignored, not rejected. The done-when now asserts the achievable, equally-important guarantee: passing such a key does not change the evaluation (the read is structurally a now-read). True boundary rejection is deferred to the cross-cutting strict-input followup above.
  2. **Renderer TS: phase 2 → phase 3.** `gen-types` is OpenAPI-route-driven and cannot emit non-route-bound SSE payloads; the established mechanism is the hand-mirrored `desktop/renderer/types/events.ts` + the `events.test.ts` parity guard, which is `ui-builder`/`desktop` territory. Phase 2 only confirms `gen-types --check` stays no-drift; phase 3 adds the mirror + parity case. Also: Plan 0028 has landed, so the payload lives in the neutral `events/` core, not `api/events/`.

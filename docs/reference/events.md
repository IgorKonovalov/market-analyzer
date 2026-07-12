<!-- GENERATED FILE - DO NOT EDIT BY HAND.
     Regenerate: uv run python -m market_analyser.apiref  (or: pnpm gen:api-docs)
     Rendered from the live sidecar; see Plan 0070 / ADR-0064. -->

# SSE events

The 25 SSE envelope kinds published on `/events`, from the event type registry. Each kind carries a versioned, validated payload.

| Event | Summary |
| --- | --- |
| [`alert.triggered`](#alerttriggered) | `alert.triggered v1` payload (Plan 0060, ADR-0055): a watch's condition transitioned false→true on its latest evaluation. |
| [`chart.highlight`](#charthighlight) | `chart.highlight v1` payload: render markers on a chart. |
| [`chart.show`](#chartshow) | `chart.show v1` payload: render this chart fresh. |
| [`chart.trendlines`](#charttrendlines) | `chart.trendlines v1` payload: layer sloped pattern lines onto the chart already showing `symbol`/`timeframe` (ADR-0059, Plan 0064). |
| [`chart.update`](#chartupdate) | `chart.update v1` payload: apply delta to the chart for symbol+timeframe. |
| [`chart.update_dropped`](#chartupdatedropped) | Synthetic notice emitted when a subscriber's queue overflowed. |
| [`defi.pnl_completed`](#defipnlcompleted) | `defi.pnl_completed v1`: the reconstruction finished. |
| [`defi.pnl_failed`](#defipnlfailed) | `defi.pnl_failed v1`: the reconstruction failed with a typed reason (the scan-failed literal set — same closed vocabulary, same neutrality: the precise auth error reaches the caller through the job's re-raised typed exception, not the wire). |
| [`defi.pnl_started`](#defipnlstarted) | `defi.pnl_started v1`: a wallet P&L reconstruction began (Plan 0035). `wallet` is the **masked** address — the full address never reaches the wire (ADR-0038 discipline). |
| [`defi.scan_completed`](#defiscancompleted) | `defi.scan_completed v1`: the scan finished. |
| [`defi.scan_failed`](#defiscanfailed) | `defi.scan_failed v1`: the scan failed with a typed reason. |
| [`defi.scan_progress`](#defiscanprogress) | `defi.scan_progress v1`: positions decoded for one chain. |
| [`defi.scan_started`](#defiscanstarted) | `defi.scan_started v1`: a wallet scan began. |
| [`forecast.completed`](#forecastcompleted) | `forecast.completed v1` payload (Plan 0037, ADR-0030/ADR-0054): the `forecast` tool produced a multi-horizon forecast. |
| [`ohlcv.backfill_failed`](#ohlcvbackfillfailed) | `ohlcv.backfill_failed v1`: a backfill failed with a typed reason. |
| [`ohlcv.backfill_started`](#ohlcvbackfillstarted) | `ohlcv.backfill_started v1`: a backfill fetch began for symbol+timeframe. Emitted before the upstream call so the renderer can show its spinner. |
| [`ohlcv.backfilled`](#ohlcvbackfilled) | `ohlcv.backfilled v1`: a backfill completed; the cache is now hot for the `[range_start, range_end]` span. |
| [`prediction.screen_completed`](#predictionscreencompleted) | `prediction.screen_completed v1` payload (Plan 0078, ADR-0041/0029): the `find_convergence_opportunities` tool screened a query and produced ranked near-decided opportunities. |
| [`recommendation.completed`](#recommendationcompleted) | `recommendation.completed v1` payload (Plan 0039, ADR-0029): the advisor produced a labeled advisory `Recommendation` for one symbol/timeframe. |
| [`recommendation.scored`](#recommendationscored) | `recommendation.scored v1` payload (Plan 0080, ADR-0075): the scheduled scorer resolved one matured advisory recommendation against realized price. |
| [`regime_forecast.completed`](#regimeforecastcompleted) | `regime_forecast.completed v1` payload (Plan 0077, ADR-0070): the `forecast_regime` tool produced a regime-transition forecast. |
| [`run.completed`](#runcompleted) | `run.completed v1` payload: a backtest/analysis/defi artifact is ready. |
| [`signal.evaluated`](#signalevaluated) | `signal.evaluated v1` payload (Plan 0026): the live signal state of one strategy on one symbol. |
| [`technical_read.completed`](#technicalreadcompleted) | `technical_read.completed v1` payload (Plan 0074, ADR-0068): the `technical_read` tool produced a single-indicator `TechnicalRead`. |
| [`volatility_forecast.completed`](#volatilityforecastcompleted) | `volatility_forecast.completed v1` payload (Plan 0077, ADR-0070): the `forecast_volatility` tool produced a realised-volatility forecast. |

---

## `alert.triggered`

**Version:** 1

`alert.triggered v1` payload (Plan 0060, ADR-0055): a watch's condition
transitioned false→true on its latest evaluation.

**Condition-only** by construction: the triggering fact (`condition`, a
human-readable statement like ``rsi 28.44 < 30``), the numbers behind it
(`values`), and identity/timing fields. Deliberately absent: direction,
action, conviction, side, size — an alert from a background loop must
never cross the ADR-0029 advisory boundary (`extra="forbid"` plus the
schema test in `tests/alerts/test_scheduler.py` pin this).

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer | yes | — |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `kind` | enum["indicator_threshold", "pattern", "strategy_signal"] | yes | — |
| `fired_at` | string (date-time) | yes | — |
| `condition` | string | yes | — |
| `values` | object | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `chart.highlight`

**Version:** 1

`chart.highlight v1` payload: render markers on a chart.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `markers` | array[Marker] | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `chart.show`

**Version:** 1

`chart.show v1` payload: render this chart fresh.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `overlays` | array[OverlaySpec] \| null | no | `None` |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `chart.trendlines`

**Version:** 1

`chart.trendlines v1` payload: layer sloped pattern lines onto the chart
already showing `symbol`/`timeframe` (ADR-0059, Plan 0064).

Trendlines live on their OWN channel — not on `chart.show`/`chart.update` —
so a plain `chart.show` can no longer wipe them and they are recomputed from
current bars (never persisted). Active-chart-gated in the renderer exactly
like `chart.highlight`: the reducer applies it only when `symbol`+`timeframe`
match the chart on screen.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `trendlines` | array[TrendlineSpec] | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `chart.update`

**Version:** 1

`chart.update v1` payload: apply delta to the chart for symbol+timeframe.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `overlays` | array[OverlaySpec] \| null | no | `None` |
| `range_start` | string (date-time) \| null | no | `None` |
| `range_end` | string (date-time) \| null | no | `None` |
| `focus_bar` | string (date-time) \| null | no | `None` |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `chart.update_dropped`

**Version:** 1

Synthetic notice emitted when a subscriber's queue overflowed.

Carries no fields — the renderer's job is to reconcile state when it sees
this, not to consume the contents of the dropped frames.

**Payload fields**

No payload fields.

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `defi.pnl_completed`

**Version:** 1

`defi.pnl_completed v1`: the reconstruction finished. Totals are `None`
whenever any position is `incomplete` — the wire carries the same honesty
the engine does (never a confident partial number, ADR-0036).

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |
| `position_count` | integer | yes | — |
| `incomplete_count` | integer | yes | — |
| `realized_usd` | number \| null | yes | — |
| `unrealized_usd` | number \| null | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `defi.pnl_failed`

**Version:** 1

`defi.pnl_failed v1`: the reconstruction failed with a typed reason (the
scan-failed literal set — same closed vocabulary, same neutrality: the
precise auth error reaches the caller through the job's re-raised typed
exception, not the wire).

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |
| `reason` | enum["rate_limited", "upstream_unavailable", "malformed_response"] | yes | — |
| `message` | string | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `defi.pnl_started`

**Version:** 1

`defi.pnl_started v1`: a wallet P&L reconstruction began (Plan 0035).
`wallet` is the **masked** address — the full address never reaches the
wire (ADR-0038 discipline).

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `defi.scan_completed`

**Version:** 1

`defi.scan_completed v1`: the scan finished. `chains` is the chains where
positions were found; `position_count` is the total across all chains.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |
| `chains` | array[string] | yes | — |
| `position_count` | integer | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `defi.scan_failed`

**Version:** 1

`defi.scan_failed v1`: the scan failed with a typed reason. The literal set
is closed so the renderer can branch on it exhaustively. A missing/invalid
key and any other upstream outage both surface as `upstream_unavailable` on
the wire; the precise auth signal reaches the agent through the scan tool's
re-raised typed exception (phase 4), keeping this neutral payload decoupled
from any one source's error taxonomy.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |
| `reason` | enum["rate_limited", "upstream_unavailable", "malformed_response"] | yes | — |
| `message` | string | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `defi.scan_progress`

**Version:** 1

`defi.scan_progress v1`: positions decoded for one chain. At least one is
emitted between `scan_started` and `scan_completed` for a non-empty wallet.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |
| `chain` | string | yes | — |
| `position_count` | integer | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `defi.scan_started`

**Version:** 1

`defi.scan_started v1`: a wallet scan began. Emitted before the upstream
call so the renderer can show its spinner. `wallet` is the **masked** address
(`0x1234…abcd`) — the full address is never put on the wire (ADR-0038
discipline). `chains` is the set of chains being scanned.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |
| `chains` | array[string] | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `forecast.completed`

**Version:** 1

`forecast.completed v1` payload (Plan 0037, ADR-0030/ADR-0054): the
`forecast` tool produced a multi-horizon forecast.

Like `signal.evaluated` and `recommendation.completed`, the full result
rides inline — small and ephemeral, nothing is persisted for the viewer to
follow-up fetch. One envelope per tool call, however many horizons; a
horizon that failed the baseline gate travels inside its block with null
probabilities (the honest no-edge verdict is carried, never suppressed —
ADR-0030 invariants 3/4). A *condition report* (a calibrated probability),
never a recommendation (ADR-0029).

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `forecast` | MultiHorizonForecastResult | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `ohlcv.backfill_failed`

**Version:** 1

`ohlcv.backfill_failed v1`: a backfill failed with a typed reason. The
literal set is closed so the renderer can branch on it exhaustively.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `reason` | enum["rate_limited", "upstream_unavailable", "unknown_symbol", "history_exceeded"] | yes | — |
| `message` | string | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `ohlcv.backfill_started`

**Version:** 1

`ohlcv.backfill_started v1`: a backfill fetch began for symbol+timeframe.
Emitted before the upstream call so the renderer can show its spinner.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `gaps` | array[GapWindow] | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `ohlcv.backfilled`

**Version:** 1

`ohlcv.backfilled v1`: a backfill completed; the cache is now hot for the
`[range_start, range_end]` span. The renderer refetches `/ohlcv` on this.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `bars_added` | integer | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `prediction.screen_completed`

**Version:** 1

`prediction.screen_completed v1` payload (Plan 0078, ADR-0041/0029): the
`find_convergence_opportunities` tool screened a query and produced ranked
near-decided opportunities.

Like `signal.evaluated` and `technical_read.completed`, the models ride inline —
small and ephemeral, nothing persisted for the viewer to follow-up fetch; the
`opportunities` list is the bounded top-N page the tool returned (ADR-0046), so
the payload stays small. Each `ConvergenceOpportunity` carries its edge math AND
its risk context (resolution risk, liquidity caution, capital-lockup note) and has
no direction/size/action field, so anything this payload validates is safe to
render as *opportunities with their risks attached* and only that — never a buy
call (ADR-0029; the buying is the deferred ADR-0072 pillar). Published only when a
screen yields at least one opportunity — an empty screen has nothing to show, so
it leaves the bus untouched.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `query` | string | yes | — |
| `opportunities` | array[ConvergenceOpportunity] | yes | — |
| `queried_at` | string (date-time) | yes | — |
| `source` | string | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `recommendation.completed`

**Version:** 1

`recommendation.completed v1` payload (Plan 0039, ADR-0029): the advisor
produced a labeled advisory `Recommendation` for one symbol/timeframe.

Like `signal.evaluated` (and unlike `run.completed`), the full model rides
inline: a recommendation is small and ephemeral — nothing is persisted, so
the viewer needs no follow-up fetch. The `Recommendation` model itself
enforces the advisory shape structurally (the `label` can only be
`"advisory"`, a basis always travels with the call), so anything this
payload validates is safe to render as advice-and-only-advice.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `recommendation` | Recommendation | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `recommendation.scored`

**Version:** 1

`recommendation.scored v1` payload (Plan 0080, ADR-0075): the scheduled
scorer resolved one matured advisory recommendation against realized price.

A *fact*, not advice — it reports how a past call turned out (was the stop or
a target hit first, honoring the ticket), never what to do now. Only actually
scored calls emit this: `direction` is `long`/`short` (flat calls are never
scored) and `outcome_class` is `target_hit`/`stopped`/`timeout` (never
`pending`), so every measurement field is non-null. `directional_correct` is
the separate direction axis — a call can be directionally right yet score a
`stopped` loss (ADR-0075). Scalars only, so the wire stays small (ADR-0046)
and the renderer needs no follow-up fetch — the ledger is the source of
truth, this is the live nudge to refresh the track record.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `strategy_id` | string | yes | — |
| `direction` | enum["long", "short"] | yes | — |
| `as_of_bar_ts` | string (date-time) | yes | — |
| `horizon_bars` | integer | yes | — |
| `conviction` | number | yes | — |
| `forecast_prob` | number \| null | yes | — |
| `outcome_class` | enum["target_hit", "stopped", "timeout"] | yes | — |
| `realized_return` | number | yes | — |
| `realized_r` | number | yes | — |
| `directional_correct` | boolean | yes | — |
| `scored_at` | string (date-time) | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `regime_forecast.completed`

**Version:** 1

`regime_forecast.completed v1` payload (Plan 0077, ADR-0070): the `forecast_regime`
tool produced a regime-transition forecast.

The full result rides inline — one envelope per tool call. Distinct from the
crypto-macro nowcast (ADR-0027): per-symbol, technical, predictive. A *condition
report*, never a recommendation (ADR-0029).

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `forecast` | RegimeForecast | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `run.completed`

**Version:** 1

`run.completed v1` payload: a backtest/analysis/defi artifact is ready.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `kind` | enum["backtest", "analysis", "defi"] | yes | — |
| `run_id` | string | yes | — |
| `artifact_path` | string | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `signal.evaluated`

**Version:** 1

`signal.evaluated v1` payload (Plan 0026): the live signal state of one
strategy on one symbol.

Unlike `run.completed` (which carries identifiers and lets the renderer fetch
the large persisted `BacktestResult` via a GET route), this payload rides the
full `SignalEvaluation` inline — it is small and ephemeral (nothing is
persisted), so the viewer needs no follow-up fetch. A *condition report*,
never a recommendation.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `evaluation` | SignalEvaluation | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `technical_read.completed`

**Version:** 1

`technical_read.completed v1` payload (Plan 0074, ADR-0068): the `technical_read`
tool produced a single-indicator `TechnicalRead`.

The full model rides inline — small and ephemeral, nothing persisted for the viewer
to follow-up fetch; one envelope per tool call. The **lesser** advisory tier: a
direction from ONE named indicator, with no conviction and no levels (structural
omission, ADR-0068). Deliberately a **distinct type and a distinct event** from
`recommendation.completed` so a consumer can never conflate the thin single-indicator
read with the corroborated fused call. The `TechnicalRead` model itself has no
conviction/entry/stop/target fields, so anything this payload validates is safe to
render as the lesser tier and only that.

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `read` | TechnicalRead | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

## `volatility_forecast.completed`

**Version:** 1

`volatility_forecast.completed v1` payload (Plan 0077, ADR-0070): the
`forecast_volatility` tool produced a realised-volatility forecast.

The full result rides inline — small and ephemeral, nothing persisted for the viewer
to follow-up fetch; one envelope per tool call. A no-edge verdict travels honestly
(``beats_baseline=False`` with the baseline surfaced). A *condition report* (a
magnitude), never a recommendation and never a price level (ADR-0029).

**Payload fields**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `forecast` | VolatilityForecast | yes | — |

**Source:** [`src/market_analyser/events/payloads.py`](../../src/market_analyser/events/payloads.py)

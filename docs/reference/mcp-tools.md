<!-- GENERATED FILE - DO NOT EDIT BY HAND.
     Regenerate: uv run python -m market_analyser.apiref  (or: pnpm gen:api-docs)
     Rendered from the live sidecar; see Plan 0070 / ADR-0064. -->

# MCP tools

The 59 agent-callable MCP tools mounted at `/mcp`, from the live FastMCP registry.

| Tool | Summary |
| --- | --- |
| [`analyze_symbol`](#analyzesymbol) | Compute a full technical-condition snapshot for one symbol over cached bars: trend (up/down/sideways), momentum stance, latest indicator values (RSI, MACD, Bollinger, ATR, ADX, Supertrend, plus trailing RSI/ATR percentiles), trailing support/resistance levels, any candlestick patterns on the most recent bars, and the active classical chart patterns (head & shoulders, doubles, triangles, wedges — forming or freshly confirmed) still in play. |
| [`annotate_chart`](#annotatechart) | Place freeform drawings (annotations) on a symbol's chart. |
| [`backfill_ohlcv`](#backfillohlcv) | Pre-warm the local cache for a symbol/timeframe over [start, end] by fetching any missing bars from the upstream in the background. |
| [`bitcoin_market_pulse`](#bitcoinmarketpulse) | Get the current crypto macro picture in one call (CoinGecko, free public API): BTC price and 24h change, BTC dominance %, total crypto market cap and its 24h change, plus a neutral `regime` label describing market STRUCTURE (btc_led / alt_structure / risk_off_structure / neutral). |
| [`btc_cycle_snapshot`](#btccyclesnapshot) | Get the current BTC cycle picture in one call: days since the 2024-04-19 halving, ESTIMATED days to the next (the next-halving date is an estimate, hence the _est suffix), the cycle phase fraction (0.0 just after a halving, 1.0 at the estimated next), Mayer Multiple (close / 200-day SMA) and distance to the 200-week MA (close / SMA1400 - 1) from cached daily BTC-USD bars, plus the latest Fear & Greed and BTC dominance with 7/30-day deltas from the stored metric series, plus on-chain MVRV (market value / realized value) with its trailing full-history percentile. |
| [`compare_strategies`](#comparestrategies) | Run every reference strategy on one symbol/timeframe/window at its default parameters and return a leaderboard ranked by a chosen metric. |
| [`compute_wallet_pnl`](#computewalletpnl) | Reconstruct a wallet's DeFi profitability from its decoded on-chain transaction history (Ethereum, Base, Arbitrum, Optimism): per-position and total realized/unrealized P&L under average-cost lots, every leg valued at its own block timestamp - never trusting an aggregator's number. |
| [`create_position_watch`](#createpositionwatch) | Create a persisted watch over one concentrated-liquidity LP position the sidecar's DeFi position monitor re-reads on-chain on an interval (ADR-0093). |
| [`create_watch`](#createwatch) | Create a persisted watch the sidecar's alerting scheduler evaluates on an interval (ADR-0055). |
| [`crypto_fear_greed`](#cryptofeargreed) | Get the current crypto Fear & Greed index (Alternative.me): a single 0-100 value with a label (Extreme Fear / Fear / Neutral / Greed / Extreme Greed). |
| [`defi_fundamentals`](#defifundamentals) | Read DeFi-native token/protocol fundamentals for a symbol or protocol slug (e.g. |
| [`defi_risk`](#defirisk) | Read-only DeFi position risk as CONDITIONAL FACTS (a condition read, never investment advice or an action), discriminated by `kind`. |
| [`delete_position_watch`](#deletepositionwatch) | Delete a DeFi position watch by id, including its alert history. |
| [`delete_watch`](#deletewatch) | Delete a watch by id, including its alert history. |
| [`derivatives_snapshot`](#derivativessnapshot) | Get the Binance USDS-M derivatives picture for one contract symbol (e.g. |
| [`detect_chart_patterns`](#detectchartpatterns) | Detect classical chart patterns on the cached bars and draw them on the chart in one call: recognises head & shoulders (+inverse), double top/bottom, ascending/descending/symmetrical triangles, and rising/falling wedges over confirmed swing pivots, returns the typed hits as data (pattern, forming/confirmed state, direction, pivots, defining lines, measured-move target, strength), AND publishes a single `chart.trendlines v1` event carrying one trendline per hit line (dashed = forming, solid = confirmed) onto the chart already showing that symbol/timeframe. |
| [`detect_divergences`](#detectdivergences) | Detect price↔oscillator divergences on one symbol's cached bars for the chosen oscillator (rsi, macd_hist, obv, or mfi). |
| [`detect_levels`](#detectlevels) | Detect support/resistance levels on the cached bars and draw them on the chart in one call: clusters confirmed swing pivots into zones, ranks each zone's strength by touch count weighted by the volume traded at that price (volume-by-price), returns the ranked levels as data, AND publishes a single `chart.show v1` event carrying one `price_line` overlay per level (role support/resistance, labels S1/R1/... |
| [`evaluate_signals`](#evaluatesignals) | Evaluate a strategy against the CURRENT bar of one symbol — a live signal read, not a historical backtest. |
| [`event_calendar`](#eventcalendar) | List upcoming SCHEDULED market events for a category — dated forward facts (a timestamp, sometimes a magnitude), never buy/sell advice (a CONDITION). |
| [`find_convergence_opportunities`](#findconvergenceopportunities) | Screen prediction markets matching a query for CONVERGENCE opportunities — markets nearing resolution whose top outcome is near-certain, where a price converging to 1.00 leaves a few percent of implied upside. |
| [`forecast`](#forecast) | Forecast a cached symbol over a window; `kind` selects WHAT is predicted, all read-only conditions (never a buy/sell call, never a price level). |
| [`get_backtest`](#getbacktest) | Fetch a persisted backtest's full detail by run_id (the id run_backtest returns). |
| [`get_chart_drawings`](#getchartdrawings) | Read the drawings the USER placed on a symbol's chart (trendlines, rays, h/v-lines, rectangles, fib grids, long/short position boxes, and date/price range measures) — use this to see and reason about what the user drew, e.g. |
| [`get_metric_series`](#getmetricseries) | Read a stored metric time series (ADR-0051): points of one registered series_id over an inclusive [start, end] epoch-second window, sorted by ts ascending. |
| [`get_ohlcv`](#getohlcv) | Read OHLCV bars for one symbol over a [start, end] window. |
| [`get_pending_ui_events`](#getpendinguievents) | Read recent UI events the user generated in the chart viewer — drag-selected ranges and single bar clicks. |
| [`get_track_record`](#gettrackrecord) | Read the advisor's own live track record (ADR-0075): how its past recommendations turned out against realized price, scored path-dependently (did the stop or a target hit first). |
| [`highlight_pattern`](#highlightpattern) | Highlight a pattern on a chart. |
| [`list_alerts`](#listalerts) | Read fired-alert history, newest first, optionally scoped to one watch_id. |
| [`list_annotations`](#listannotations) | List annotations for a symbol/timeframe over a [start, end] window. |
| [`list_position_alerts`](#listpositionalerts) | Read fired DeFi position-alert history, newest first, optionally scoped to one watch_id. |
| [`list_position_watches`](#listpositionwatches) | List the persisted DeFi position watches (id, wallet, chain, pool_address, nft_token_id, dwell_hours, interval_seconds, enabled, source config\|agent, dwell_state), ordered by id. |
| [`list_watches`](#listwatches) | List the persisted watches (id, symbol, timeframe, kind, params, interval_seconds, enabled, last_state, created_at), ordered by id. |
| [`market_snapshot`](#marketsnapshot) | Get a point-in-time global market snapshot: live quotes for a fixed basket — S&P 500 (^GSPC), NASDAQ (^IXIC), VIX (^VIX), Bitcoin (BTC-USD), Ethereum (ETH-USD), EUR/USD (EURUSD=X), SPY, and GLD. |
| [`multi_timeframe_analysis`](#multitimeframeanalysis) | Report whether one symbol's trend is aligned across a ladder of timeframes. |
| [`news_for`](#newsfor) | Fetch recent news headlines for a symbol (or across all feeds when `symbol` is null) from a curated set of free RSS feeds (CoinDesk, CoinTelegraph, Yahoo Finance, MarketWatch, CNBC). |
| [`portfolio_summary`](#portfoliosummary) | Aggregate cross-venue holdings into one read-only view (facts only, no recommendation of any kind): the Binance account leg (spot balances + USDS-M futures positions, read via the read-only API key), the DeFi leg (wallet discovery across Ethereum/Base/Arbitrum/Optimism when a 0x wallet address is given, with average-cost basis joined from the reconstructed on-chain history), and the manual positions file (positions/portfolio.json). |
| [`prediction_market_odds`](#predictionmarketodds) | Get one prediction market's current outcomes and implied probabilities by market_id (from search_prediction_markets). |
| [`price_structure`](#pricestructure) | Read a single-symbol price-structure overlay on cached bars; `kind` selects the read. |
| [`quote_for`](#quotefor) | Get a live quote for one symbol: price, change_pct, previous_close, day high/low, 52-week high/low, currency, market_state (REGULAR/PRE/POST/CLOSED) and volume. |
| [`recommend`](#recommend) | ADVISORY ONLY — fuse the four analyst outputs for one symbol into a single labeled trade recommendation: the technical condition snapshot, the named strategy's live signal on the current bar, its walk-forward out-of-sample edge, and the calibrated direction forecast. |
| [`recommend_rebalance`](#recommendrebalance) | ADVISORY ONLY - turn a DeFi LP out-of-range alert into a single labeled rebalance recommendation: recenter / widen / exit, or an honest 'hold'. |
| [`run_backtest`](#runbacktest) | Run a backtest for a single strategy/symbol/timeframe window. |
| [`scan_patterns`](#scanpatterns) | Sweep a time range for EVERY candlestick pattern on the cached bars and highlight them all at once: publishes a single `chart.highlight v1` event carrying one marker per detected pattern (multi-bar patterns carry a bar span; doji/neutral patterns are included). |
| [`scan_pool_discrepancies`](#scanpooldiscrepancies) | Screen configured DEX pools for cross-pool price discrepancies, NET OF COST, for one or more canonical pairs (e.g. |
| [`scan_wallet`](#scanwallet) | Discover a wallet's DeFi positions from a public EVM address across Ethereum, Base, Arbitrum, and Optimism. |
| [`scan_watchlist`](#scanwatchlist) | Rank or filter a supplied symbol list (watchlist) on cached bars by a chosen condition — one watchlist-ranking verb, `rank_by` selects the mode. |
| [`screener_query`](#screenerquery) | Screen a market universe for symbols matching indicator/price filters (e.g. |
| [`search_prediction_markets`](#searchpredictionmarkets) | Search prediction markets by free text and get each match with its current odds. |
| [`search_symbols`](#searchsymbols) | Resolve a loose or free-text name/ticker to fetchable symbols (e.g. |
| [`sector_rotation`](#sectorrotation) | Rank a self-defined set of crypto sectors (Layer-1, Layer-2, DeFi, Memecoins, AI, DePIN, ...) by equal-weighted constituent momentum over cached bars — the classic 'where is capital rotating' read, for crypto. |
| [`sentiment`](#sentiment) | Summarise crowd/news sentiment for a symbol over a window; `source` selects the feed. |
| [`show_chart`](#showchart) | Render a chart in the Electron viewer. |
| [`technical_read`](#technicalread) | ADVISORY ONLY, LESSER TIER — a single-indicator technical read: the mechanical direction (long/short/flat) of ONE curated regime indicator by its textbook rule, with NO conviction and NO entry/stop/target levels. |
| [`update_chart`](#updatechart) | Apply a delta to the currently-rendered chart. |
| [`volume_read`](#volumeread) | Read one symbol's recent volume against its price move on cached bars; `kind` selects the read. |
| [`walk_forward_backtest`](#walkforwardbacktest) | Evaluate one strategy across n_splits rolling out-of-sample folds and return per-fold metrics plus an aggregate (mean/std of total_return and sharpe) and a full-run baseline. |
| [`write_annotation`](#writeannotation) | Write a chart annotation (bullish/bearish marker on a single candle). |

---

## `analyze_symbol`

Compute a full technical-condition snapshot for one symbol over cached bars: trend (up/down/sideways), momentum stance, latest indicator values (RSI, MACD, Bollinger, ATR, ADX, Supertrend, plus trailing RSI/ATR percentiles), trailing support/resistance levels, any candlestick patterns on the most recent bars, and the active classical chart patterns (head & shoulders, doubles, triangles, wedges — forming or freshly confirmed) still in play. Returns {snapshot, partial_reason, message, analyzed_at}: snapshot is null with partial_reason='no_bars' when nothing is cached for the symbol (backfill via get_ohlcv first). `lookback` is like 6mo/1y/30d/2w. Pass `as_of` (ISO datetime) for historical replay — the read is trailing, so no future bar leaks in. Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `lookback` | string | no | `"6mo"` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `AnalyzeSymbolResponse`

| Field | Type |
| --- | --- |
| `snapshot` | ConditionSnapshot \| null |
| `partial_reason` | string \| null |
| `message` | string \| null |
| `analyzed_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/analyze_symbol.py`](../../src/market_analyser/api/mcp_tools/analyze_symbol.py)

## `annotate_chart`

Place freeform drawings (annotations) on a symbol's chart. Publishes a `chart.annotations v1` event that declaratively REPLACES your previous annotation set for `symbol` — send the full set each time; an empty `drawings` list clears it. Each drawing is `{kind, points, id?, style?}` with `points` as `[{ts, price}, ...]` anchors: `trendline` (segment, 2 points), `ray` (through 2 points, extended right), `hline` (horizontal line at the point's price, 1 point), `vline` (vertical line at the point's ts, 1 point), `rect` (zone between 2 corner points), `fib` (Fibonacci retracement grid between 2 anchor points), `date_range` / `price_range` / `date_price_range` (measure between 2 anchor points; readouts derived at render). The position kinds `long_position` / `short_position` take exactly 1 anchor `(ts, entry)` plus required `stop` and `target` prices — long needs `stop < entry < target`, short `target < entry < stop` — and, because a position box is a directional call, an agent-placed one MUST also carry non-empty `rationale` and `basis` strings (ADR-0029; a bare box is rejected). Risk-reward is derived, never sent. Drawings are per-symbol and render on every timeframe (anchored to time+price, not bars). Supply your own stable `id` per drawing so the user's hide choices survive a re-push; `style` is optional `{color?, width?}`. Agent drawings render hide-only for the user (their own drawings stay editable) and are not persisted by the viewer — re-issue after a reload.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `drawings` | array[object] | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/annotate_chart.py`](../../src/market_analyser/api/mcp_tools/annotate_chart.py)

## `backfill_ohlcv`

Pre-warm the local cache for a symbol/timeframe over [start, end] by fetching any missing bars from the upstream in the background. Returns immediately with {started, gaps, message}: started=true plus the gap windows when a background fetch was scheduled, or started=false and an empty gaps list when the cache already covers the window. Watch the event stream — ohlcv.backfill_started fires first, then ohlcv.backfilled on success or ohlcv.backfill_failed (reason: rate_limited | upstream_unavailable | unknown_symbol | history_exceeded) on failure.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `start` | string (date-time) | yes | — |
| `end` | string (date-time) | yes | — |

**Returns:** `BackfillOhlcvResponse`

| Field | Type |
| --- | --- |
| `started` | boolean |
| `gaps` | array[GapWindow] |
| `message` | string |

**Source:** [`src/market_analyser/api/mcp_tools/backfill_ohlcv.py`](../../src/market_analyser/api/mcp_tools/backfill_ohlcv.py)

## `bitcoin_market_pulse`

Get the current crypto macro picture in one call (CoinGecko, free public API): BTC price and 24h change, BTC dominance %, total crypto market cap and its 24h change, plus a neutral `regime` label describing market STRUCTURE (btc_led / alt_structure / risk_off_structure / neutral). Market defaults to crypto (the only value in v1). Returns {macro, queried_at}: macro holds the measurements above; queried_at is when this call ran. `regime` is a structural condition (where capital is concentrating), NOT a buy/sell or risk recommendation. The figures are a point-in-time read — there is no as_of/historical replay — and are cached briefly, so asking again within a minute may return the same values.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | BitcoinMarketPulseInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/bitcoin_market_pulse.py`](../../src/market_analyser/api/mcp_tools/bitcoin_market_pulse.py)

## `btc_cycle_snapshot`

Get the current BTC cycle picture in one call: days since the 2024-04-19 halving, ESTIMATED days to the next (the next-halving date is an estimate, hence the _est suffix), the cycle phase fraction (0.0 just after a halving, 1.0 at the estimated next), Mayer Multiple (close / 200-day SMA) and distance to the 200-week MA (close / SMA1400 - 1) from cached daily BTC-USD bars, plus the latest Fear & Greed and BTC dominance with 7/30-day deltas from the stored metric series, plus on-chain MVRV (market value / realized value) with its trailing full-history percentile. Reads are local-only by default; pass refresh=true to first backfill/update the MVRV series from the network, then read. Fields are null when history is insufficient (fewer than 200 / 1400 daily bars for the moving averages; the dominance series accrues from deployment, so its deltas stay null until it warms up; mvrv and mvrv_percentile are null until the MVRV series is backfilled). Conditions, not advice.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | BtcCycleSnapshotInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/cycle_snapshot.py`](../../src/market_analyser/api/mcp_tools/cycle_snapshot.py)

## `compare_strategies`

Run every reference strategy on one symbol/timeframe/window at its default parameters and return a leaderboard ranked by a chosen metric. rank_by is one of sharpe|calmar|total_return|sortino (default sharpe), sorted best-first; rows whose metric is undefined (e.g. Calmar when the curve never dipped, reported as null) sort last, ties broken by strategy_id. Each row carries the strategy id/version and its full metric set (the extended ADR-0024 metrics included). Costs default to 0 bps / $10k capital. Comparison runs are NOT persisted — the leaderboard is the result. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | enum["15m", "1h", "4h", "1d", "1w"] | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `rank_by` | enum["sharpe", "calmar", "total_return", "sortino"] | no | `"sharpe"` |
| `commission_bps` | number | no | `0.0` |
| `slippage_bps` | number | no | `0.0` |
| `initial_capital` | number | no | `10000.0` |

**Returns:** `CompareStrategiesResponse`

| Field | Type |
| --- | --- |
| `symbol` | string |
| `timeframe` | string |
| `rank_by` | enum["sharpe", "calmar", "total_return", "sortino"] |
| `rows` | array[StrategyLeaderboardRow] |

**Source:** [`src/market_analyser/api/mcp_tools/compare_strategies.py`](../../src/market_analyser/api/mcp_tools/compare_strategies.py)

## `compute_wallet_pnl`

Reconstruct a wallet's DeFi profitability from its decoded on-chain transaction history (Ethereum, Base, Arbitrum, Optimism): per-position and total realized/unrealized P&L under average-cost lots, every leg valued at its own block timestamp - never trusting an aggregator's number. Returns {wallet (masked), positions: [{position_id, chain, pool_address (on-chain pool contract; null when the source omits it), is_lp, realized_usd, unrealized_usd, cost_basis_usd, vs_hodl_usd (LP only), incomplete, notes, windows, unclaimed_rewards}], position_count, incomplete, partial, incomplete_position_count, realized_usd, unrealized_usd, unclaimed_rewards, crosscheck_zerion_total, crosscheck_warning, error, message}. LP positions are the headline and are listed FIRST (is_lp=true); non-LP positions (lending, loose tokens, unpriceable exotics) follow, de-emphasized, and never suppress the LP figures. unclaimed_rewards is a labeled CURRENT-STATE on-chain read of gauge emissions owed-but-not-yet-claimed ([{symbol, amount, usd_value}], per position + a wallet roll-up); it is deliberately kept OUT of realized/unrealized and out of the deterministic re-run guarantee (there is no claim tx to replay), null when a position owes nothing. windows is the per-position P&L over a fixed rolling set ([{window: 7d|30d|90d|all, realized_usd, total_return_usd, estimated}]): realized_usd is EXACT, anchored to the run's analysis time, with the 'all' window equal to the position's all-time realized_usd; total_return_usd is an ESTIMATE (estimated=true always) of realized-in-window plus the unrealized drift since the window start, null for any window whose start mark cannot be priced (an honest per-window gap that does NOT mark the position incomplete). A position with a missing historical price or an unbooked event kind reports null figures with incomplete=true and a naming note - never a silently-zeroed number. Wallet totals sum over the COMPLETE positions only (Plan 0088 / ADR-0082): an incomplete position is excluded - never zeroed, never nulling the wallet - with partial=true and incomplete_position_count flagging the exclusion. crosscheck_zerion_total is Zerion's own FIFO figure, advisory only; crosscheck_warning flags gross (order-of-magnitude or sign) divergence - small differences are expected because the methods differ (average-cost vs FIFO). refresh=true pulls new transactions before replaying; the default replays the immutable cached history (deterministic re-run, zero upstream calls). First pull of a long history is slow (rate-limit-spaced pagination + per-event price lookups); re-runs read SQLite. On failure positions is null and error is 'auth' (no Zerion API key set - set it via the Settings secret endpoint), 'rate_limited', 'upstream_unavailable', or 'malformed_response'. address must be a raw 0x EVM address; ENS is not supported. Streams pnl_started/pnl_completed/pnl_failed on the SSE stream. Data from Zerion (history) + DefiLlama (historical prices).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | ComputeWalletPnlInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/compute_wallet_pnl.py`](../../src/market_analyser/api/mcp_tools/compute_wallet_pnl.py)

## `create_position_watch`

Create a persisted watch over one concentrated-liquidity LP position the sidecar's DeFi position monitor re-reads on-chain on an interval (ADR-0093). Identify the position by wallet (0x address), chain (ethereum/base/arbitrum/optimism; deep reads need that chain's RPC URL secret), pool_address, and optionally nft_token_id (omit to match the wallet's CL position in the pool). The alert is DWELL-QUALIFIED: it fires exactly once after the position has been continuously out of its tick range for >= dwell_hours (default 6.0), then re-arms when price re-enters the range. A one-tick excursion never fires. Alerts are condition facts (ticks, hours out, uncollected fees) - never rebalance advice (use `recommend` for that). Delivery: `defi.position_alert v1` SSE event (viewer toast + OS notification) + the pending-events poll + `list_position_alerts` history. interval_seconds defaults to 900 (15 min - LP ranges move on the timescale of hours; each check is an RPC read).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `wallet` | string | yes | — |
| `chain` | string | yes | — |
| `pool_address` | string | yes | — |
| `nft_token_id` | integer \| null | no | `None` |
| `dwell_hours` | number | no | `6.0` |
| `interval_seconds` | integer | no | `900` |
| `enabled` | boolean | no | `True` |

**Returns:** `DefiPositionWatch`

| Field | Type |
| --- | --- |
| `id` | integer |
| `wallet` | string |
| `chain` | enum["ethereum", "base", "arbitrum", "optimism"] |
| `pool_address` | string |
| `nft_token_id` | integer \| null |
| `dwell_hours` | number |
| `interval_seconds` | integer |
| `enabled` | boolean |
| `source` | enum["config", "agent"] |
| `created_at` | string (date-time) |
| `dwell_state` | DwellState |

**Source:** [`src/market_analyser/api/mcp_tools/position_watches.py`](../../src/market_analyser/api/mcp_tools/position_watches.py)

## `create_watch`

Create a persisted watch the sidecar's alerting scheduler evaluates on an interval (ADR-0055). Three kinds: 'indicator_threshold' (params: {indicator, operator, level} with operator one of < <= > >= and indicator one of adx, atr, bb_lower, bb_middle, bb_pct_b, bb_upper, close, macd, macd_hist, macd_signal, minus_di, obv, obv_slope, plus_di, rel_volume, rsi, supertrend, supertrend_direction, vol_pct90, vol_sma20, volume, vwap), 'pattern' (params: {pattern} one of bearish_engulfing, bearish_harami, bullish_engulfing, bullish_harami, dark_cloud_cover, doji, evening_star, hammer, hanging_man, marubozu, morning_star, piercing_line, three_black_crows, three_white_soldiers), and 'strategy_signal' (params: {strategy_id, params} — fires when the strategy emits a fresh signal on the latest closed bar). Alerts are EDGE-TRIGGERED: one alert per false->true transition of the condition, evaluated on closed bars only. interval_seconds defaults to the timeframe's bar period. Alerts are condition facts, never buy/sell advice. Delivery: `alert.triggered v1` SSE event (viewer toast) + the pending-events poll + `list_alerts` history. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo. Optional `note` (<= 500 chars): free-text context for WHY the watch exists (e.g. 'ETH long scenario A - neckline retest'), shown in the viewer's watch list and editable there.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `kind` | string | yes | — |
| `params` | object | yes | — |
| `interval_seconds` | integer \| null | no | `None` |
| `enabled` | boolean | no | `True` |
| `note` | string \| null | no | `None` |

**Returns:** `Watch`

| Field | Type |
| --- | --- |
| `id` | integer |
| `symbol` | string |
| `timeframe` | string |
| `kind` | enum["indicator_threshold", "pattern", "strategy_signal"] |
| `params` | IndicatorThresholdParams \| PatternParams \| StrategySignalParams |
| `interval_seconds` | integer |
| `enabled` | boolean |
| `last_state` | boolean \| null |
| `created_at` | string (date-time) |
| `note` | string \| null |

**Source:** [`src/market_analyser/api/mcp_tools/watches.py`](../../src/market_analyser/api/mcp_tools/watches.py)

## `crypto_fear_greed`

Get the current crypto Fear & Greed index (Alternative.me): a single 0-100 value with a label (Extreme Fear / Fear / Neutral / Greed / Extreme Greed). Takes no arguments. Returns `value`, `classification`, `published_at` (when the index was published upstream), `queried_at`, and `source`. The reading is market-wide (not per-symbol), wall-clock-current (no historical replay), and updates roughly once a day — asking again within the hour returns the same value.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | CryptoFearGreedInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/crypto_fear_greed.py`](../../src/market_analyser/api/mcp_tools/crypto_fear_greed.py)

## `defi_fundamentals`

Read DeFi-native token/protocol fundamentals for a symbol or protocol slug (e.g. 'AERO', 'aerodrome', 'uniswap') — the fundamentals price/structure is blind to for a DeFi token. Returns {query, protocol_slug, tvl (USD), tvl_trend (trailing [date, value] history), dex_volume (24h/7d/30d USD + change_1d_pct), fee_apr, reward_apr (annualized %, TVL-weighted over the protocol's pools), mcap, fdv (USD), unlocks (token-unlock calendar), emissions_detail + ve_gauge (Aerodrome-only deep tier: weekly emission/decay + veAERO lock/vote weight, read on-chain; null for other protocols), as_of, source, notes}. Keyless (DefiLlama); any field DefiLlama does not cover comes back null with a `notes` entry explaining the gap (e.g. a token with no gecko_id has null mcap; the unlock calendar is DefiLlama-Pro-gated for many small caps) — never a fabricated or zeroed number. Wall-clock-sensitive: current-state only, no historical replay (no as_of). This is a CONDITION read, never buy/sell advice.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | DefiFundamentalsInput | yes | — |

**Returns:** `DefiFundamentals`

| Field | Type |
| --- | --- |
| `query` | string |
| `protocol_slug` | string \| null |
| `tvl` | number \| null |
| `tvl_trend` | array[FundamentalsPoint] \| null |
| `dex_volume` | VolumeSummary \| null |
| `fee_apr` | number \| null |
| `reward_apr` | number \| null |
| `mcap` | number \| null |
| `fdv` | number \| null |
| `unlocks` | array[UnlockEvent] \| null |
| `emissions_detail` | EmissionsDetail \| null |
| `ve_gauge` | VeGaugeStats \| null |
| `as_of` | string (date-time) |
| `source` | string |
| `notes` | array[string] |

**Source:** [`src/market_analyser/api/mcp_tools/defi_fundamentals.py`](../../src/market_analyser/api/mcp_tools/defi_fundamentals.py)

## `defi_risk`

Read-only DeFi position risk as CONDITIONAL FACTS (a condition read, never investment advice or an action), discriminated by `kind`. Two independent, optional legs: an Aave account (fetched on-chain from `address` + `chain`) and a constant-product LP (numbers supplied in `lp`). Pass either or both. kind='scenario' (deterministic sensitivity to a SUPPLIED price move): the Aave leg returns {account, scenario:{collateral_shock, health_factor_before/after, liquidation_distance_before/after (fractional collateral drop that reaches HF=1), collateral/net value before/after}} for a supplied `collateral_shock` (e.g. -0.30); the LP leg returns {value_before, hodl_value_after, lp_value_after, impermanent_loss} from a supplied lp={amount0,price0,shock0,amount1,price1,shock1}. kind='conditional' (likelihood under a STATED vol model): the Aave leg returns {account, liquidation:{probability, horizon_days, daily_vol, seed, assumption}} — a seeded Monte Carlo of `collateral_symbol`'s trailing realized vol over `lookback_days` (a no-debt account returns liquidation=null with a note); the LP leg returns {quantiles, mean, daily_vol, assumption} from supplied lp={ratio_log_returns:[...]}. Every probabilistic figure carries its volatility assumption inline and is reproducible from `seed`; a trailing-vol fit cannot see a future regime shift (stated, not hidden). `horizon_days` (default 30), `seed` (default 0), `lookback_days` (default 90) control the Monte Carlo. On an Aave read failure the aave leg carries {error, message} (config/rate_limited/upstream_unavailable/malformed_response); the LP leg needs no network. `address` must be a raw 0x EVM address.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | DefiRiskInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/defi_risk.py`](../../src/market_analyser/api/mcp_tools/defi_risk.py)

## `delete_position_watch`

Delete a DeFi position watch by id, including its alert history. Returns {deleted: bool} - false when the id does not exist (idempotent).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer | yes | — |

**Returns:** `DeletePositionWatchResponse`

| Field | Type |
| --- | --- |
| `deleted` | boolean |

**Source:** [`src/market_analyser/api/mcp_tools/position_watches.py`](../../src/market_analyser/api/mcp_tools/position_watches.py)

## `delete_watch`

Delete a watch by id, including its alert history. Returns {deleted: bool} — false when the id does not exist (idempotent).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer | yes | — |

**Returns:** `DeleteWatchResponse`

| Field | Type |
| --- | --- |
| `deleted` | boolean |

**Source:** [`src/market_analyser/api/mcp_tools/watches.py`](../../src/market_analyser/api/mcp_tools/watches.py)

## `derivatives_snapshot`

Get the Binance USDS-M derivatives picture for one contract symbol (e.g. BTCUSDT, ETHUSDT) from the locally stored metric series: the latest funding rate (decimal per funding interval, e.g. 0.0001 = 1bp) with next_funding_ts estimated from the actual spacing of the stored prints (not an assumed 8h), the mean funding rate over the trailing 7 days, and the latest open interest (base-asset units) with its 24h and 7d deltas. Reads are local-only by default; pass refresh=true to first fetch new funding prints and accrue one open-interest sample from the network. Fields are null when the stored series cannot support them (no funding print yet, the open-interest accrual still warming up, no point at the delta anchor) — null means insufficient history, never zero. Conditions, not advice.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | DerivativesSnapshotInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/derivatives_snapshot.py`](../../src/market_analyser/api/mcp_tools/derivatives_snapshot.py)

## `detect_chart_patterns`

Detect classical chart patterns on the cached bars and draw them on the chart in one call: recognises head & shoulders (+inverse), double top/bottom, ascending/descending/symmetrical triangles, and rising/falling wedges over confirmed swing pivots, returns the typed hits as data (pattern, forming/confirmed state, direction, pivots, defining lines, measured-move target, strength), AND publishes a single `chart.trendlines v1` event carrying one trendline per hit line (dashed = forming, solid = confirmed) onto the chart already showing that symbol/timeframe. Strictly trailing: a hit at bar i reads only bars up to i; `forming` means the geometry is complete but the breakout close has not happened, `confirmed` means a close broke the neckline/trendline by the ATR-scaled margin. Optional `patterns` / `states` filter the hits (patterns from head_shoulders, inverse_head_shoulders, double_top, double_bottom, ascending_triangle, descending_triangle, symmetrical_triangle, rising_wedge, falling_wedge; states from forming, confirmed). Reads cached bars only (backfill via get_ohlcv first); an empty/uncached range publishes nothing and returns count=0. Results are derived and NOT persisted. Conditions only — hits are geometry facts, never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `patterns` | array[string] \| null | no | `None` |
| `states` | array[string] \| null | no | `None` |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/detect_chart_patterns.py`](../../src/market_analyser/api/mcp_tools/detect_chart_patterns.py)

## `detect_divergences`

Detect price↔oscillator divergences on one symbol's cached bars for the chosen oscillator (rsi, macd_hist, obv, or mfi). Returns {result, partial_reason, scanned_at}: result is the list of divergences — each with its kind (regular/hidden bullish/bearish), the two price anchors, the two matched oscillator anchors, the confirming bar_index, and a 0..1 strength — pairing the two most recent confirmed price swing pivots of a kind against the oscillator's own pivots. Regular bearish = higher price high + lower oscillator high (a rally losing momentum); regular bullish = lower low + higher oscillator low; hidden divergences flag trend continuation. An empty list means the scan ran and found nothing; result is null with partial_reason='no_bars' when nothing is cached (backfill via get_ohlcv first). When it finds a divergence it ALSO publishes a single chart.divergences v1 event onto the chart already showing that symbol/timeframe (each drawn as two segments — one on the price pane, one on that oscillator's own pane); an empty or no_bars scan publishes nothing. Strictly trailing: a divergence at bar i reads only bars up to i. Pass `as_of` for historical replay (no future leak). Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `oscillator` | enum["rsi", "macd_hist", "obv", "mfi"] | no | `"rsi"` |
| `lookback` | integer | no | `60` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `DivergencesResponse`

| Field | Type |
| --- | --- |
| `result` | array[Divergence] \| null |
| `partial_reason` | string \| null |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/detect_divergences.py`](../../src/market_analyser/api/mcp_tools/detect_divergences.py)

## `detect_levels`

Detect support/resistance levels on the cached bars and draw them on the chart in one call: clusters confirmed swing pivots into zones, ranks each zone's strength by touch count weighted by the volume traded at that price (volume-by-price), returns the ranked levels as data, AND publishes a single `chart.show v1` event carrying one `price_line` overlay per level (role support/resistance, labels S1/R1/... in strength order). Reads cached bars only (backfill via get_ohlcv first); an empty/uncached range publishes nothing and returns count=0. `max_levels` caps how many levels per role survive (strongest first). Results are derived and NOT persisted — reopening the viewer re-runs the detection. Conditions only — levels are chart geometry, never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `max_levels` | integer | no | `5` |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/detect_levels.py`](../../src/market_analyser/api/mcp_tools/detect_levels.py)

## `evaluate_signals`

Evaluate a strategy against the CURRENT bar of one symbol — a live signal read, not a historical backtest. Reports the current implied position (flat/long), the most recent signal (kind + bar + timestamp + reason), bars-since-last-signal, and a `fresh_signal` flag that is true when a signal fired on the last closed bar. A still-forming latest bar is excluded (surfaced via `latest_bar_excluded_as_forming` / `evaluated_through_ts`). `range_start` is the warm-up lookback — request enough history for the strategy's indicators to warm up; there is no range_end (the read always runs to the latest available bar) and no as_of. This is a CONDITION REPORT, never a buy/sell recommendation. Publishes a `signal.evaluated v1` event so the viewer's live-signal panel updates. Nothing is persisted. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `strategy_id` | string | yes | — |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `params` | object | yes | — |

**Returns:** `SignalEvaluation`

| Field | Type |
| --- | --- |
| `strategy_id` | string |
| `symbol` | string |
| `timeframe` | string |
| `evaluated_through_ts` | string (date-time) |
| `closed_bar_count` | integer |
| `latest_bar_excluded_as_forming` | boolean |
| `current_position` | enum["flat", "long", "short"] |
| `last_signal` | EvaluatedSignal \| null |
| `bars_since_last_signal` | integer \| null |
| `fresh_signal` | boolean |

**Source:** [`src/market_analyser/api/mcp_tools/evaluate_signals.py`](../../src/market_analyser/api/mcp_tools/evaluate_signals.py)

## `event_calendar`

List upcoming SCHEDULED market events for a category — dated forward facts (a timestamp, sometimes a magnitude), never buy/sell advice (a CONDITION). Returns {category, events: [{category, title, symbol, scheduled_at (UTC ISO-8601), magnitude, source, note}], notes, queried_at}, events sorted by scheduled_at ascending. category='macro': upcoming FOMC rate-decision dates (from a curated seed — dates only, no consensus/actual numbers) plus CPI and PCE release dates from FRED. FRED needs a free `fred_api_key` secret; WITHOUT the key the macro read is FOMC-only and a `notes` entry says FRED is unconfigured (inert — zero requests). Coverage is honestly incomplete: release DATES, not the printed figures, and the curated FOMC seed can lag a Fed reschedule. category='earnings': upcoming equity earnings dates from Finnhub over a forward `window` (7d/30d/90d/180d/1y, default 90d); pass `symbol` (e.g. 'TSLA') to narrow to one company, or omit it for the whole window. Each event's `magnitude` is the EPS estimate where the free tier serves it (null when gated), and the `note` carries the session (before/after market), quarter/year, revenue estimate, and any gated field. Finnhub needs a free `finnhub_api_key` secret; WITHOUT the key the earnings read is honest-empty with a `notes` entry (inert — zero requests). category='listings': keyless crypto listings/delistings detected by self-diffing Binance and Coinbase tradeable-symbol sets against a persisted prior snapshot — one event per tradeable add (listing) or remove (delisting), `scheduled_at` is the DETECTION time. Honestly incomplete: forward announcements and forks/upgrades are NOT covered, and a first run (no prior snapshot) records a baseline and detects nothing (both disclosed in `notes`); `symbol`/`window` do not apply. Each degraded or unconfigured provider adds a `notes` entry rather than failing the call. Wall-clock-sensitive: forward-looking, no historical replay (no as_of) — repeated calls legitimately differ as the calendar advances.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | EventCalendarInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/event_calendar.py`](../../src/market_analyser/api/mcp_tools/event_calendar.py)

## `find_convergence_opportunities`

Screen prediction markets matching a query for CONVERGENCE opportunities — markets nearing resolution whose top outcome is near-certain, where a price converging to 1.00 leaves a few percent of implied upside. Returns ranked opportunities {market_id, question, outcome_label, implied_probability, implied_return_if_right, time_to_resolution, capital_lockup_note, liquidity_caution, resolution_risk {level, reasons}, volume_usd, closes_at, queried_at, source, market_url}. market_url is the canonical Polymarket page for the market (provenance/citation — where the public fact lives, never a trade control), null when the source gives no usable slug. implied_return_if_right = (1 - price) / price is GROSS of the resolution tail — it is NOT expected value; the tail lives in resolution_risk (a LABELED HEURISTIC over multi-outcome wording, thin/unknown book, and dispute-prone question terms — never a guarantee), liquidity_caution, and capital_lockup_note (market close is not settlement — UMA resolution can lag or be disputed, locking capital). IMPORTANT: these are facts with their risks attached, never a call — this reports conditions and never tells you to take a position; it signs nothing and moves no funds. Filter knobs: max_days_to_close (window, default 7), min_confidence (probability floor, default 0.90), thin_book_volume_usd (thin-book threshold, default 50000). Results are bounded to 50 per page: when more remain partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=returned). On failure opportunities is null and error is a typed reason (rate_limited / upstream_unavailable / malformed_response). Data from Polymarket public endpoints (no account, no funds).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | FindConvergenceOpportunitiesInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/prediction_screener.py`](../../src/market_analyser/api/mcp_tools/prediction_screener.py)

## `forecast`

Forecast a cached symbol over a window; `kind` selects WHAT is predicted, all read-only conditions (never a buy/sell call, never a price level). Returns {kind, result}: the kind's payload rides under `result`. kind='direction' (default): the price DIRECTION over one or more horizons, each a calibrated up/down/flat probability or an honest 'no edge over baseline' verdict. Horizons default to 1/5/21 bars on 1d (next-day / ~1w / ~1mo) and next-bar elsewhere; pass horizons=[...] to override. Each horizon trains and walk-forward-validates its OWN model and passes/fails the naive-baseline gate INDEPENDENTLY ('edge at 1d, no edge at 1mo' is normal); a failed horizon ships prob_*=null with its validation basis, and each block carries out-of-sample skill, baseline skill, edge_margin, and edge_strength ('no_edge'/'marginal'/'clear'). kind='volatility': realised VOLATILITY over the next horizon_bars — the predicted per-bar magnitude with a 1-sigma band, scored against EWMA + persistence baselines by QLIKE; when beats_baseline is false, trust baseline_vol (always surfaced). Use it for position sizing and stop distance. kind='regime': the market REGIME TRANSITION — the current trend x volatility state (e.g. up_quiet / down_volatile) and a probability distribution over the next-period regime horizon_bars ahead, scored against a persistence baseline (regime unchanged) by the Brier score; regimes are sticky, so beating persistence is a real signal. horizons/flat_band apply to 'direction' only; horizon_bars to 'volatility'/'regime' only. Features (all kinds): the symbol's OHLCV indicators plus BTC cycle + exogenous series (Fear & Greed, BTC dominance, funding, open interest, MVRV) joined lag-1 as-of at bar open (no publication-lag lookahead), on the richest-first tier ladder v2-full -> v2-deep -> v1; provenance names the tier (feature_set_id), its series (series_inputs), any skipped tier (fallback_reason), and the top out-of-sample permutation-importance drivers. Requires bars already cached for the window (backfill via get_ohlcv first). Supported timeframes: 1d, 1h, 15m, 4h, 1w.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `kind` | enum["direction", "volatility", "regime"] | no | `"direction"` |
| `horizons` | array[integer] \| null | no | `None` |
| `flat_band` | number | no | `0.001` |
| `horizon_bars` | integer | no | `5` |
| `n_splits` | integer | no | `5` |
| `seed` | integer | no | `1729` |

**Returns:** `ForecastResponse`

| Field | Type |
| --- | --- |
| `kind` | enum["direction", "volatility", "regime"] |
| `result` | MultiHorizonForecastResult \| VolatilityForecast \| RegimeForecast |

**Source:** [`src/market_analyser/api/mcp_tools/forecast.py`](../../src/market_analyser/api/mcp_tools/forecast.py)

## `get_backtest`

Fetch a persisted backtest's full detail by run_id (the id run_backtest returns). Returns the spec, the full metrics block, and the COMPLETE trade list (entry/exit bar index + price per round-trip) inline — the trade-by-trade breakdown run_backtest's compact summary omits. The equity curve is one point per bar and can be large, so it is NOT returned unless include_equity=true; when requested it is paged like get_ohlcv (equity_offset / max_equity_points, capped at 1000, with partial_reason='too_large' and total_available/offset/returned when more remain). An unknown run_id is a not-found error, not a result.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `run_id` | string | yes | — |
| `include_equity` | boolean | no | `False` |
| `equity_offset` | integer | no | `0` |
| `max_equity_points` | integer \| null | no | `None` |

**Returns:** `GetBacktestResponse`

| Field | Type |
| --- | --- |
| `run_id` | string |
| `engine_version` | string |
| `strategy_id` | string |
| `strategy_version` | string |
| `symbol` | string |
| `timeframe` | string |
| `range_start` | string (date-time) |
| `range_end` | string (date-time) |
| `params` | object |
| `costs` | object |
| `initial_capital` | number |
| `sizing` | string |
| `metrics` | BacktestMetrics |
| `trades` | array[Trade] |
| `equity` | EquityPage \| null |

**Source:** [`src/market_analyser/api/mcp_tools/get_backtest.py`](../../src/market_analyser/api/mcp_tools/get_backtest.py)

## `get_chart_drawings`

Read the drawings the USER placed on a symbol's chart (trendlines, rays, h/v-lines, rectangles, fib grids, long/short position boxes, and date/price range measures) — use this to see and reason about what the user drew, e.g. 'what do you think about this resistance line I drew?'. Returns `{symbol, drawings, synced_at}` where each drawing is `{kind, points, id, ...}` anchored at `(ts, price)`. `synced_at` is an ISO timestamp of the last sync, or null when the viewer has not synced this symbol since the sidecar started — null (or a stale timestamp) means the set may not reflect what is on screen now, so read it before trusting an empty list. This is a READ-ONLY mirror; the user owns their drawings — place your own with annotate_chart instead.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |

**Returns:** `UserDrawingsSnapshot`

| Field | Type |
| --- | --- |
| `symbol` | string |
| `drawings` | array[DrawingSpec] |
| `synced_at` | string (date-time) \| null |

**Source:** [`src/market_analyser/api/mcp_tools/get_chart_drawings.py`](../../src/market_analyser/api/mcp_tools/get_chart_drawings.py)

## `get_metric_series`

Read a stored metric time series (ADR-0051): points of one registered series_id over an inclusive [start, end] epoch-second window, sorted by ts ascending. Returns {series_id, points: [{ts, value}], partial_reason, message, total_available, offset, returned}. The inline result is bounded to 2000 points per page: when the window holds more, partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=offset+returned) — paging never changes what is stored, only the reply slice. Unknown series ids are rejected with the registered list. Registered series: binance.funding_rate.BTCUSDT, binance.funding_rate.ETHUSDT, binance.open_interest.BTCUSDT, binance.open_interest.ETHUSDT, coingecko.btc_dominance, coingecko.total_mcap_usd, coinmetrics.btc.mvrv, fng.value.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `series_id` | string | yes | — |
| `start` | integer | no | `0` |
| `end` | integer \| null | no | `None` |
| `offset` | integer | no | `0` |
| `max_points` | integer \| null | no | `None` |

**Returns:** `GetMetricSeriesResponse`

| Field | Type |
| --- | --- |
| `series_id` | string |
| `points` | array[MetricPointOut] |
| `partial_reason` | string \| null |
| `message` | string \| null |
| `total_available` | integer |
| `offset` | integer |
| `returned` | integer |

**Source:** [`src/market_analyser/api/mcp_tools/metric_series.py`](../../src/market_analyser/api/mcp_tools/metric_series.py)

## `get_ohlcv`

Read OHLCV bars for one symbol over a [start, end] window. Reads the local cache and fetches any missing bars from the upstream (Yahoo) on a cache miss before returning, so this tool populates the cache itself — no separate step is needed. Returns {bars, partial_reason, message}: partial_reason is null on full success, or a typed reason (rate_limited | upstream_unavailable | unknown_symbol | history_exceeded) when only some gaps could be filled or the window reaches past the timeframe's available history. Set backfill_async=true to return whatever is already cached immediately and run the fetch in the background (partial_reason='backfill_async_pending'); progress then arrives on the event stream as ohlcv.backfilled / ohlcv.backfill_failed. The inline result is bounded to 400 bars per page: when the window holds more, partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=returned) — the cache still holds the whole window, only the reply is sliced. Live-mode only; supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `start` | string (date-time) | yes | — |
| `end` | string (date-time) | yes | — |
| `backfill_async` | boolean | no | `False` |
| `offset` | integer | no | `0` |
| `max_bars` | integer \| null | no | `None` |

**Returns:** `GetOhlcvResponse`

| Field | Type |
| --- | --- |
| `bars` | array[Bar] |
| `partial_reason` | enum["rate_limited", "upstream_unavailable", "unknown_symbol", "history_exceeded", "backfill_async_pending", "too_large"] \| null |
| `message` | string \| null |
| `total_available` | integer |
| `offset` | integer |
| `returned` | integer |

**Source:** [`src/market_analyser/api/mcp_tools/get_ohlcv.py`](../../src/market_analyser/api/mcp_tools/get_ohlcv.py)

## `get_pending_ui_events`

Read recent UI events the user generated in the chart viewer — drag-selected ranges and single bar clicks. Gestures are always forwarded (no setup or mode required). By default (drain=True) each call drains the events it returns, so consecutive draining reads return disjoint sets — call it when you are ready to act on the user's gestures. Pass drain=False to peek without consuming. `since` returns only events stamped strictly after that timestamp. The same buffer is also exposed (non-draining) as the MCP resource ui-events://recent, which you can subscribe to for update notifications; dedupe across the tool and the resource on each event's `event_id`.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `since` | string (date-time) \| null | no | `None` |
| `drain` | boolean | no | `True` |

**Returns:** `get_pending_ui_eventsOutput`

| Field | Type |
| --- | --- |
| `result` | array[UIEventEnvelope] |

**Source:** [`src/market_analyser/api/mcp_tools/get_pending_ui_events.py`](../../src/market_analyser/api/mcp_tools/get_pending_ui_events.py)

## `get_track_record`

Read the advisor's own live track record (ADR-0075): how its past recommendations turned out against realized price, scored path-dependently (did the stop or a target hit first). Returns {track_record, recent, partial_reason, message, total_available, offset, returned}. The track_record carries the directional hit-rate and mean R-multiple, a calibration read (Brier score + reliability buckets: stated probability vs realized frequency), and a baseline comparison (hit-rate vs a buy-and-hold over-horizon alternative) — each with its sample size, and marked insufficient below a stated floor so a handful of calls is never presented as a conclusion. `recent` is the most-recent scored calls (symbol, direction, outcome, realized R). Optionally filter by `symbol`. This is a factual record of past accuracy — what happened and how it compares to the trivial baseline, nothing more, and no call to act. Bounded to 100 recent calls per page (ADR-0046); page on with offset=offset+returned.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string \| null | no | `None` |
| `offset` | integer | no | `0` |
| `max_calls` | integer \| null | no | `None` |

**Returns:** `GetTrackRecordResponse`

| Field | Type |
| --- | --- |
| `track_record` | TrackRecord |
| `recent` | array[ScoredCallOut] |
| `partial_reason` | string \| null |
| `message` | string \| null |
| `total_available` | integer |
| `offset` | integer |
| `returned` | integer |

**Source:** [`src/market_analyser/api/mcp_tools/track_record.py`](../../src/market_analyser/api/mcp_tools/track_record.py)

## `highlight_pattern`

Highlight a pattern on a chart. Publishes a `chart.highlight v1` event AND persists each marker as an annotation row (so the highlight survives a viewer reload). Use this for patterns you detected NOW; use `write_annotation` for the lower-level persist-only primitive.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `event_ts` | string (date-time) | yes | — |
| `kind` | AnnotationKind | yes | — |
| `label` | string \| null | no | `None` |
| `agent_id` | string | no | `"unknown"` |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/highlight_pattern.py`](../../src/market_analyser/api/mcp_tools/highlight_pattern.py)

## `list_alerts`

Read fired-alert history, newest first, optionally scoped to one watch_id. Each alert's payload is the condition-only `alert.triggered v1` fact (what condition, what values, when) — never a recommendation. The inline result is bounded to 200 alerts per page: when more match, partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=offset+returned).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer \| null | no | `None` |
| `offset` | integer | no | `0` |
| `max_alerts` | integer \| null | no | `None` |

**Returns:** `ListAlertsResponse`

| Field | Type |
| --- | --- |
| `alerts` | array[Alert] |
| `partial_reason` | string \| null |
| `message` | string \| null |
| `total_available` | integer |
| `offset` | integer |
| `returned` | integer |

**Source:** [`src/market_analyser/api/mcp_tools/watches.py`](../../src/market_analyser/api/mcp_tools/watches.py)

## `list_annotations`

List annotations for a symbol/timeframe over a [start, end] window. Boundary-inclusive on both ends. Returns annotations from all agents (no per-agent_id filter).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `start` | string (date-time) | yes | — |
| `end` | string (date-time) | yes | — |

**Returns:** `list_annotationsOutput`

| Field | Type |
| --- | --- |
| `result` | array[Annotation] |

**Source:** [`src/market_analyser/api/mcp_tools/list_annotations.py`](../../src/market_analyser/api/mcp_tools/list_annotations.py)

## `list_position_alerts`

Read fired DeFi position-alert history, newest first, optionally scoped to one watch_id. Each alert is the condition-only out-of-range fact (pool, tick range vs current tick, hours out of range, uncollected fees at fire) - never a recommendation; ask `recommend` for the advisory rebalance call. The inline result is bounded to 100 alerts per page: when more match, partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=offset+returned).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer \| null | no | `None` |
| `offset` | integer | no | `0` |
| `max_alerts` | integer \| null | no | `None` |

**Returns:** `ListPositionAlertsResponse`

| Field | Type |
| --- | --- |
| `alerts` | array[DefiPositionAlert] |
| `partial_reason` | string \| null |
| `message` | string \| null |
| `total_available` | integer |
| `offset` | integer |
| `returned` | integer |

**Source:** [`src/market_analyser/api/mcp_tools/position_watches.py`](../../src/market_analyser/api/mcp_tools/position_watches.py)

## `list_position_watches`

List the persisted DeFi position watches (id, wallet, chain, pool_address, nft_token_id, dwell_hours, interval_seconds, enabled, source config|agent, dwell_state), ordered by id. `enabled_only=true` filters to the watches the monitor is ticking. A watch whose pool the RPC adapter cannot deep-read never fires - the monitor's /healthz heartbeat surfaces it as 'unreadable'.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `enabled_only` | boolean | no | `False` |

**Returns:** `list_position_watchesOutput`

| Field | Type |
| --- | --- |
| `result` | array[DefiPositionWatch] |

**Source:** [`src/market_analyser/api/mcp_tools/position_watches.py`](../../src/market_analyser/api/mcp_tools/position_watches.py)

## `list_watches`

List the persisted watches (id, symbol, timeframe, kind, params, interval_seconds, enabled, last_state, created_at), ordered by id. `enabled_only=true` filters to the watches the scheduler is ticking.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `enabled_only` | boolean | no | `False` |

**Returns:** `list_watchesOutput`

| Field | Type |
| --- | --- |
| `result` | array[Watch] |

**Source:** [`src/market_analyser/api/mcp_tools/watches.py`](../../src/market_analyser/api/mcp_tools/watches.py)

## `market_snapshot`

Get a point-in-time global market snapshot: live quotes for a fixed basket — S&P 500 (^GSPC), NASDAQ (^IXIC), VIX (^VIX), Bitcoin (BTC-USD), Ethereum (ETH-USD), EUR/USD (EURUSD=X), SPY, and GLD. Takes no arguments. Returns {quotes, queried_at}: quotes maps each basket symbol to {quote, error, message} — quote is the live quote object (price, change_pct, day range, etc.) on success, or null with a typed error reason ('unknown_symbol' / 'rate_limited' / 'upstream_unavailable') and a human message if that symbol failed. One failing symbol does NOT fail the snapshot; the others still return. Live and wall-clock-current — there is no as_of/historical replay (use get_ohlcv for history). Data from Yahoo Finance.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | MarketSnapshotInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/market_snapshot.py`](../../src/market_analyser/api/mcp_tools/market_snapshot.py)

## `multi_timeframe_analysis`

Report whether one symbol's trend is aligned across a ladder of timeframes. Runs the full condition snapshot per timeframe and returns {alignment, analyzed_at}: alignment.timeframes carries each timeframe's snapshot (null when nothing is cached for that timeframe — backfill via get_ohlcv first), alignment.dominant_trend is the trend held by the most timeframes, and alignment.agreement is the 0..1 fraction of available timeframes that agree with it. Default ladder is weekly/daily/4h/1h/15m; pass `timeframes` to override. Pass `as_of` (ISO datetime) for historical replay — each per-timeframe read is trailing, so no future bar leaks in. Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframes` | array[string] \| null | no | `None` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `MultiTimeframeAnalysisResponse`

| Field | Type |
| --- | --- |
| `alignment` | MultiTimeframeAlignment |
| `analyzed_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/multi_timeframe_analysis.py`](../../src/market_analyser/api/mcp_tools/multi_timeframe_analysis.py)

## `news_for`

Fetch recent news headlines for a symbol (or across all feeds when `symbol` is null) from a curated set of free RSS feeds (CoinDesk, CoinTelegraph, Yahoo Finance, MarketWatch, CNBC). Returns up to `limit` items newest-first under `items`, each with title, url, published_at, source. Set `with_sentiment=true` to attach a per-headline VADER `compound_sentiment` in [-1, 1] (slower — it scores every item). `window` is one of 1h/4h/24h/7d. Symbol filtering is a whole-word token match (BTC matches 'BTC ETF', not 'BTCUSD'); long company names may be missed (no name expansion). Results are wall-clock-sensitive — no historical replay.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | NewsForInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/news_for.py`](../../src/market_analyser/api/mcp_tools/news_for.py)

## `portfolio_summary`

Aggregate cross-venue holdings into one read-only view (facts only, no recommendation of any kind): the Binance account leg (spot balances + USDS-M futures positions, read via the read-only API key), the DeFi leg (wallet discovery across Ethereum/Base/Arbitrum/Optimism when a 0x wallet address is given, with average-cost basis joined from the reconstructed on-chain history), and the manual positions file (positions/portfolio.json). Returns {summary: {holdings: [{symbol, venue, quantity, avg_cost, as_of, usd_value, pricing_source, kind}], unrealized_pnl_usd, exposure_by_asset, exposure_by_venue, legs_as_of, queried_at}, leg_errors, notes, error, message}. Every leg carries its own as_of - freshness is never blended; every valuation names its pricing_source (venue mark for futures, live quotes for spot/manual rows, discovery figures for DeFi) - no single implied oracle. unrealized_pnl_usd = usd_value - avg_cost x quantity summed over holdings carrying both a price and a basis; None when none does; notes flag partial coverage, unpriced holdings, and skipped or incomplete basis. A failing leg never fails the call: it lands in leg_errors with a typed reason ('auth' = venue credential missing or rejected - set binance_read_api_key + binance_read_api_secret, or zerion_api_key, via the Settings secret endpoint) while the other legs still aggregate. wallet is optional; include_defi_basis=false skips the history replay. First basis call for a wallet ingests its history (slow); re-runs read the immutable SQLite cache.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | PortfolioSummaryInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/portfolio.py`](../../src/market_analyser/api/mcp_tools/portfolio.py)

## `prediction_market_odds`

Get one prediction market's current outcomes and implied probabilities by market_id (from search_prediction_markets). Returns {market, queried_at, source, error, message}: market is {market_id, question, outcomes, closed, closes_at, volume_usd, liquidity_usd, queried_at, source} with outcomes a list of {label, implied_probability in [0, 1]}. The price IS the money-weighted probability of the outcome; a binary market's outcomes sum to about 1. Facts only - a market-implied probability is a condition, never a buy/sell/hold call. On failure market is null and error is a typed reason: not_found (no such market_id), rate_limited, upstream_unavailable, or malformed_response. Data from Polymarket public endpoints (no account, no funds).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | PredictionMarketOddsInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/prediction_markets.py`](../../src/market_analyser/api/mcp_tools/prediction_markets.py)

## `price_structure`

Read a single-symbol price-structure overlay on cached bars; `kind` selects the read. Returns {kind, result, partial_reason, scanned_at}: result is the mode's geometry (null with partial_reason when uncomputable), scanned_at is run provenance. Modes: kind='fibonacci' — a Fibonacci grid auto-anchored to the dominant recent swing (FibonacciLevels: the grid kind, high/low anchors, swing direction, ratio->price levels); fibonacci.kind='retracement' (default) draws inside the swing, 'extension' projects beyond it off the last close; partial_reason='no_swing' when the bars hold no dominant swing. kind='pivots' — classic pivot levels from the last completed bar's HLC (PivotPoints: central pivot, R1-R3, S1-S3); pivots.method='floor' (default), 'camarilla', or 'woodie'. kind='anchored_vwap' — the anchored VWAP accumulated from a chosen bar (AnchoredVwapValue: anchor_index, anchor_ts, latest value); omit anchored_vwap.anchor_index to auto-anchor to the dominant swing's start (first bar if none), or pass an explicit 0-based index. kind='market_structure' — the price-action structure (MarketStructure: structural_trend from the HH/HL/LH/LL swing sequence, labeled_pivots, BOS/CHoCH events); this is a SECOND, distinct trend read reported ALONGSIDE analyze_symbol's indicator trend — disagreement is itself the signal, never merged. partial_reason='no_bars' (any mode) when nothing is cached (backfill via get_ohlcv first). Strictly trailing: reads only bars at-or-before the last one. Pass `as_of` for historical replay (no future leak). Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `kind` | enum["fibonacci", "pivots", "anchored_vwap", "market_structure"] | yes | — |
| `fibonacci` | FibonacciOpts \| null | no | `None` |
| `pivots` | PivotsOpts \| null | no | `None` |
| `anchored_vwap` | AnchoredVwapOpts \| null | no | `None` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `PriceStructureResponse`

| Field | Type |
| --- | --- |
| `kind` | enum["fibonacci", "pivots", "anchored_vwap", "market_structure"] |
| `result` | FibonacciLevels \| PivotPoints \| AnchoredVwapValue \| MarketStructure \| null |
| `partial_reason` | enum["no_bars", "no_swing"] \| null |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/price_structure.py`](../../src/market_analyser/api/mcp_tools/price_structure.py)

## `quote_for`

Get a live quote for one symbol: price, change_pct, previous_close, day high/low, 52-week high/low, currency, market_state (REGULAR/PRE/POST/CLOSED) and volume. Returns {quote, error, message, queried_at}: quote is an object with those fields on success; on failure quote is null and error is a typed reason (e.g. 'unknown_symbol' for a symbol the source doesn't carry — recover via search_symbols, then retry — or 'rate_limited'/'upstream_unavailable'), with a human message. change_pct is derived from previous_close. Live and wall-clock-current: there is no as_of/historical replay (use get_ohlcv for historical price). Data from Yahoo Finance.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | QuoteForInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/quote_for.py`](../../src/market_analyser/api/mcp_tools/quote_for.py)

## `recommend`

ADVISORY ONLY — fuse the four analyst outputs for one symbol into a single labeled trade recommendation: the technical condition snapshot, the named strategy's live signal on the current bar, its walk-forward out-of-sample edge, and the calibrated direction forecast. Returns a Recommendation (direction long/short/flat, entry zone, stop, target(s), conviction, rationale, and the full basis that backed the call) — or an honest 'no actionable edge' flat verdict when any leg disagrees or shows no edge. A directional call requires the forecast, the live signal, and a positive backtested edge to all agree; conviction is DERIVED (forecast probability x backtested edge), never invented, so a marginal edge reads as low conviction. Every result is labeled 'advisory': the app recommends, the user decides and acts. This tool holds no trade key, places no order, and moves no money. Publishes a `recommendation.completed v1` event so a connected viewer renders the advisory call live. `range_start` is the warm-up lookback — request enough history for indicator warm-up, walk-forward folds, and forecast training (several hundred bars). Bars are fetched on miss where the data layer supports it. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `strategy_id` | string | yes | — |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `params` | object \| null | no | `None` |
| `horizon_bars` | integer | no | `1` |
| `flat_band` | number | no | `0.001` |
| `n_splits` | integer | no | `5` |
| `seed` | integer | no | `1729` |

**Returns:** `Recommendation`

| Field | Type |
| --- | --- |
| `symbol` | string |
| `timeframe` | string |
| `direction` | enum["long", "short", "flat"] |
| `entry_zone` | array[any] \| null |
| `stop` | number \| null |
| `targets` | array[number] |
| `conviction` | number |
| `rationale` | array[string] |
| `basis` | RecommendationBasis |
| `label` | string |
| `as_of_bar_ts` | string (date-time) |
| `reason_codes` | array[ReasonCode] |
| `sizing` | VolatilitySizing \| null |
| `regime_context` | RegimeContext \| null |
| `direction_leg` | DirectionLegStatus \| null |

**Source:** [`src/market_analyser/api/mcp_tools/recommend.py`](../../src/market_analyser/api/mcp_tools/recommend.py)

## `recommend_rebalance`

ADVISORY ONLY - turn a DeFi LP out-of-range alert into a single labeled rebalance recommendation: recenter / widen / exit, or an honest 'hold'. Pass watch_id (uses that watch's newest alert, qualified by its CURRENT dwell state - a position that re-entered its range yields hold/no-action) or alert_id (scores that specific fired alert). The direction comes from a stated excursion-depth heuristic (how many range-widths price sits beyond the bound) and every recommendation carries its rationale and the numeric basis behind it (ADR-0029). A healthy in-range position yields 'hold - no action'; missing on-chain detail yields 'hold - insufficient basis', never a guessed direction. This tool holds no trade key, builds no transaction, places no order, and moves no funds - on-chain rebalancing is out of scope by decision (ADR-0072 BA-1 / ADR-0025); the user decides and acts.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer \| null | no | `None` |
| `alert_id` | integer \| null | no | `None` |

**Returns:** `RebalanceRecommendation`

| Field | Type |
| --- | --- |
| `wallet` | string |
| `chain` | string |
| `pool_address` | string |
| `nft_token_id` | integer \| null |
| `action` | enum["recenter", "widen", "exit", "hold"] |
| `rationale` | array[string] |
| `basis` | object |
| `label` | string |
| `as_of` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/recommend_rebalance.py`](../../src/market_analyser/api/mcp_tools/recommend_rebalance.py)

## `run_backtest`

Run a backtest for a single strategy/symbol/timeframe window. Composes the strategy contract (Plan 0002), the engine (Plan 0008), and the persistence layer. The full BacktestResult is written to runs/<run_id>/ on disk and indexed in SQLite; this call returns a compact summary (5 metrics) and the run_id you can use to fetch the full result. Publishes a `run.completed v1` event to the SSE bus so the renderer's BacktestView opens automatically.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `strategy_id` | string | yes | — |
| `symbol` | string | yes | — |
| `timeframe` | enum["15m", "1h", "4h", "1d", "1w"] | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `params` | object | yes | — |
| `commission_bps` | number | no | `0.0` |
| `slippage_bps` | number | no | `0.0` |
| `initial_capital` | number | no | `10000.0` |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/run_backtest.py`](../../src/market_analyser/api/mcp_tools/run_backtest.py)

## `scan_patterns`

Sweep a time range for EVERY candlestick pattern on the cached bars and highlight them all at once: publishes a single `chart.highlight v1` event carrying one marker per detected pattern (multi-bar patterns carry a bar span; doji/neutral patterns are included). Use this instead of calling `highlight_pattern` once per pattern. Reads cached bars only (backfill via get_ohlcv first); an empty/uncached range publishes nothing and returns count=0. Results are derived and NOT persisted — reopening the viewer re-runs the sweep. Optional `patterns` keeps only the named detectors (e.g. ['morning_star','doji']); optional `min_strength` (0..1) drops weak hits. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `patterns` | array[string] \| null | no | `None` |
| `min_strength` | number \| null | no | `None` |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/scan_patterns.py`](../../src/market_analyser/api/mcp_tools/scan_patterns.py)

## `scan_pool_discrepancies`

Screen configured DEX pools for cross-pool price discrepancies, NET OF COST, for one or more canonical pairs (e.g. 'WETH/USDC') at a given trade_size. Combines constant-product and concentrated-liquidity venues: it reads each pool's EXECUTABLE quote (buy_cost = exact-output cost to acquire trade_size base; sell_proceeds = exact-input proceeds from selling it, both already net of the pool's fee and its measured slippage) and returns ranked observations {pair, trade_size, buy_pool, buy_dex, buy_cost, sell_pool, sell_dex, sell_proceeds, est_gas_cost, net_spread, reconstructed_slippage, reconstructed_fees, capturable_at_threshold, capturability_note, queried_at}, where net_spread = max(sell_proceeds) - min(buy_cost) - gas is the honest number (buy at the executably cheapest venue, sell at the dearest). A sub-threshold discrepancy is flagged capturable_at_threshold=false, not dropped. reconstructed_slippage/fees decompose the executable numbers against the marginal reference for auditability (derived, not a second source of truth). IMPORTANT: net_spread is an UPPER BOUND on capturability, not a capture guarantee - an RPC poller sees prices later than a colocated searcher, so a discrepancy visible here may not be capturable in practice (see capturability_note). Facts only - this reports conditions, never a buy/sell/execute call, and it signs nothing and moves no funds. est_gas_cost (quote-token units) and min_net_spread tune the gas assumption and the capturable threshold. Results are bounded to 50 per page: when more remain partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=returned). On failure observations is null and error is a typed reason (unconfigured / config_error / rate_limited / upstream_unavailable / malformed_response).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | ScanPoolDiscrepanciesInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/pool_discrepancies.py`](../../src/market_analyser/api/mcp_tools/pool_discrepancies.py)

## `scan_wallet`

Discover a wallet's DeFi positions from a public EVM address across Ethereum, Base, Arbitrum, and Optimism. Returns {wallet, positions, chains, position_count, total_usd_value, error, message}: positions is a list of decoded positions (each with chain, protocol, kind = lp|lending_supply|lending_borrow|staking, tokens, usd_value) on success; on failure positions is null and error is a typed reason — 'auth' (no Zerion API key is set: set it via the Settings secret endpoint, then retry), 'rate_limited', 'upstream_unavailable', or 'malformed_response' — with a human message. address must be a raw 0x EVM address (40 hex chars); ENS names are not supported. Streams scan_started/scan_progress/scan_completed on the SSE stream. Positions are live (not persisted); values are the source's interpreted figures. When an on-chain RPC source is configured, LP positions are enriched (best-effort) with tick_lower/tick_upper/current_tick/in_range and uncollected_fees; without it those stay null. Data from Zerion (+ RPC).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | ScanWalletInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/scan_wallet.py`](../../src/market_analyser/api/mcp_tools/scan_wallet.py)

## `scan_watchlist`

Rank or filter a supplied symbol list (watchlist) on cached bars by a chosen condition — one watchlist-ranking verb, `rank_by` selects the mode. Returns {rank_by, matches, skipped, scanned_at}: matches carry the mode's per-symbol reading (tie-broken by symbol); skipped lists symbols with too short a history for the mode or no cached bars (backfill via get_ohlcv first). Modes: `squeeze` ranks by TTM squeeze tightness (ADR-0083 trio: bb_width, its trailing 90-window percentile, squeeze_on), most-coiled first; `gainers` ranks by trailing close-to-close % change descending (biggest gainer first) and `losers` the same move ascending (biggest loser first); `momentum` filters by an RSI band [momentum.rsi_min, momentum.rsi_max] and optional momentum.trend (one of up, down, sideways), NO volume gate, ranked by RSI descending; `quality` ranks by a composite 0..100 technical-quality score descending (four factor contributions that sum to the score, plus a liquidity gate); `volume_breakout` keeps only price+volume breakouts (volume_breakout.vol_multiple x trailing average AND clearing the volume_breakout.price_lookback-bar high/low), ranked by multiple descending; `smart_volume` keeps only a volume surge (smart_volume.vol_multiple x average) with RSI inside [smart_volume.rsi_low, smart_volume.rsi_high], ranked by multiple descending. Each mode's extra params live in a nested block named for the mode; modes without extra params take only symbols/timeframe/as_of. Max 25 symbols. Pass `as_of` for historical replay (trailing — no future leak). Conditions only — a ranking is a fact, never buy/sell advice (the `quality` mode is a SCREENING RANK, not a recommendation — use `recommend` for a directional call). Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbols` | array[string] | yes | — |
| `timeframe` | string | yes | — |
| `rank_by` | enum["squeeze", "gainers", "losers", "momentum", "quality", "volume_breakout", "smart_volume"] | yes | — |
| `momentum` | MomentumOpts \| null | no | `None` |
| `volume_breakout` | VolumeBreakoutOpts \| null | no | `None` |
| `smart_volume` | SmartVolumeOpts \| null | no | `None` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `ScanWatchlistResponse`

| Field | Type |
| --- | --- |
| `rank_by` | enum["squeeze", "gainers", "losers", "momentum", "quality", "volume_breakout", "smart_volume"] |
| `matches` | array[SqueezeScanMatch \| GainersLosersMatch \| MomentumScanMatch \| QualityScore \| VolumeBreakout \| SmartVolumeHit] |
| `skipped` | array[string] |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/scan_watchlist.py`](../../src/market_analyser/api/mcp_tools/scan_watchlist.py)

## `screener_query`

Screen a market universe for symbols matching indicator/price filters (e.g. RSI < 30 on US large-caps). Returns the matching rows with their indicator columns plus `queried_at`, the wall-clock time the screen ran. Results are wall-clock-sensitive — there is no historical replay (no as_of). `filters` is a dict keyed by column with operator sub-dicts, e.g. {"RSI": {"lt": 30}, "market_cap_basic": {"gte": 1e10}}; operators are lt/lte/gt/gte/eq/ne (a bare scalar means equality). Data comes from TradingView's public scanner (reverse-engineered; may change without notice).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | ScreenerQueryInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/screener_query.py`](../../src/market_analyser/api/mcp_tools/screener_query.py)

## `search_prediction_markets`

Search prediction markets by free text and get each match with its current odds. Returns {query, markets, count, queried_at, source, error, message}: markets is a list of {market_id, question, outcomes, closed, closes_at, volume_usd, liquidity_usd, queried_at, source}, where outcomes is a list of {label, implied_probability}. implied_probability is the market-implied probability of that outcome in [0, 1] (a prediction market trades each outcome between 0 and 1, and the price IS the money-weighted probability) - the outcomes of a binary market sum to about 1. volume_usd / liquidity_usd are honest-uncertainty hints: a thin-book market's probability is noisier and must not be read as ground truth. Facts only (a market-implied probability is a condition, never a call - no buy/sell/hold advice). limit caps the results (default 20). On failure markets is null and error is a typed reason (rate_limited / upstream_unavailable / malformed_response). Data from Polymarket public endpoints (no account, no funds).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | SearchPredictionMarketsInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/prediction_markets.py`](../../src/market_analyser/api/mcp_tools/prediction_markets.py)

## `search_symbols`

Resolve a loose or free-text name/ticker to fetchable symbols (e.g. 'bitcoin' or 'BTC' -> BTC-USD, Bitcoin USD, Cryptocurrency). Returns {results, queried_at}: results is a list of {symbol, name, exchange, quote_type} in upstream relevance order, where every `symbol` is directly fetchable by get_ohlcv. Use this as the recovery path when get_ohlcv reports unknown_symbol — call search_symbols, then retry get_ohlcv with a returned `symbol`. A zero-match query returns an empty results list (not an error). Data comes from Yahoo Finance's search endpoint (live; no as_of).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | SearchSymbolsInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/search_symbols.py`](../../src/market_analyser/api/mcp_tools/search_symbols.py)

## `sector_rotation`

Rank a self-defined set of crypto sectors (Layer-1, Layer-2, DeFi, Memecoins, AI, DePIN, ...) by equal-weighted constituent momentum over cached bars — the classic 'where is capital rotating' read, for crypto. Crypto has no canonical sector index, so the taxonomy is an in-house versioned config (sector -> a basket of liquid USD-native constituents) and each sector's momentum is the equal-weighted mean of its constituents' trailing `lookback`-bar close-to-close returns. Returns {taxonomy_version, timeframe, lookback, sectors, scanned_at}: `sectors` are ranked hottest-first (complete sectors before incomplete ones, momentum descending), each carrying its equal-weight momentum, n_priced, a `complete` flag (>= the priced floor), its best/worst constituents (`leaders` / `laggards`, return %), and any `skipped` constituents (no cached bars / too short a history). A sector with too few priced constituents is reported `complete=false` and ranked last rather than silently mixed in; `momentum` is null when nothing priced. Pass `lookback` (bars, default 30) and `as_of` for historical replay (trailing — no future leak). Conditions only — a rotation reading is a fact about relative momentum, never a buy/sell call; use `recommend` for a directional call. Constituents are priced through the existing USD-native sources; backfill via get_ohlcv if a sector reports many skipped. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `timeframe` | string | yes | — |
| `lookback` | integer | no | `30` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `SectorRotationResponse`

| Field | Type |
| --- | --- |
| `taxonomy_version` | string |
| `timeframe` | string |
| `lookback` | integer |
| `sectors` | array[SectorMomentum] |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/sector_rotation.py`](../../src/market_analyser/api/mcp_tools/sector_rotation.py)

## `sentiment`

Summarise crowd/news sentiment for a symbol over a window; `source` selects the feed. Returns {score (in [-1, 1]), window, source, breakdown (positive/negative/neutral counts), queried_at}. source='news': mean VADER compound over each recent RSS headline (source tag 'rss-vader'); no headlines in the window returns score 0.0 with an all-zero breakdown (zero, not unknown). source='stocktwits': (bullish - bearish) / labeled-post count from StockTwits' explicit post labels (no NLP; source tag 'stocktwits') — the payload also echoes the upper-cased `symbol`; pass the exact StockTwits ticker (AAPL for stocks, the '.X' suffix for crypto like BTC.X/ETH.X); patchy small-cap coverage returns an all-zero breakdown (neutral, not unknown), a symbol StockTwits does not track is an error. source='reddit': keyless, or keyed app-only OAuth when configured (reddit_client_id + reddit_client_secret) — an upvote-weighted keyword-lexicon score over one fixed multi-subreddit crowd group (r/CryptoCurrency+Bitcoin+wallstreetbets+stocks+investing) searched for the symbol (source tag 'reddit'); the payload adds a `label` (Strongly Bullish..Strongly Bearish) and `sample_size` (scored-post count); Reddit rate-limits hard and 403-walls keyless JSON from some networks, so an empty result (score 0.0, all-zero breakdown) may be a rate-limit or block rather than genuine silence — never fabricated. source='x': KEYED X (Twitter) / social crowd sentiment via the LunarCrush aggregator (source tag 'x'): the vendor's 0..100 aggregate maps linearly to the signed score, per-network polarity interaction counts form the breakdown, and the payload adds `label` and `sample_size` (polarity-classified interactions) like the reddit mode. Requires the `lunarcrush_api_key` secret — absent it the source is inert and returns an honest-empty result with a `note` (not an error). The upstream read is a current social snapshot (roughly 24h), so `window` does not narrow it; the funded tier budget is small (~100 requests/day, 4/min — responses are cached ~15 min), so a keyed empty result may be a rate-limit rather than silence — never fabricated. `window` is one of 1h/4h/24h/7d. Wall-clock-sensitive — no historical replay (no as_of). This is a CONDITION (crowd/news mood), never buy/sell advice.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | SentimentInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/sentiment.py`](../../src/market_analyser/api/mcp_tools/sentiment.py)

## `show_chart`

Render a chart in the Electron viewer. Publishes a `chart.show v1` event to the SSE stream. The renderer mounts/switches to the requested symbol+timeframe and renders the requested window with the supplied overlays. Overlay `kind`s: indicator overlays `ema`/`sma`/`rsi`/`macd`/`bbands`/`supertrend`/`ichimoku`/`obv` (computed and drawn client-side — `supertrend` takes an optional `multiplier`, `ichimoku` optional `conversion`/`base`/`span_b`/`displacement` periods defaulting to 9/26/52/26, `obv` carries no fields and draws in its own pane) and `price_line` (a labelled horizontal line for support/resistance). Returns immediately whether or not a viewer is connected — events are ephemeral; reopening Electron after a call to this tool will not replay it.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `overlays` | array[object] \| null | no | `None` |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/show_chart.py`](../../src/market_analyser/api/mcp_tools/show_chart.py)

## `technical_read`

ADVISORY ONLY, LESSER TIER — a single-indicator technical read: the mechanical direction (long/short/flat) of ONE curated regime indicator by its textbook rule, with NO conviction and NO entry/stop/target levels. This is NOT the fully-corroborated `recommend` call — it is one named indicator, said out loud, and nothing more; there is no ML forecast, no walk-forward edge, no cross-leg agreement behind it. It may say long while `recommend` says flat — that is thin vs. corroborated, not a contradiction. The user reads it and sizes it themselves. Eligible indicators: ema_stack, ichimoku, macd, supertrend. supertrend -> its direction; ema_stack -> fast-vs-slow EMA and close; macd -> histogram sign; ichimoku -> price vs the displaced cloud with tenkan/kijun. Returns a TechnicalRead (direction, the indicator's regime_state read, and the mechanical rule as rationale). Reads the last CLOSED bar; requires bars already cached for the window (backfill via get_ohlcv first). Publishes `technical_read.completed v1` so a connected viewer renders the read live. This tool holds no trade key, places no order, moves no money. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `indicator_id` | string | yes | — |

**Returns:** `TechnicalRead`

| Field | Type |
| --- | --- |
| `symbol` | string |
| `timeframe` | string |
| `as_of_bar_ts` | string (date-time) |
| `indicator_id` | enum["supertrend", "ema_stack", "macd", "ichimoku"] |
| `direction` | enum["long", "short", "flat"] |
| `regime_state` | string |
| `rationale` | array[string] |

**Source:** [`src/market_analyser/api/mcp_tools/technical_read.py`](../../src/market_analyser/api/mcp_tools/technical_read.py)

## `update_chart`

Apply a delta to the currently-rendered chart. Publishes a `chart.update v1` event. Any subset of {overlays, range_start, range_end, focus_bar} may be supplied; unset fields are not carried on the wire (the renderer merges the delta into its current state). Overlay `kind`s: indicator overlays `ema`/`sma`/`rsi`/`macd`/`bbands`/`supertrend`/`ichimoku`/`obv` (computed and drawn client-side — `supertrend` takes an optional `multiplier`, `ichimoku` optional `conversion`/`base`/`span_b`/`displacement` periods defaulting to 9/26/52/26, `obv` carries no fields and draws in its own pane) and `price_line` (a labelled horizontal line for support/resistance). If no chart for `symbol`+`timeframe` is currently open in the viewer, the renderer treats this as a `chart.show`.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `overlays` | array[object] \| null | no | `None` |
| `range_start` | string (date-time) \| null | no | `None` |
| `range_end` | string (date-time) \| null | no | `None` |
| `focus_bar` | string (date-time) \| null | no | `None` |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/update_chart.py`](../../src/market_analyser/api/mcp_tools/update_chart.py)

## `volume_read`

Read one symbol's recent volume against its price move on cached bars; `kind` selects the read. Returns {kind, result, partial_reason, scanned_at}: result is the mode's read (null with partial_reason='no_bars' when nothing is cached — backfill via get_ohlcv first), scanned_at is run provenance. Modes: kind='confirmation' — how well volume backs the recent move (VolumeConfirmation: score, a 0..1 share of directional volume aligned with the net move over the trailing confirmation.lookback bars — high when the move is carried by trend volume, low on a counter-trend divergence — plus confirmed, direction, and the supportive/opposing volume figures). kind='counter_trend' — the volume decomposed with-trend vs counter-trend, anchored to the symbol's canonical trend (the same up/down/sideways label analyze_symbol reports, NOT the net move): result.bars lists each trailing counter_trend.lookback bar with its direction, trailing relative volume, and counter-trend flag, and result.counter_trend_volume_share is the share of directional volume on the counter-trend bars (high = a volume divergence against the trend); when the trend is sideways there is nothing to run counter to, anchored_to_sideways is true and the share is null. Pass `as_of` for historical replay (trailing — no future leak). Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `kind` | enum["confirmation", "counter_trend"] | yes | — |
| `confirmation` | ConfirmationOpts \| null | no | `None` |
| `counter_trend` | CounterTrendOpts \| null | no | `None` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `VolumeReadResponse`

| Field | Type |
| --- | --- |
| `kind` | enum["confirmation", "counter_trend"] |
| `result` | VolumeConfirmation \| CounterTrendVolume \| null |
| `partial_reason` | string \| null |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/volume_read.py`](../../src/market_analyser/api/mcp_tools/volume_read.py)

## `walk_forward_backtest`

Evaluate one strategy across n_splits rolling out-of-sample folds and return per-fold metrics plus an aggregate (mean/std of total_return and sharpe) and a full-run baseline. The bar series is partitioned into contiguous, non-overlapping test windows — fold k's bars strictly follow fold k-1's, so there is no lookahead. This is rolling out-of-sample EVALUATION, not walk-forward optimization: fixed params per fold, no re-fitting. params defaults to the strategy's own defaults. Not persisted. n_splits must be >=1 and <= the number of bars. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `strategy_id` | string | yes | — |
| `symbol` | string | yes | — |
| `timeframe` | enum["15m", "1h", "4h", "1d", "1w"] | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `n_splits` | integer | no | `4` |
| `params` | object \| null | no | `None` |
| `commission_bps` | number | no | `0.0` |
| `slippage_bps` | number | no | `0.0` |
| `initial_capital` | number | no | `10000.0` |

**Returns:** `WalkForwardResult`

| Field | Type |
| --- | --- |
| `strategy_id` | string |
| `symbol` | string |
| `timeframe` | string |
| `n_splits` | integer |
| `folds` | array[WalkForwardFold] |
| `aggregate` | object |
| `full_run_baseline` | BacktestMetrics |

**Source:** [`src/market_analyser/api/mcp_tools/walk_forward_backtest.py`](../../src/market_analyser/api/mcp_tools/walk_forward_backtest.py)

## `write_annotation`

Write a chart annotation (bullish/bearish marker on a single candle). Returns the persisted record with its id and created_at. `label` is the hover text; `agent_id` is your opaque identifier (defaults to 'unknown').

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `event_ts` | string (date-time) | yes | — |
| `kind` | AnnotationKind | yes | — |
| `label` | string \| null | no | `None` |
| `agent_id` | string | no | `"unknown"` |

**Returns:** `Annotation`

| Field | Type |
| --- | --- |
| `id` | string |
| `symbol` | string |
| `timeframe` | string |
| `event_ts` | string (date-time) |
| `kind` | AnnotationKind |
| `label` | string \| null |
| `agent_id` | string |
| `created_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/write_annotation.py`](../../src/market_analyser/api/mcp_tools/write_annotation.py)

<!-- GENERATED FILE - DO NOT EDIT BY HAND.
     Regenerate: uv run python -m market_analyser.apiref  (or: pnpm gen:api-docs)
     Rendered from the live sidecar; see Plan 0070 / ADR-0064. -->

# MCP tools

The 50 agent-callable MCP tools mounted at `/mcp`, from the live FastMCP registry.

| Tool | Summary |
| --- | --- |
| [`analyze_symbol`](#analyzesymbol) | Compute a full technical-condition snapshot for one symbol over cached bars: trend (up/down/sideways), momentum stance, latest indicator values (RSI, MACD, Bollinger, ATR, ADX, Supertrend, plus trailing RSI/ATR percentiles), trailing support/resistance levels, any candlestick patterns on the most recent bars, and the active classical chart patterns (head & shoulders, doubles, triangles, wedges — forming or freshly confirmed) still in play. |
| [`backfill_ohlcv`](#backfillohlcv) | Pre-warm the local cache for a symbol/timeframe over [start, end] by fetching any missing bars from the upstream in the background. |
| [`bitcoin_market_pulse`](#bitcoinmarketpulse) | Get the current crypto macro picture in one call (CoinGecko, free public API): BTC price and 24h change, BTC dominance %, total crypto market cap and its 24h change, plus a neutral `regime` label describing market STRUCTURE (btc_led / alt_structure / risk_off_structure / neutral). |
| [`btc_cycle_snapshot`](#btccyclesnapshot) | Get the current BTC cycle picture in one call: days since the 2024-04-19 halving, ESTIMATED days to the next (the next-halving date is an estimate, hence the _est suffix), the cycle phase fraction (0.0 just after a halving, 1.0 at the estimated next), Mayer Multiple (close / 200-day SMA) and distance to the 200-week MA (close / SMA1400 - 1) from cached daily BTC-USD bars, plus the latest Fear & Greed and BTC dominance with 7/30-day deltas from the stored metric series, plus on-chain MVRV (market value / realized value) with its trailing full-history percentile. |
| [`compare_strategies`](#comparestrategies) | Run every reference strategy on one symbol/timeframe/window at its default parameters and return a leaderboard ranked by a chosen metric. |
| [`compute_wallet_pnl`](#computewalletpnl) | Reconstruct a wallet's DeFi profitability from its decoded on-chain transaction history (Ethereum, Base, Arbitrum, Optimism): per-position and total realized/unrealized P&L under average-cost lots, every leg valued at its own block timestamp - never trusting an aggregator's number. |
| [`create_watch`](#createwatch) | Create a persisted watch the sidecar's alerting scheduler evaluates on an interval (ADR-0055). |
| [`crypto_fear_greed`](#cryptofeargreed) | Get the current crypto Fear & Greed index (Alternative.me): a single 0-100 value with a label (Extreme Fear / Fear / Neutral / Greed / Extreme Greed). |
| [`delete_watch`](#deletewatch) | Delete a watch by id, including its alert history. |
| [`derivatives_snapshot`](#derivativessnapshot) | Get the Binance USDS-M derivatives picture for one contract symbol (e.g. |
| [`detect_chart_patterns`](#detectchartpatterns) | Detect classical chart patterns on the cached bars and draw them on the chart in one call: recognises head & shoulders (+inverse), double top/bottom, ascending/descending/symmetrical triangles, and rising/falling wedges over confirmed swing pivots, returns the typed hits as data (pattern, forming/confirmed state, direction, pivots, defining lines, measured-move target, strength), AND publishes a single `chart.trendlines v1` event carrying one trendline per hit line (dashed = forming, solid = confirmed) onto the chart already showing that symbol/timeframe. |
| [`detect_levels`](#detectlevels) | Detect support/resistance levels on the cached bars and draw them on the chart in one call: clusters confirmed swing pivots into zones, ranks each zone's strength by touch count weighted by the volume traded at that price (volume-by-price), returns the ranked levels as data, AND publishes a single `chart.show v1` event carrying one `price_line` overlay per level (role support/resistance, labels S1/R1/... |
| [`evaluate_signals`](#evaluatesignals) | Evaluate a strategy against the CURRENT bar of one symbol — a live signal read, not a historical backtest. |
| [`find_convergence_opportunities`](#findconvergenceopportunities) | Screen prediction markets matching a query for CONVERGENCE opportunities — markets nearing resolution whose top outcome is near-certain, where a price converging to 1.00 leaves a few percent of implied upside. |
| [`forecast`](#forecast) | Forecast the price DIRECTION of a cached symbol over one or more horizons, each as a calibrated up/down/flat probability or an honest 'no edge over baseline' verdict. |
| [`forecast_regime`](#forecastregime) | Forecast the market REGIME TRANSITION (not direction) of a cached symbol: the current regime (a trailing trend x volatility state, e.g. |
| [`forecast_volatility`](#forecastvolatility) | Forecast realised VOLATILITY (not direction) of a cached symbol over the next horizon_bars: the predicted per-bar volatility with a 1-sigma out-of-sample band, scored against deterministic EWMA + persistence baselines by QLIKE. |
| [`get_backtest`](#getbacktest) | Fetch a persisted backtest's full detail by run_id (the id run_backtest returns). |
| [`get_metric_series`](#getmetricseries) | Read a stored metric time series (ADR-0051): points of one registered series_id over an inclusive [start, end] epoch-second window, sorted by ts ascending. |
| [`get_ohlcv`](#getohlcv) | Read OHLCV bars for one symbol over a [start, end] window. |
| [`get_pending_ui_events`](#getpendinguievents) | Read recent UI events the user generated in the chart viewer — drag-selected ranges, single bar clicks, and agent-mode toggles. |
| [`get_track_record`](#gettrackrecord) | Read the advisor's own live track record (ADR-0075): how its past recommendations turned out against realized price, scored path-dependently (did the stop or a target hit first). |
| [`highlight_pattern`](#highlightpattern) | Highlight a pattern on a chart. |
| [`list_alerts`](#listalerts) | Read fired-alert history, newest first, optionally scoped to one watch_id. |
| [`list_annotations`](#listannotations) | List annotations for a symbol/timeframe over a [start, end] window. |
| [`list_watches`](#listwatches) | List the persisted watches (id, symbol, timeframe, kind, params, interval_seconds, enabled, last_state, created_at), ordered by id. |
| [`market_snapshot`](#marketsnapshot) | Get a point-in-time global market snapshot: live quotes for a fixed basket — S&P 500 (^GSPC), NASDAQ (^IXIC), VIX (^VIX), Bitcoin (BTC-USD), Ethereum (ETH-USD), EUR/USD (EURUSD=X), SPY, and GLD. |
| [`multi_timeframe_analysis`](#multitimeframeanalysis) | Report whether one symbol's trend is aligned across a ladder of timeframes. |
| [`news_for`](#newsfor) | Fetch recent news headlines for a symbol (or across all feeds when `symbol` is null) from a curated set of free RSS feeds (CoinDesk, CoinTelegraph, Yahoo Finance, MarketWatch, CNBC). |
| [`portfolio_summary`](#portfoliosummary) | Aggregate cross-venue holdings into one read-only view (facts only, no recommendation of any kind): the Binance account leg (spot balances + USDS-M futures positions, read via the read-only API key), the DeFi leg (wallet discovery across Ethereum/Base/Arbitrum/Optimism when a 0x wallet address is given, with average-cost basis joined from the reconstructed on-chain history), and the manual positions file (positions/portfolio.json). |
| [`prediction_market_odds`](#predictionmarketodds) | Get one prediction market's current outcomes and implied probabilities by market_id (from search_prediction_markets). |
| [`quote_for`](#quotefor) | Get a live quote for one symbol: price, change_pct, previous_close, day high/low, 52-week high/low, currency, market_state (REGULAR/PRE/POST/CLOSED) and volume. |
| [`recommend`](#recommend) | ADVISORY ONLY — fuse the four analyst outputs for one symbol into a single labeled trade recommendation: the technical condition snapshot, the named strategy's live signal on the current bar, its walk-forward out-of-sample edge, and the calibrated direction forecast. |
| [`run_backtest`](#runbacktest) | Run a backtest for a single strategy/symbol/timeframe window. |
| [`scan_patterns`](#scanpatterns) | Sweep a time range for EVERY candlestick pattern on the cached bars and highlight them all at once: publishes a single `chart.highlight v1` event carrying one marker per detected pattern (multi-bar patterns carry a bar span; doji/neutral patterns are included). |
| [`scan_pool_discrepancies`](#scanpooldiscrepancies) | Screen configured DEX pools for cross-pool price discrepancies, NET OF COST, for one or more canonical pairs (e.g. |
| [`scan_wallet`](#scanwallet) | Discover a wallet's DeFi positions from a public EVM address across Ethereum, Base, Arbitrum, and Optimism. |
| [`screener_query`](#screenerquery) | Screen a market universe for symbols matching indicator/price filters (e.g. |
| [`search_prediction_markets`](#searchpredictionmarkets) | Search prediction markets by free text and get each match with its current odds. |
| [`search_symbols`](#searchsymbols) | Resolve a loose or free-text name/ticker to fetchable symbols (e.g. |
| [`sentiment_for_news`](#sentimentfornews) | Summarise news sentiment for a symbol over a window by running VADER over each recent headline and aggregating. |
| [`show_chart`](#showchart) | Render a chart in the Electron viewer. |
| [`smart_volume`](#smartvolume) | Scan a supplied symbol list (watchlist) for a volume surge with RSI in a band on cached bars. |
| [`stocktwits_sentiment`](#stocktwitssentiment) | Summarise StockTwits crowd sentiment for a symbol over a window by counting users' explicit Bullish/Bearish post labels (no NLP model). |
| [`technical_read`](#technicalread) | ADVISORY ONLY, LESSER TIER — a single-indicator technical read: the mechanical direction (long/short/flat) of ONE curated regime indicator by its textbook rule, with NO conviction and NO entry/stop/target levels. |
| [`update_chart`](#updatechart) | Apply a delta to the currently-rendered chart. |
| [`volume_breakout`](#volumebreakout) | Scan a supplied symbol list (watchlist) for price+volume breakouts on cached bars. |
| [`volume_confirmation`](#volumeconfirmation) | Report how well volume backs one symbol's recent price move on cached bars. |
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

Reconstruct a wallet's DeFi profitability from its decoded on-chain transaction history (Ethereum, Base, Arbitrum, Optimism): per-position and total realized/unrealized P&L under average-cost lots, every leg valued at its own block timestamp - never trusting an aggregator's number. Returns {wallet (masked), positions: [{position_id, realized_usd, unrealized_usd, cost_basis_usd, vs_hodl_usd (LP only), incomplete, notes, unclaimed_rewards}], position_count, incomplete, realized_usd, unrealized_usd, unclaimed_rewards, crosscheck_zerion_total, crosscheck_warning, error, message}. unclaimed_rewards is a labeled CURRENT-STATE on-chain read of gauge emissions owed-but-not-yet-claimed ([{symbol, amount, usd_value}], per position + a wallet roll-up); it is deliberately kept OUT of realized/unrealized and out of the deterministic re-run guarantee (there is no claim tx to replay), null when a position owes nothing. A position with a missing historical price or an unbooked event kind reports null figures with incomplete=true and a naming note - never a silently-zeroed number; wallet totals are null whenever any position is incomplete. crosscheck_zerion_total is Zerion's own FIFO figure, advisory only; crosscheck_warning flags gross (order-of-magnitude or sign) divergence - small differences are expected because the methods differ (average-cost vs FIFO). refresh=true pulls new transactions before replaying; the default replays the immutable cached history (deterministic re-run, zero upstream calls). First pull of a long history is slow (rate-limit-spaced pagination + per-event price lookups); re-runs read SQLite. On failure positions is null and error is 'auth' (no Zerion API key set - set it via the Settings secret endpoint), 'rate_limited', 'upstream_unavailable', or 'malformed_response'. address must be a raw 0x EVM address; ENS is not supported. Streams pnl_started/pnl_completed/pnl_failed on the SSE stream. Data from Zerion (history) + DefiLlama (historical prices).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | ComputeWalletPnlInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/compute_wallet_pnl.py`](../../src/market_analyser/api/mcp_tools/compute_wallet_pnl.py)

## `create_watch`

Create a persisted watch the sidecar's alerting scheduler evaluates on an interval (ADR-0055). Three kinds: 'indicator_threshold' (params: {indicator, operator, level} with operator one of < <= > >= and indicator one of adx, atr, bb_lower, bb_middle, bb_pct_b, bb_upper, close, macd, macd_hist, macd_signal, minus_di, obv, obv_slope, plus_di, rel_volume, rsi, supertrend, supertrend_direction, vol_pct90, vol_sma20, volume, vwap), 'pattern' (params: {pattern} one of bearish_engulfing, bearish_harami, bullish_engulfing, bullish_harami, dark_cloud_cover, doji, evening_star, hammer, hanging_man, marubozu, morning_star, piercing_line, three_black_crows, three_white_soldiers), and 'strategy_signal' (params: {strategy_id, params} — fires when the strategy emits a fresh signal on the latest closed bar). Alerts are EDGE-TRIGGERED: one alert per false->true transition of the condition, evaluated on closed bars only. interval_seconds defaults to the timeframe's bar period. Alerts are condition facts, never buy/sell advice. Delivery: `alert.triggered v1` SSE event (viewer toast) + the pending-events poll + `list_alerts` history. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `kind` | string | yes | — |
| `params` | object | yes | — |
| `interval_seconds` | integer \| null | no | `None` |
| `enabled` | boolean | no | `True` |

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

**Source:** [`src/market_analyser/api/mcp_tools/watches.py`](../../src/market_analyser/api/mcp_tools/watches.py)

## `crypto_fear_greed`

Get the current crypto Fear & Greed index (Alternative.me): a single 0-100 value with a label (Extreme Fear / Fear / Neutral / Greed / Extreme Greed). Takes no arguments. Returns `value`, `classification`, `published_at` (when the index was published upstream), `queried_at`, and `source`. The reading is market-wide (not per-symbol), wall-clock-current (no historical replay), and updates roughly once a day — asking again within the hour returns the same value.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | CryptoFearGreedInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/crypto_fear_greed.py`](../../src/market_analyser/api/mcp_tools/crypto_fear_greed.py)

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

## `find_convergence_opportunities`

Screen prediction markets matching a query for CONVERGENCE opportunities — markets nearing resolution whose top outcome is near-certain, where a price converging to 1.00 leaves a few percent of implied upside. Returns ranked opportunities {market_id, question, outcome_label, implied_probability, implied_return_if_right, time_to_resolution, capital_lockup_note, liquidity_caution, resolution_risk {level, reasons}, volume_usd, closes_at, queried_at, source}. implied_return_if_right = (1 - price) / price is GROSS of the resolution tail — it is NOT expected value; the tail lives in resolution_risk (a LABELED HEURISTIC over multi-outcome wording, thin/unknown book, and dispute-prone question terms — never a guarantee), liquidity_caution, and capital_lockup_note (market close is not settlement — UMA resolution can lag or be disputed, locking capital). IMPORTANT: these are facts with their risks attached, never a call — this reports conditions and never tells you to take a position; it signs nothing and moves no funds. Filter knobs: max_days_to_close (window, default 7), min_confidence (probability floor, default 0.90), thin_book_volume_usd (thin-book threshold, default 50000). Results are bounded to 50 per page: when more remain partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=returned). On failure opportunities is null and error is a typed reason (rate_limited / upstream_unavailable / malformed_response). Data from Polymarket public endpoints (no account, no funds).

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | FindConvergenceOpportunitiesInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/prediction_screener.py`](../../src/market_analyser/api/mcp_tools/prediction_screener.py)

## `forecast`

Forecast the price DIRECTION of a cached symbol over one or more horizons, each as a calibrated up/down/flat probability or an honest 'no edge over baseline' verdict. Horizons default to 1/5/21 bars on 1d (next-day / ~1w / ~1mo) and to next-bar only on other timeframes; pass horizons=[...] to override. Each horizon trains and walk-forward-validates its OWN model and passes or fails the naive-baseline gate (persistence + majority-class) INDEPENDENTLY — 'edge at 1d, no edge at 1mo' is a normal result; a failed horizon ships prob_*=null with its validation basis. Features: the target symbol's own OHLCV indicators plus BTC cycle features (halving clock, Mayer Multiple, 200W-MA distance) and exogenous series (Fear & Greed, BTC dominance, funding rate, open interest, MVRV) joined lag-1 as-of at bar open, so publication-lag lookahead is structurally impossible. Feature sets form a fixed ladder selected richest-first per call by exogenous history depth: v2-full (all five series) -> v2-deep (F&G/funding/MVRV only, the deep-history tier) -> v1 (OHLCV only); provenance lists exactly the selected tier's series under series_inputs (empty for v1) and provenance.fallback_reason names every richer tier skipped with its surviving-row count (absent when v2-full trained; check feature_set_id for the tier used). Each block carries out-of-sample skill, baseline skill, edge_margin = skill - baseline_skill, and edge_strength ('no_edge' / 'marginal' / 'clear'); treat a high prob_* under a 'marginal' edge as thin, not near-certain. This is a CONDITION (a probability), never a buy/sell recommendation and never a price level. Requires bars already cached for the window (backfill via get_ohlcv first). Supported timeframes: 1d, 1h, 15m, 4h, 1w.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `horizons` | array[integer] \| null | no | `None` |
| `flat_band` | number | no | `0.001` |
| `n_splits` | integer | no | `5` |
| `seed` | integer | no | `1729` |

**Returns:** `MultiHorizonForecastResult`

| Field | Type |
| --- | --- |
| `symbol` | string |
| `timeframe` | string |
| `as_of_bar_ts` | string (date-time) |
| `feature_set_id` | string |
| `horizons` | array[HorizonForecast] |

**Source:** [`src/market_analyser/api/mcp_tools/forecast.py`](../../src/market_analyser/api/mcp_tools/forecast.py)

## `forecast_regime`

Forecast the market REGIME TRANSITION (not direction) of a cached symbol: the current regime (a trailing trend x volatility state, e.g. up_quiet / down_volatile) and a probability distribution over the next-period regime horizon_bars ahead, scored against a persistence baseline (regime unchanged) by the Brier score. beats_baseline is the honest gate (the classifier must beat persistence out-of-sample); regimes are sticky, so persistence is a strong baseline and beating it is a real signal. The trend axis is the same classifier the analyst snapshot uses; the volatility axis splits ATR% at its trailing median. Features use the richest-first tier ladder (v2-full -> v2-deep -> v1); provenance names the tier, its series, any skipped tier, and the top out-of-sample permutation-importance drivers. Distinct from bitcoin_market_pulse's whole-market regime: this is per-symbol and predictive. A CONDITION, never a buy/sell recommendation. Requires bars already cached for the window (backfill via get_ohlcv first). Supported timeframes: 1d, 1h, 15m, 4h, 1w.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `horizon_bars` | integer | no | `5` |
| `n_splits` | integer | no | `5` |
| `seed` | integer | no | `1729` |

**Returns:** `RegimeForecast`

| Field | Type |
| --- | --- |
| `symbol` | string |
| `timeframe` | string |
| `as_of_bar_ts` | string (date-time) |
| `horizon_bars` | integer |
| `current_regime` | RegimeState \| null |
| `transition_probs` | object \| null |
| `beats_baseline` | boolean |
| `score_margin` | number \| null |
| `validation` | RegimeValidation |
| `provenance` | ForecastProvenance \| null |

**Source:** [`src/market_analyser/api/mcp_tools/forecast_regime.py`](../../src/market_analyser/api/mcp_tools/forecast_regime.py)

## `forecast_volatility`

Forecast realised VOLATILITY (not direction) of a cached symbol over the next horizon_bars: the predicted per-bar volatility with a 1-sigma out-of-sample band, scored against deterministic EWMA + persistence baselines by QLIKE. beats_baseline is the honest gate (the model must beat the better baseline out-of-sample); when it does not, trust baseline_vol (the winning baseline's current reading), which is always surfaced. Features use the same richest-first tier ladder as `forecast` (v2-full -> v2-deep -> v1 by exogenous history depth); provenance names the tier (feature_set_id), its series (series_inputs), any skipped tier (fallback_reason), and the top out-of-sample permutation-importance drivers. This is a CONDITION (a magnitude), never a buy/sell recommendation and never a price level; use it for position sizing and stop distance. Requires bars already cached for the window (backfill via get_ohlcv first). Supported timeframes: 1d, 1h, 15m, 4h, 1w.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `range_start` | string (date-time) | yes | — |
| `range_end` | string (date-time) | yes | — |
| `horizon_bars` | integer | no | `5` |
| `n_splits` | integer | no | `5` |
| `seed` | integer | no | `1729` |

**Returns:** `VolatilityForecast`

| Field | Type |
| --- | --- |
| `symbol` | string |
| `timeframe` | string |
| `as_of_bar_ts` | string (date-time) |
| `horizon_bars` | integer |
| `predicted_vol` | number \| null |
| `band` | array[any] \| null |
| `baseline_vol` | number \| null |
| `baseline_kind` | enum["persistence", "ewma"] \| null |
| `beats_baseline` | boolean |
| `score_margin` | number \| null |
| `validation` | VolatilityValidation |
| `provenance` | ForecastProvenance \| null |

**Source:** [`src/market_analyser/api/mcp_tools/forecast_volatility.py`](../../src/market_analyser/api/mcp_tools/forecast_volatility.py)

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

Read recent UI events the user generated in the chart viewer — drag-selected ranges, single bar clicks, and agent-mode toggles. Events are buffered ONLY while **agent mode** is ON; when it is OFF this returns an empty list. By default (drain=True) each call drains the events it returns, so consecutive draining reads return disjoint sets — call it when you are ready to act on the user's gestures. Pass drain=False to peek without consuming. `since` returns only events stamped strictly after that timestamp. The same buffer is also exposed (non-draining) as the MCP resource ui-events://recent, which you can subscribe to for update notifications; dedupe across the tool and the resource on each event's `event_id`.

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

Screen configured DEX pools for cross-pool price discrepancies, NET OF COST, for one or more canonical pairs (e.g. 'WETH/USDC') at a given trade_size. Reads each configured pool's on-chain price and returns ranked observations {pair, trade_size, buy_pool, buy_dex, sell_pool, sell_dex, buy_price, sell_price, gross_spread, est_gas_cost, est_slippage, est_fees, net_spread, capturable_at_threshold, capturability_note, queried_at}, where net_spread = gross - gas - slippage - fees is the honest number (a gross spread is never reported as the opportunity). A sub-threshold discrepancy is flagged capturable_at_threshold=false, not dropped. IMPORTANT: net_spread is an UPPER BOUND on capturability, not a capture guarantee - an RPC poller sees prices later than a colocated searcher, so a discrepancy visible here may not be capturable in practice (see capturability_note). Facts only - this reports conditions, never a buy/sell/execute call, and it signs nothing and moves no funds. est_gas_cost (quote-token units) and min_net_spread tune the cost model and the capturable threshold. Results are bounded to 50 per page: when more remain partial_reason='too_large' and total_available/offset/returned tell you how to page (call again with offset=returned). On failure observations is null and error is a typed reason (unconfigured / config_error / rate_limited / upstream_unavailable / malformed_response).

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

## `sentiment_for_news`

Summarise news sentiment for a symbol over a window by running VADER over each recent headline and aggregating. Returns `score` (mean compound in [-1, 1]), `window`, `source` ('rss-vader'), a `breakdown` of positive/negative/neutral headline counts, and `queried_at`. No news in the window returns score 0.0 with an all-zero breakdown (zero, not unknown). `window` is one of 1h/4h/24h/7d. Wall-clock-sensitive — no historical replay.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | SentimentForNewsInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/sentiment_for_news.py`](../../src/market_analyser/api/mcp_tools/sentiment_for_news.py)

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

## `smart_volume`

Scan a supplied symbol list (watchlist) for a volume surge with RSI in a band on cached bars. A symbol qualifies when its latest bar's volume is at least `vol_multiple` times its trailing average AND the latest RSI sits inside [rsi_low, rsi_high]. Returns {matches, skipped, scanned_at}: matches are the qualifying symbols only, each with volume_multiple and rsi, sorted by multiple descending then symbol; skipped lists symbols with no cached bars (backfill via get_ohlcv first). Max 25 symbols. Pass `as_of` for historical replay (trailing — no future leak). Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbols` | array[string] | yes | — |
| `timeframe` | string | yes | — |
| `rsi_low` | number | no | `40.0` |
| `rsi_high` | number | no | `60.0` |
| `vol_multiple` | number | no | `1.5` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `SmartVolumeScanResponse`

| Field | Type |
| --- | --- |
| `matches` | array[SmartVolumeHit] |
| `skipped` | array[string] |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/smart_volume.py`](../../src/market_analyser/api/mcp_tools/smart_volume.py)

## `stocktwits_sentiment`

Summarise StockTwits crowd sentiment for a symbol over a window by counting users' explicit Bullish/Bearish post labels (no NLP model). Returns `symbol` (upper-cased), `score` ((bullish - bearish) / labeled count, in [-1, 1]), `window`, `source` ('stocktwits'), a `breakdown` of positive/negative/neutral post counts, and `queried_at`. Pass the exact StockTwits ticker: a plain symbol for stocks (AAPL) and the '.X' suffix for crypto (BTC.X, ETH.X). Patchy coverage on small-caps returns an all-zero breakdown (neutral, not unknown); a symbol StockTwits does not track is an error. `window` is one of 1h/4h/24h/7d. Wall-clock-sensitive — no historical replay.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `params` | StockTwitsSentimentInput | yes | — |

**Returns:** `dict[str, Any]`

**Source:** [`src/market_analyser/api/mcp_tools/stocktwits_sentiment.py`](../../src/market_analyser/api/mcp_tools/stocktwits_sentiment.py)

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

## `volume_breakout`

Scan a supplied symbol list (watchlist) for price+volume breakouts on cached bars. A symbol breaks out when its latest bar's volume is at least `vol_multiple` times its trailing average AND the close clears its trailing `price_lookback`-bar high (bullish) or low (bearish). Returns {matches, skipped, scanned_at}: matches are the breakouts only, each with direction, volume_multiple, and the broken price level, sorted by multiple descending then symbol; skipped lists symbols with no cached bars (backfill via get_ohlcv first). Max 25 symbols. Pass `as_of` for historical replay (trailing — no future leak). Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbols` | array[string] | yes | — |
| `timeframe` | string | yes | — |
| `vol_multiple` | number | no | `2.0` |
| `price_lookback` | integer | no | `20` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `VolumeBreakoutScanResponse`

| Field | Type |
| --- | --- |
| `matches` | array[VolumeBreakout] |
| `skipped` | array[string] |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/volume_breakout.py`](../../src/market_analyser/api/mcp_tools/volume_breakout.py)

## `volume_confirmation`

Report how well volume backs one symbol's recent price move on cached bars. Returns {result, partial_reason, scanned_at}: result.score is a 0..1 share of directional volume aligned with the net move over the trailing `lookback` bars (high when the move is carried by trend volume, low on a counter-trend divergence), with result.confirmed, direction, and the supportive/opposing volume figures. result is null with partial_reason='no_bars' when nothing is cached (backfill via get_ohlcv first). Pass `as_of` for historical replay (trailing — no future leak). Conditions only — never buy/sell advice. Supported timeframes: 15m, 1h, 4h, 1d, 1w, 1mo.

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `lookback` | integer | no | `20` |
| `as_of` | string (date-time) \| null | no | `None` |

**Returns:** `VolumeConfirmationResponse`

| Field | Type |
| --- | --- |
| `result` | VolumeConfirmation \| null |
| `partial_reason` | string \| null |
| `scanned_at` | string (date-time) |

**Source:** [`src/market_analyser/api/mcp_tools/volume_confirmation.py`](../../src/market_analyser/api/mcp_tools/volume_confirmation.py)

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

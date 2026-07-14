/**
 * English catalog (Plan 0069, ADR-0063).
 *
 * `en` is both the default locale and the test-suite locale, so each value here
 * is authored to equal the literal the corresponding renderer spec greps for —
 * a catalog typo surfaces as a failing existing spec rather than silent drift.
 *
 * Keys are dotted and namespaced by surface. Phase 2 covers all renderer chrome
 * (extracted via the `i18n/no-unkeyed-literals` guard); phases 5–6 add the
 * sidecar reason-codes, enum labels, and fixed-error entries. `{param}` and the
 * ICU-lite `{count, plural, …}` forms are resolved by `t()` (see `lib/i18n.ts`).
 * Numbers/dates/currency stay `en-US` (ADR-0063), so `#`/`{param}` numeric
 * values format `en-US` regardless of locale.
 */
import type { Catalog } from '../lib/i18n'

export const en = {
  // ── App shell: nav + backtest panel (App.tsx) ──
  'app.nav.primaryLabel': 'Primary',
  'app.nav.chart': 'Chart',
  'app.nav.backtests': 'Backtests',
  'app.nav.signals': 'Signals',
  'app.nav.recommendations': 'Recommendations',
  'app.nav.technicalRead': 'Technical read',
  'app.nav.trackRecord': 'Track record',
  'app.nav.forecast': 'Forecast',
  'app.nav.convergence': 'Convergence',
  'app.nav.defi': 'DeFi',
  'app.nav.news': 'News',
  'app.nav.alerts': 'Alerts',
  'app.nav.settings': 'Settings',
  // Collapsed nav menu (Plan 0096 phase 5).
  'app.nav.menu': 'Menu',
  'app.nav.menuAria': 'More destinations',
  'app.nav.group.analyze': 'Analyze',
  'app.nav.group.ideas': 'Ideas',
  'app.nav.group.portfolio': 'Portfolio',
  'app.nav.group.system': 'System',
  'app.backtest.loading': 'Loading backtest result…',
  'app.backtest.loadError': 'Failed to load backtest result:',
  'app.backtest.backToRecent': 'Back to Recent backtests',
  'app.backtest.noneSelected': 'No backtest selected. Open Recent backtests to pick one.',
  'app.backtest.recentBacktests': 'Recent backtests',

  // ── Backtest result view (BacktestView.tsx) ──
  'backtest.rootLabel': 'Backtest {runId}',
  'backtest.backButton': '← Recent backtests',
  'backtest.engineVersion': 'engine v{version}',
  'backtest.metricsHeading': 'Metrics',
  'backtest.totalReturn': 'Total return',
  'backtest.sharpe': 'Sharpe',
  'backtest.maxDrawdown': 'Max drawdown',
  'backtest.maxDdDuration': 'Max DD duration',
  'backtest.winRate': 'Win rate',
  'backtest.tradeCount': 'Trade count',
  'backtest.buyAndHold': 'Buy & hold',
  'backtest.equityCurveHeading': 'Equity curve',
  'backtest.tradeLogHeading': 'Trade log ({count})',
  'backtest.noTrades': 'No trades in this run.',
  'backtest.colEntry': 'Entry',
  'backtest.colExit': 'Exit',
  'backtest.colEntryPrice': 'Entry $',
  'backtest.colExitPrice': 'Exit $',
  'backtest.colPnlUsd': 'P&L $',
  'backtest.colPnlPct': 'P&L %',
  'backtest.colStatus': 'Status',
  'backtest.statusOpen': 'Open',
  'backtest.statusClosed': 'Closed',
  'backtest.equityCurveLabel': 'Equity curve for {symbol} {timeframe}, {points} points',

  // ── Alerts view (AlertsView.tsx) ──
  'alerts.alerts': 'Alerts',
  'alerts.watches': 'Watches',
  'alerts.alertHistory': 'Alert history',
  'alerts.disclaimer': 'Alerts report conditions the agent was asked to watch — facts, not advice.',
  'alerts.loadingWatches': 'Loading watches…',
  'alerts.watchesError': 'Failed to load watches:',
  'alerts.noWatches': 'No watches yet — ask the agent to create one.',
  'alerts.watchRowLabel': 'Watch {id}: {symbol} {timeframe} {kind} — {state}',
  'alerts.enabled': 'enabled',
  'alerts.disabled': 'disabled',
  'alerts.loadingHistory': 'Loading alert history…',
  'alerts.historyError': 'Failed to load alert history:',
  'alerts.nothingFired': 'Nothing has fired yet.',
  'alerts.watchFallback': 'watch {id}',
  'alerts.noConditionText': '(no condition text)',
  'alerts.kind.indicatorThreshold': 'indicator threshold',
  'alerts.kind.pattern': 'pattern',
  'alerts.kind.strategySignal': 'strategy signal',

  // ── Toast (Toast.tsx) ──
  'toast.dismiss': 'Dismiss notification',

  // ── OHLCV chart view (OhlcvView.tsx) ──
  'ohlcv.viewLabel': 'OHLCV view for {symbol} {timeframe}',
  'ohlcv.refresh': 'Refresh',
  'ohlcv.refreshing': 'Refreshing…',
  'ohlcv.updated': 'Updated ✓',
  'ohlcv.backfillingLabel': 'Backfilling {symbol} {timeframe}',
  'ohlcv.backfilling': 'Backfilling…',
  'ohlcv.loadingChart': 'Loading chart',
  'ohlcv.loadingBars': 'Loading {symbol} {timeframe}…',
  'ohlcv.loadFailedPrefix': 'Failed to load',
  'ohlcv.retry': 'Retry',
  'ohlcv.emptyBars': 'No bars for {symbol} {timeframe} in this window.',
  'ohlcv.chartLabel': 'Candlestick chart for {symbol} {timeframe}, {count} bars',
  'ohlcv.loadingOlder': 'Loading older bars',
  'ohlcv.loadingHistory': 'Loading history…',
  'ohlcv.olderBarsError': 'Couldn’t load older bars:',
  'ohlcv.historyClampedNotice': 'showing max available history (~{days}d) for {timeframe}',
  'ohlcv.currentPriceLabel': 'Current price for {symbol}',
  'ohlcv.disconnectedLabel': 'Live price for {symbol} disconnected — showing last known value',
  'ohlcv.disconnected': 'disconnected',

  // ── Symbol picker (SymbolPicker.tsx) ──
  'symbolPicker.symbol': 'Symbol',
  'symbolPicker.timeframe': 'Timeframe',
  'symbolPicker.symbolSuggestions': 'Symbol suggestions',
  'symbolPicker.deepUsdHint': 'deep USD',

  // ── Recent backtests list (RecentBacktestsView.tsx) ──
  'recent.title': 'Recent backtests',
  'recent.lede': 'Persisted runs from `runs/`. Click a row to open the full result view.',
  'recent.loading': 'Loading runs…',
  'recent.loadError': 'failed to load backtests',
  'recent.empty':
    'No backtests yet. Ask Claude to run one — e.g. "backtest RSI on AAPL daily for the last year".',
  'recent.colStrategy': 'Strategy',
  'recent.colSymbol': 'Symbol',
  'recent.colTimeframe': 'Timeframe',
  'recent.colRange': 'Range',
  'recent.colTotalReturn': 'Total return',
  'recent.colSharpe': 'Sharpe',
  'recent.colMaxDd': 'Max DD',
  'recent.colTrades': 'Trades',
  'recent.colFinished': 'Finished',
  'recent.openBacktestLabel': 'Open backtest {runId}',

  // ── News view (NewsView.tsx) ──
  'news.title': 'News',
  'news.lede': 'Recent headlines and aggregate tone. Leave the symbol blank to browse all feeds.',
  'news.symbolLabel': 'Symbol',
  'news.symbolPlaceholder': 'e.g. BTC (blank = all feeds)',
  'news.windowLabel': 'Window',
  'news.load': 'Load',
  'news.loading': 'Loading news…',
  'news.loadError': 'failed to load news',
  'news.empty': 'No headlines in this window.',
  'news.headlinesLabel': 'Headlines',
  'news.toneScore': 'tone {score}',
  'news.toneCounts': '{pos} pos / {neg} neg / {neu} neu',

  // ── DeFi wallet-P&L view (DefiPnlView.tsx) ──
  'defi.title': 'Wallet P&L',
  'defi.lede':
    'Paste a wallet address to reconstruct its DeFi P&L — LP positions first, with 7/30/90-day and all-time realized figures.',
  'defi.addressLabel': 'Wallet address',
  'defi.addressPlaceholder': '0x…',
  'defi.refreshLabel': 'Re-pull from source (slower)',
  'defi.analyze': 'Analyze',
  'defi.invalidAddress': 'Enter a valid 0x… address (40 hex characters).',
  'defi.recentLabel': 'Recent',
  'defi.idle': 'Paste a wallet address and choose Analyze.',
  'defi.loading': 'Reconstructing wallet P&L…',
  'defi.empty': 'No positions found for this wallet.',
  'defi.partialBanner': "Partial — {excluded} of {total} positions excluded (couldn't be priced).",
  'defi.summary.realized': 'Realized P&L',
  'defi.summary.unrealized': 'Unrealized P&L',
  'defi.summary.complete': 'Complete positions',
  'defi.summary.completeValue': '{complete} of {total}',
  'defi.legend':
    'Bold figures are realized P&L — profit locked in by on-chain events. The parenthesized sub-row is estimated total return, including unrealized drift.',
  'defi.explorerLink': 'View on {explorer}',
  'defi.explorerWalletTitle': 'Open this wallet on {explorer} in your browser',
  'defi.poolLinkTitle': 'Open the pool contract on {explorer}',
  'defi.copyId': 'Copy full position ID',
  'defi.copied': 'Copied',
  'defi.tableLabel': 'LP positions',
  'defi.col.position': 'Position',
  'defi.col.unclaimed': 'Unclaimed',
  'defi.estReturnLabel': 'est. return',
  'defi.otherLabel': 'Other positions',
  'defi.otherFigures': 'realized {realized} · unrealized {unrealized}',
  'defi.incompleteGeneric': 'incomplete — could not be priced',
  'defi.error.setKeyHint':
    'No data source configured — set your Zerion API key in Settings to analyze wallets.',

  // ── Live-signal view (LiveSignalView.tsx) ──
  'signals.liveSignalEvaluation': 'Live signal evaluation',
  'signals.noEvaluation': 'No evaluation yet — ask the agent to evaluate a strategy.',
  'signals.currentPosition': 'Current position',
  'signals.lastSignal': 'Last signal',
  'signals.noneYet': 'none yet',
  'signals.freshness': 'Freshness',
  'signals.freshFired': 'fresh — fired on the last closed bar',
  'signals.noFreshSignal': 'no fresh signal on the last closed bar',
  'signals.evaluatedThrough': 'Evaluated through',
  'signals.closedBars': '{count} closed bars',
  'signals.formingNote':
    'The latest bar is still forming and was excluded — this reads through the last closed bar.',
  'signals.disclaimer': "A condition report of the strategy's current signal state — not advice.",
  'signals.at': 'at',
  'signals.barsAgo': ' ({count, plural, one {# bar} other {# bars}} ago)',
  'signals.kind.enterLong': 'enter long',
  'signals.kind.exitLong': 'exit long',
  'signals.kind.enterShort': 'enter short',
  'signals.kind.exitShort': 'exit short',

  // ── Candlestick chart controls (CandlestickChart.tsx) ──
  'chart.selectingRange': 'Selecting range… (Esc to cancel)',
  'chart.selectRange': 'Select range',
  'chart.scanning': 'Scanning…',
  'chart.candlesticks': 'Candlesticks',
  'chart.chartPatterns': 'Chart patterns',
  'chart.patternCount': '{count, plural, one {# pattern} other {# patterns}}',
  'chart.noPatternsInView': 'No patterns in view',
  'chart.noChartPatternsInView': 'No chart patterns in view',
  'chart.ariaLabel': 'Candlestick chart, {count} bars',
  // Drawing dock (DrawingRail.tsx, Plan 0097 / ADR-0091).
  'chart.draw.railLabel': 'Drawing tools',
  'chart.draw.select': 'Select / edit',
  'chart.draw.trendline': 'Trendline',
  'chart.draw.ray': 'Ray',
  'chart.draw.hline': 'Horizontal line',
  'chart.draw.vline': 'Vertical line',
  'chart.draw.rect': 'Rectangle / zone',
  'chart.draw.fib': 'Fibonacci retracement',
  'chart.draw.delete': 'Delete selected drawing',
  'chart.draw.hide': 'Hide agent drawing',
  // Market-structure badge (MarketStructureBadge.tsx, Plan 0092 / ADR-0084).
  'chart.structure.label': 'Structure',
  'chart.structure.trend.up': 'Up',
  'chart.structure.trend.down': 'Down',
  'chart.structure.trend.range': 'Range',

  // ── Forecast view (ForecastView.tsx) — chrome only; sidecar prose is phase 5 ──
  'forecast.panelLabel': 'Forecasts',
  'forecast.viewLabel': 'Direction forecast',
  'forecast.emptyState': 'No forecast yet — ask the agent for one via the `forecast` tool.',
  'forecast.conditionBannerLead': 'A forecast is a',
  'forecast.conditionBannerStrong': 'calibrated probability of direction',
  'forecast.conditionBannerTail':
    '— a condition report, not advice. Each horizon passes or fails its own out-of-sample baseline gate.',
  'forecast.asOf': 'as of',
  'forecast.asOfSuffix': '(last bar the features saw)',
  'forecast.featureSet': 'feature set',
  'forecast.featuresPriceOnly': '— price-derived features only; no exogenous series were consumed',
  'forecast.exogenousSeries': '— exogenous series:',
  'forecast.whySummary': 'Why — what the validated models lean on',
  'forecast.inputFreshness': 'Input freshness',
  'forecast.freshestPoint': '— freshest point {ts}',
  'forecast.noObservablePoint': '— no observable point',
  'forecast.artifactLead': 'full explanation persisted at',
  'forecast.artifactTail': "(relative to the sidecar's runs directory)",
  'forecast.disclaimer':
    'Skill numbers are out-of-sample directional accuracy from purged walk-forward validation; the baseline is the stronger of persistence and majority-class on the same bars (ADR-0030). A marginal edge means the beat was thin — treat its probabilities as weak evidence.',
  'forecast.driversHeadingAhead': 'ahead —',
  'forecast.topDrivers': 'top drivers',
  'forecast.blockAriaLabel': 'Forecast for {horizon} ahead',
  'forecast.ahead': 'ahead',
  'forecast.directionUp': 'Up',
  'forecast.directionDown': 'Down',
  'forecast.directionFlat': 'Flat',
  'forecast.noEdgeStrong': 'No edge over baseline.',
  'forecast.noEdgeBody':
    'The model did not beat a naive baseline out-of-sample at this horizon, so no probability is shown — an honest "don\'t know" rather than a fabricated number.',
  'forecast.outOfSample': 'out-of-sample',
  'forecast.skill': 'skill',
  'forecast.unscored': 'unscored',
  'forecast.vs': 'vs',
  'forecast.baseline': 'baseline',
  'forecast.margin': 'margin',
  'forecast.scoredBars': 'scored bars',
  'forecast.across': 'across',
  'forecast.folds': 'folds',
  'forecast.provenanceTitle': 'model {model} · libs {libs} · seed {seed}',
  'forecast.provenanceModelPrefix': 'model',
  'forecast.trainedThrough': '· trained through',
  'forecast.noModelTrained': 'no model was trained at this horizon (insufficient usable history)',
  // Volatility forecast section (Plan 0077 phase 6). A magnitude, never a
  // direction; the ML model is scored against a deterministic baseline by QLIKE.
  'forecast.volatilityLabel': 'Volatility forecast',
  'forecast.volatilityTitle': 'Volatility',
  'forecast.volatilityLede':
    'Predicted realised volatility over the horizon — a magnitude, not a direction.',
  'forecast.predictedVol': 'Predicted volatility',
  'forecast.volBand': '1σ band',
  'forecast.baselineVol': 'Baseline',
  'forecast.perBarVol': 'per-bar RMS of log returns',
  'forecast.volNoEdgeStrong': 'No edge over baseline.',
  'forecast.volNoEdgeBody':
    'The model did not beat the deterministic EWMA/persistence baseline out-of-sample, so the baseline reading below is the honest volatility estimate — an EWMA/persistence number, not a fabricated model prediction.',
  'forecast.qlike': 'QLIKE',
  'forecast.volDisclaimer':
    'Volatility is scored by QLIKE on variance (lower is better); the baseline is the stronger of RiskMetrics EWMA and naive persistence on the same bars (ADR-0070). The deterministic baseline is a useful reading even when the model adds nothing.',
  // Regime forecast section (Plan 0077 phase 6). A trailing rule-based state
  // (trend × volatility); the transition model is scored vs persistence by Brier.
  'forecast.regimeLabel': 'Regime forecast',
  'forecast.regimeTitle': 'Regime',
  'forecast.regimeLede':
    'Trailing market state (trend × volatility) and the model’s next-period transition — a condition, not a direction.',
  'forecast.currentRegime': 'Current regime',
  'forecast.regimeTransitionHeading': 'Next-period regime ({horizon})',
  'forecast.regimeCurrentTag': '(current)',
  'forecast.brier': 'Brier',
  'forecast.persistence': 'persistence',
  'forecast.regimeNoEdgeStrong': 'No edge over persistence.',
  'forecast.regimeNoEdgeBody':
    'The transition model did not beat the naive persistence baseline (regime unchanged) out-of-sample, so the honest next-period expectation is simply that the current regime holds.',
  'forecast.regimeDisclaimer':
    'Regime is a trailing, rule-based classification (trend × volatility); the transition model is scored by multiclass Brier vs a persistence baseline (ADR-0070). A condition, never a direction.',

  // ── Recommendations view (RecommendationsView.tsx) chrome ──
  'recommendations.advisoryRecommendationLabel': 'Advisory recommendation',
  'recommendations.empty':
    'No recommendation yet — ask the agent for one via the `recommend` tool.',
  'recommendations.advisoryOnly': 'Advisory only.',
  'recommendations.advisoryBannerBody':
    'This is a recommendation, not an order ticket — nothing in this app can act on it. The agent recommends; you decide.',
  'recommendations.asOf': 'as of',
  'recommendations.lastClosedBar': '(last closed bar the basis saw)',
  'recommendations.direction': 'Direction',
  'recommendations.directionLong': 'long',
  'recommendations.directionShort': 'short',
  'recommendations.directionFlat': 'flat — no actionable edge',
  'recommendations.conviction': 'Conviction',
  'recommendations.convictionDerived':
    'derived (forecast probability × backtested edge), never invented',
  'recommendations.advisoryLevelsLabel': 'Advisory levels',
  'recommendations.advisoryLevelsTitle': 'Advisory levels — for your judgement, not a ticket',
  'recommendations.entryZone': 'Entry zone',
  'recommendations.advisoryTag': '(advisory)',
  'recommendations.stop': 'Stop',
  'recommendations.targetHeading': '{count, plural, one {Target} other {Targets}}',
  'recommendations.rationaleLabel': 'Rationale',
  'recommendations.why': 'Why',
  'recommendations.basisLabel': 'Basis',
  'recommendations.whatBackedThisCall': 'What backed this call',
  'recommendations.conditions': 'Conditions',
  'recommendations.liveSignals': 'Live signals',
  'recommendations.backtestedEdge': 'Backtested edge',
  'recommendations.disclaimer':
    'Labeled advisory (ADR-0029): the basis above travels with every call, and a flat verdict is an honest "no actionable edge", never a fabricated call.',
  'recommendations.fusionChecksLabel': 'Fusion checks',
  'recommendations.everyGateChecked': 'Every gate checked',
  'recommendations.leg': 'leg',
  'recommendations.check': 'check',
  'recommendations.threshold': 'threshold',
  'recommendations.actual': 'actual',
  'recommendations.result': 'result',
  'recommendations.pass': 'pass',
  'recommendations.fail': 'FAIL',
  'recommendations.checksNote':
    "The trace records the fusion's decision; a directional call means every gating gate passed.",
  'recommendations.nonGatingTag': 'non-gating',
  'recommendations.none': 'none',
  'recommendations.notPartOfBasis': 'not part of this basis',
  'recommendations.forecast': 'Forecast',
  'recommendations.forecastRanOnTier': 'Forecast ran on the {name} feature set.',
  'recommendations.forecastRanOnFeatureSet': 'Forecast ran on feature set {x}.',
  // Direction-leg demotion + non-voting vol/regime inputs (Plan 0077 phase 5/6,
  // ADR-0071). The demotion and the non-voting nature are stated out loud.
  'recommendations.directionLegLabel': 'Direction forecast leg',
  'recommendations.directionLegGating': 'voting',
  'recommendations.directionLegNonGating': 'present but non-gating',
  'recommendations.directionLegGatingNote':
    'The direction forecast cleared the skill-margin threshold, so it voted on this call.',
  'recommendations.directionLegNonGatingNote':
    'The direction forecast had no reliable edge (out-of-sample skill margin below threshold), so it did not vote and could not veto this call — it is advisory only (ADR-0071). The call rests on the conditions, the live signal, and the backtested edge.',
  'recommendations.directionLegMargin': 'out-of-sample skill margin {margin}',
  'recommendations.directionLegNoMargin': 'no scored skill margin',
  'recommendations.nonVotingLabel': 'Non-voting inputs',
  'recommendations.nonVotingTitle': 'Sizing & context (non-voting)',
  'recommendations.nonVotingNote':
    'Volatility and regime shape size, stop distance, and conviction — they never vote on or flip the direction (ADR-0071).',
  'recommendations.sizingTitle': 'Volatility sizing',
  'recommendations.sizeFactor': 'Size factor',
  'recommendations.sizeFactorNote': 'inverse-vol · 1.00 = reference · advisory, not an order size',
  'recommendations.volUsed': 'Volatility used',
  'recommendations.volSource': 'Source',
  'recommendations.stopVolDistance': 'Vol-implied stop distance',
  'recommendations.sizingNeutral': 'No usable volatility reading — neutral sizing.',
  'recommendations.regimeContextTitle': 'Regime context',
  'recommendations.currentRegime': 'Current regime',
  'recommendations.regimeTrusted': 'transition model trusted (beats persistence out-of-sample)',
  'recommendations.regimeUntrusted':
    'transition model not trusted — persistence is the default, conviction unchanged',
  'recommendations.convictionFactor': 'Conviction factor',
  'recommendations.convictionFactorNote': 'regime-stability multiplier · 1.00 = neutral',
  'recommendations.regimeUndefined': 'undefined',

  // ── Technical Read view (TechnicalReadView.tsx) — Plan 0074, ADR-0068 ──
  'technicalRead.viewLabel': 'Technical read',
  'technicalRead.empty':
    'No technical read yet — ask the agent for one via the `technical_read` tool.',
  'technicalRead.notCorroboratedTitle': 'Single indicator — not corroborated.',
  'technicalRead.notCorroboratedBody':
    'This is one indicator’s mechanical read, not the fused `recommend` call. There is no forecast, no backtested edge, and no conviction behind it — it may disagree with a corroborated recommendation. You read it and size it yourself.',
  'technicalRead.asOf': 'as of',
  'technicalRead.lastClosedBar': '(last closed bar the read saw)',
  'technicalRead.indicator': 'Indicator',
  'technicalRead.direction': 'Direction',
  'technicalRead.directionLong': 'long',
  'technicalRead.directionShort': 'short',
  'technicalRead.directionFlat': 'flat — no clear direction',
  'technicalRead.regimeState': 'Regime read',
  'technicalRead.why': 'Mechanical rule',
  'technicalRead.indicatorSupertrend': 'Supertrend',
  'technicalRead.indicatorEmaStack': 'EMA stack',
  'technicalRead.indicatorMacd': 'MACD',
  'technicalRead.indicatorIchimoku': 'Ichimoku',
  'technicalRead.disclaimer':
    'The lesser advisory tier (ADR-0068): one named indicator, read by its textbook rule, with no conviction and no entry/stop/target levels by design. For a corroborated call with levels, use the fused `recommend` tool.',

  // ── Convergence view (ConvergenceView.tsx) — Plan 0078, ADR-0041/0029 ──
  'convergence.viewLabel': 'Convergence opportunities',
  'convergence.empty':
    'No convergence screen yet — ask the agent to run the `find_convergence_opportunities` tool.',
  'convergence.disclaimerTitle': 'Facts, not a call.',
  'convergence.disclaimerBody':
    'These are markets near resolution with their risks attached — a near-certain outcome’s implied upside is shown gross of the resolution tail, never as expected value. Read the resolution risk, liquidity, and lockup before you decide anything yourself.',
  'convergence.forQuery': 'For query',
  'convergence.opportunities': 'opportunities',
  'convergence.asOf': 'as of',
  'convergence.outcome': 'Near-certain outcome',
  'convergence.returnIfRight': 'Return if right',
  'convergence.returnGrossNote': 'gross of the resolution tail — not expected value',
  'convergence.timeToResolution': 'Time to resolution',
  'convergence.closesAt': 'closes',
  'convergence.volume': 'Volume',
  'convergence.resolutionRisk': 'Resolution risk',
  'convergence.riskHeuristicNote': 'a labeled heuristic, not a guarantee',
  'convergence.riskLevelLow': 'low',
  'convergence.riskLevelMedium': 'medium',
  'convergence.riskLevelHigh': 'high',
  'convergence.viewOnPolymarket': 'View on Polymarket ↗',

  // ── Track-record view (TrackRecordView.tsx) — Plan 0080, ADR-0075 ──
  'trackRecord.title': 'Track record',
  'trackRecord.lede':
    "How the advisor's own past recommendations turned out against realized price, scored against the ticket each one gave.",
  'trackRecord.disclaimer':
    'A factual record of past accuracy, not advice — and never a reason to act.',
  'trackRecord.loading': 'Loading track record…',
  'trackRecord.loadError': 'Failed to load the track record.',
  'trackRecord.empty': 'No scored recommendations yet.',
  'trackRecord.sampleSize': '{n, plural, one {# scored call} other {# scored calls}}',
  'trackRecord.insufficient':
    'Not enough calls to conclude — {n} of {min} needed. Percentages are withheld until the sample is large enough to mean something.',
  'trackRecord.baselineDeltaTitle': 'Edge over baseline',
  'trackRecord.baselineDeltaLabel':
    'hit-rate vs a buy-and-hold-over-horizon baseline (the number that matters)',
  'trackRecord.hitRate': 'Hit rate',
  'trackRecord.baselineHitRate': 'Baseline hit rate',
  'trackRecord.meanR': 'Mean R',
  'trackRecord.calibrationTitle': 'Calibration',
  'trackRecord.brier': 'Brier score',
  'trackRecord.meanPredicted': 'Stated probability',
  'trackRecord.observedFreq': 'Realized frequency',
  'trackRecord.reliabilityBand': 'Stated band',
  'trackRecord.colCount': 'n',
  'trackRecord.recentTitle': 'Recent scored calls',
  'trackRecord.recentEmpty': 'No scored calls to show.',
  'trackRecord.colSymbol': 'Symbol',
  'trackRecord.colDirection': 'Direction',
  'trackRecord.colOutcome': 'Outcome',
  'trackRecord.colRealizedR': 'Realized R',
  'trackRecord.colAsOf': 'As of',
  'trackRecord.directionLong': 'Long',
  'trackRecord.directionShort': 'Short',
  'trackRecord.outcomeTargetHit': 'Target hit',
  'trackRecord.outcomeStopped': 'Stopped',
  'trackRecord.outcomeTimeout': 'Timeout',

  // ── Sidecar reason-codes (advisor fusion.py / forecast explain.py) ──
  // Templates for the structured `{code, params}` reason-codes the renderer
  // localizes (Plan 0069 phase 4/4b/5, ADR-0063). Enum-valued params ride as
  // raw tokens and are mapped through the `enum.*` catalog by `localizeReasonCode`
  // before interpolation; numeric params format `en-US`. The English prose these
  // mirror stays authoritative for the agent/MCP consumer (untouched sidecar-side).
  //
  // Directional rationale.
  'reason.forecast':
    'forecast: P({direction})={prob} over {horizon_bars} bar(s), {edge_strength}{_skill, plural, =1 { (out-of-sample skill {skill} vs baseline {baseline})} other {}}',
  'reason.signals_agree': 'live signals agree ({direction}): {strategies}',
  'reason.backtested_edge':
    'backtested edge: walk-forward sharpe_mean {sharpe_mean} over {n_splits} folds ({strategy_id})',
  'reason.conditions': 'conditions: trend={trend}, momentum={momentum}, volume={volume}',
  // Flat verdict.
  'reason.no_actionable_edge': 'no actionable edge',
  'blocker.forecast_no_edge': 'forecast shows no edge over baseline (no probability shipped)',
  'blocker.forecast_flat': 'forecast direction is flat or undecided',
  'blocker.signals_conflict': 'live signals conflict: long={long}, short={short}',
  'blocker.no_directional_signal': 'no live strategy signal implies a direction',
  'blocker.signals_disagree_forecast':
    'live signals ({signal_dir}) disagree with the forecast direction ({forecast_dir})',
  'blocker.no_walk_forward': 'no walk-forward backtest basis supplied',
  'blocker.no_backtested_edge':
    'no backtested edge{_sharpe, plural, =1 {: walk-forward sharpe_mean {sharpe_mean}} other {}}',
  'blocker.edge_nonvoting_strategy':
    'walk-forward edge is for {strategy_id}, which is not among the agreeing signals',
  // Fusion gate-check labels (1:1 with basis.checks; the dynamic threshold/actual
  // values ride in the FusionCheck itself, rendered beside these labels).
  'gate.alignment_scope': 'inputs share symbol/timeframe',
  'gate.alignment_asof': 'inputs share the as-of bar',
  'gate.conditions_read': 'condition snapshot read',
  'gate.forecast_probs_shipped': 'probabilities shipped (baseline beaten out-of-sample)',
  'gate.forecast_argmax_directional': 'argmax direction is directional',
  'gate.forecast_calibrated_prob': 'calibrated P(direction)',
  'gate.signal_live_vote': 'live vote: {strategy_id}',
  'gate.signal_no_conflict': 'no conflicting live votes',
  'gate.signal_directional_vote': 'at least one directional live vote',
  'gate.signal_agrees_forecast': 'live direction agrees with the forecast direction',
  'gate.backtest_basis_supplied': 'walk-forward basis supplied',
  'gate.backtest_edge_positive': 'backtested edge positive (sharpe_mean > 0)',
  'gate.backtest_strategy_agrees': 'walk-forward strategy among the agreeing votes',
  // Non-voting inputs + direction-leg demotion (Plan 0077 phase 5, ADR-0071).
  'gate.volatility_nonvoting': 'volatility forecast (non-voting: sizing + stop)',
  'gate.regime_nonvoting': 'regime forecast (non-voting: conviction)',
  'reason.direction_leg_nongating':
    'direction forecast leg present but non-gating (out-of-sample skill margin below {threshold}) — advisory only, the call rests on the live signal and backtested edge',
  'reason.sizing': 'volatility (non-voting): {vol_source} reading → size factor {size_factor}',
  'reason.regime_context':
    'regime (non-voting): {current_regime}, {trusted, plural, =1 {trusted} other {not trusted}} → conviction factor {conviction_factor}',
  // Condition / signal facts (basis.condition_codes / basis.signal_codes, phase 4b).
  'condition.trend': 'trend: {value}',
  'condition.momentum': 'momentum: {value}',
  'condition.volume': 'volume: {value}',
  'condition.candlestick': 'candlestick: {pattern} ({direction})',
  'signal.vote': '{strategy_id}: position={position}{fresh, plural, =1 {, fresh signal} other {}}',
  // Forecast explanation constants (forecast explain.py).
  'disclaimer.importance':
    'Driver importance is out-of-sample permutation importance — association within the validated model, not causation; correlated inputs share credit.',
  'note.no_scored_folds':
    'no scored out-of-sample folds at this horizon — no importances were measured',

  // ── Enum labels (closed condition/signal vocabularies + passthrough enums) ──
  // Every enum value the sidecar ships as a raw token is a *closed* set, so the
  // renderer translates it through this catalog (Plan 0069 phase 5, ADR-0063).
  // Trend (analysis Trend).
  'enum.trend.up': 'up',
  'enum.trend.down': 'down',
  'enum.trend.sideways': 'sideways',
  // Momentum (analysis MomentumStance).
  'enum.momentum.overbought': 'overbought',
  'enum.momentum.bullish': 'bullish',
  'enum.momentum.neutral': 'neutral',
  'enum.momentum.bearish': 'bearish',
  'enum.momentum.oversold': 'oversold',
  // Volume (analysis VolumeStance).
  'enum.volume.heavy': 'heavy',
  'enum.volume.normal': 'normal',
  'enum.volume.light': 'light',
  // Pattern direction (analysis Direction).
  'enum.direction.bullish': 'bullish',
  'enum.direction.bearish': 'bearish',
  'enum.direction.neutral': 'neutral',
  // Live-signal / recommendation direction (current_position; long/short/flat).
  'enum.position.long': 'long',
  'enum.position.short': 'short',
  'enum.position.flat': 'flat',
  // Candlestick pattern names (analysis patterns._DETECTORS).
  'enum.pattern.doji': 'doji',
  'enum.pattern.hammer': 'hammer',
  'enum.pattern.hanging_man': 'hanging man',
  'enum.pattern.marubozu': 'marubozu',
  'enum.pattern.bullish_engulfing': 'bullish engulfing',
  'enum.pattern.bearish_engulfing': 'bearish engulfing',
  'enum.pattern.dark_cloud_cover': 'dark cloud cover',
  'enum.pattern.piercing_line': 'piercing line',
  'enum.pattern.bullish_harami': 'bullish harami',
  'enum.pattern.bearish_harami': 'bearish harami',
  'enum.pattern.morning_star': 'morning star',
  'enum.pattern.evening_star': 'evening star',
  'enum.pattern.three_white_soldiers': 'three white soldiers',
  'enum.pattern.three_black_crows': 'three black crows',
  // Forecast edge strength (EdgeStrength; also the ForecastView edge badge).
  'enum.edge_strength.no_edge': 'no edge over baseline',
  'enum.edge_strength.marginal': 'marginal edge',
  'enum.edge_strength.clear': 'clear edge',
  // Regime taxonomy (RegimeState — trend × volatility, Plan 0077 phase 2).
  'enum.regime.down_quiet': 'downtrend, quiet',
  'enum.regime.down_volatile': 'downtrend, volatile',
  'enum.regime.sideways_quiet': 'sideways, quiet',
  'enum.regime.sideways_volatile': 'sideways, volatile',
  'enum.regime.up_quiet': 'uptrend, quiet',
  'enum.regime.up_volatile': 'uptrend, volatile',
  // Volatility baseline kind (BaselineKind) + advisory sizing source (Plan 0077).
  'enum.vol_baseline.ewma': 'EWMA',
  'enum.vol_baseline.persistence': 'persistence',
  'enum.vol_source.model': 'model',
  'enum.vol_source.baseline': 'baseline',
  'enum.vol_source.none': 'no usable',
  // Passthrough enums authored as labels on our side (upstream-sourced values).
  'enum.crypto_regime.btc_led': 'BTC-led',
  'enum.crypto_regime.alt_structure': 'alt structure',
  'enum.crypto_regime.risk_off_structure': 'risk-off structure',
  'enum.crypto_regime.neutral': 'neutral',
  'enum.fear_greed.extreme_fear': 'Extreme Fear',
  'enum.fear_greed.fear': 'Fear',
  'enum.fear_greed.neutral': 'Neutral',
  'enum.fear_greed.greed': 'Greed',
  'enum.fear_greed.extreme_greed': 'Extreme Greed',

  // ── Fixed sidecar error details (client.ts localizeErrorDetail) ──
  // Only the FIXED HTTP `detail=` constants are mapped; dynamic `str(exc)`
  // passthrough renders its upstream English text unchanged (ADR-0063 seam).
  'error.detail.noWalletSource': 'no wallet-positions source configured',
  'error.detail.noHistoricalPriceSource': 'no historical price source configured',
  'error.detail.noSecretsStore': 'secrets store not configured',
  'error.detail.noMcpSecretPath': 'mcp secret path not configured',
  'error.detail.noAlertingPersistence': 'alerting persistence not configured',

  // ── Settings view (SettingsView.tsx) ──
  'settings.appearance.heading': 'Appearance',
  'settings.appearance.lede.pre': 'Choose how the app looks. ',
  'settings.appearance.lede.system': 'System',
  'settings.appearance.lede.post':
    " follows your operating system's light/dark setting; Light and Dark pin it regardless of the OS.",
  'settings.appearance.theme.label': 'Theme',
  'settings.appearance.theme.light': 'Light',
  'settings.appearance.theme.dark': 'Dark',
  'settings.appearance.theme.system': 'System',
  'settings.appearance.language.label': 'Language',
  'settings.chartStyle.heading': 'Chart style',
  'settings.chartStyle.lede.pre':
    'Recolour and resize the candlestick chart’s lines and markers. Colours and widths are saved ',
  'settings.chartStyle.lede.perTheme': 'per theme',
  'settings.chartStyle.lede.post': "; you're editing the theme the chart is currently showing.",
  'settings.mcp.heading': 'MCP access',
  'settings.mcp.lede':
    'Claude Desktop and other MCP clients connect to the sidecar at the URL below using the bearer token. The token is long-lived and survives app restarts.',
  'settings.mcp.endpointUrl.label': 'Endpoint URL',
  'settings.mcp.bearerToken.label': 'Bearer token',
  'settings.mcp.loading': 'Loading…',
  'settings.mcp.hide': 'Hide',
  'settings.mcp.reveal': 'Reveal',
  'settings.mcp.copied': 'Copied!',
  'settings.mcp.copy': 'Copy',
  'settings.mcp.rotating': 'Rotating…',
  'settings.mcp.rotate': 'Rotate',
  'settings.mcp.rotateWarning':
    'Rotating generates a new token and invalidates the existing one immediately. Any active MCP clients will need to be reconfigured with the new token.',
  'settings.mcp.lifecycle.label': 'Sidecar lifecycle',
  'settings.mcp.lifecycle.lede':
    'The sidecar runs as a standalone process — closing this window does not stop it. MCP clients can keep talking to it. Click below to stop it explicitly.',
  'settings.mcp.stopping': 'Stopping…',
  'settings.mcp.stopRequested': 'Stop requested',
  'settings.mcp.stopSidecar': 'Stop sidecar',
  'settings.mcp.shutdownRequested':
    'Sidecar shutdown requested. The viewer will lose its sidecar connection.',
  'settings.mcp.snippet.label': 'Claude Desktop snippet',
  'settings.mcp.snippet.pre': 'Paste this into ',
  'settings.mcp.configFilename': 'claude_desktop_config.json',
  'settings.mcp.snippet.post': '. Reveal the token first so the snippet contains the real value.',

  // ── Theme toggle (ThemeToggle.tsx) ──
  'themeToggle.system': 'System',
  'themeToggle.light': 'Light',
  'themeToggle.dark': 'Dark',
  'themeToggle.ariaLabel': 'Theme: {current}. Activate to switch to {next}.',
  'themeToggle.title': 'Theme: {current}',

  // ── Chart-style controls (ChartStyleControls.tsx) ──
  'chartStyle.candleTypeLabel': 'Candle type',
  'chartStyle.lineAreaNotePre': 'Line and Area draw a single colour (the ',
  'chartStyle.candleUp': 'Candle up',
  'chartStyle.lineAreaNotePost': ' colour). Switch to Candles or OHLC bars to change it.',
  'chartStyle.editingPre': 'Editing ',
  'chartStyle.editingPost': ' theme — switch theme in Appearance to edit the other set.',
  'chartStyle.widthLabel': 'Width',
  'chartStyle.colorLabel': 'Color',
  'chartStyle.resetButton': 'Reset chart style',

  // ── Chart legend (ChartLegend.tsx) — Plan 0096 phase 2/3 ──
  'chartLegend.ariaLabel': 'Chart layers legend',
  'chartLegend.settingsAria': 'Style {layerName}',
  'chartLegend.presetLabel': 'Preset',
  'chartLegend.presetCustom': 'Custom',
  'chartLegend.savePreset': 'Save as…',
  'chartLegend.saveConfirm': 'Save',
  'chartLegend.presetNamePlaceholder': 'Preset name',
  'chartLegend.collapseAria': 'Collapse indicator panel',
  'chartLegend.expandAria': 'Show indicator panel',
  'chartLegend.hideAll': 'Hide all indicators',
  'chartLegend.showAll': 'Show indicators',
  'chartLegend.preset.clean': 'Clean',
  'chartLegend.preset.trend': 'Trend',
  'chartLegend.preset.meanReversion': 'Mean-reversion',
  'chartLegend.preset.patterns': 'Patterns',

  // ── Chart side dock (ChartSidePanel.tsx) — Plan 0096 phase 4 ──
  'sidePanel.expandAria': 'Show symbol details',
  'sidePanel.collapseAria': 'Hide symbol details',
  'sidePanel.title': 'Details',
  'sidePanel.last': 'Last',
  'sidePanel.change': 'Change',
  'sidePanel.open': 'Open',
  'sidePanel.high': 'High',
  'sidePanel.low': 'Low',
  'sidePanel.close': 'Close',
  'sidePanel.volume': 'Volume',
  'sidePanel.asOf': 'As of',
  'sidePanel.noData': 'No data',

  // ── Layers panel (LayersPanel.tsx) ──
  'layers.panelAriaLabel': 'Chart layers',
  'layers.heading': 'Layers',
  'layers.toggleAria': 'Toggle {layerName}',
  'layers.resizeAria': 'Resize the layers panel',
  'layers.addIndicator': '+ Indicator',
  'layers.kindLabel': 'Indicator',
  'layers.periodLabel': 'Period',
  'layers.stdDevLabel': 'Std dev (k)',
  'layers.addButton': 'Add',
  'layers.removeAria': 'Remove {layerName}',
  'layers.invalidPeriod': 'Period must be a whole number greater than 0.',
  'layers.invalidStdDev': 'Std dev must be greater than 0.',
  'layers.kind.ema': 'EMA',
  'layers.kind.sma': 'SMA',
  'layers.kind.bbands': 'Bollinger Bands',
  'layers.kind.supertrend': 'Supertrend',
  'layers.kind.ichimoku': 'Ichimoku',
  'layers.kind.stochastic': 'Stochastic',
  'layers.kind.stoch_rsi': 'Stochastic RSI',
  'layers.kind.cci': 'CCI',
  'layers.kind.williams_r': 'Williams %R',
  'layers.kind.roc': 'Rate of Change',
  'layers.kind.mfi': 'Money Flow Index',
  'layers.kind.cmf': 'Chaikin Money Flow',
  'layers.kind.ad_line': 'A/D Line',
  'layers.kind.rsi': 'RSI',
  'layers.kind.macd': 'MACD',
  'layers.kind.fibonacci': 'Fibonacci',
  'layers.kind.pivot_points': 'Pivot points',
  'layers.kind.anchored_vwap': 'Anchored VWAP',

  // ── Glossary tooltip chrome (GlossaryTerm.tsx) — prose is phase 3 ──
  'glossary.howComputedLabel': "How it's computed",
  'glossary.whatItMeansLabel': 'What it means',
} satisfies Catalog

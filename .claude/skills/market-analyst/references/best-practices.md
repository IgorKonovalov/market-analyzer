# Best practices — market-analyst

Longer-form notes on what separates honest TradFi analysis from numerology. SKILL.md has the rules; this file has the *why* and the patterns. Read on demand.

## The analyst-vs-trader vocabulary boundary

This is the single most important rule in the skill, repeated because it's the easiest to slip on.

Conditions are facts. Decisions are the user's.

| ✅ Analyst voice                                                | ❌ Trader voice                                       |
|----------------------------------------------------------------|------------------------------------------------------|
| "RSI is 78, 88th percentile over 90 days."                     | "RSI is overbought, time to sell."                   |
| "Daily candle is a bearish engulfing on 2.3× avg volume."      | "This is a great short setup."                       |
| "Price is 3.1% above the 20-EMA and 1.8 ATRs above the 50-EMA."| "Stock is overextended, expect a pullback."          |
| "Bollinger Bands compressed to 0.8% width, 14th percentile."   | "Breakout incoming."                                 |
| "Hammer on yesterday's bar at prior support; today's close above the hammer high." | "Buy here, stop below the hammer low." |

The pattern: analyst voice **names the observable** and **anchors it in a base rate**. Trader voice makes a **forward-looking claim** and **prescribes an action**. You stay in the left column.

If you find yourself reaching for "should", "buy", "sell", "entry", "exit", "stop", "target", or "take profit" — you've slipped.

The user is welcome to take the conditions you report and translate them into trades. That translation is theirs, not yours.

## Pattern context: the three things every pattern needs

A candlestick pattern in isolation is weak signal. Bulkowski's studies show single-pattern base rates rarely exceed 60% directional accuracy, and that's *with* confirmation. Without context, you're reporting horoscope-grade information.

Every pattern your scan emits must carry:

### 1. Trend context

A reversal pattern (hammer, engulfing, star) matters most **against the prevailing trend**. A continuation pattern (three-bar continuations, marubozu in trend direction) matters most **with the trend**.

Determine trend from an EMA stack on the same timeframe:

- **Uptrend**: close > 20-EMA > 50-EMA > 200-EMA, all positively sloped over the recent 10-bar window.
- **Downtrend**: close < 20-EMA < 50-EMA < 200-EMA, all negatively sloped.
- **Chop / range**: anything else. EMAs interleaved, close oscillating around them.

A "bullish engulfing" in chop is not the same signal as a bullish engulfing after a 6-bar downtrend touching the 50-EMA from above. The first is noise; the second is the textbook case. Your report says which is which.

### 2. Volume context

A pattern on above-average volume carries more weight than the same pattern on thin volume. Crude rule, but it survives backtests.

Compute the 20-bar simple average of volume. Bucket the firing bar:

- **High**: > 1.5× avg
- **Above-average**: 1.0–1.5× avg
- **Below-average**: 0.5–1.0× avg
- **Thin**: < 0.5× avg

Report this bucket in every fired pattern. A "hammer on high volume at prior support" is a different report than "hammer on thin volume in open air".

A subtle point: for indices (SPY, QQQ) volume is composite and matters less; for individual stocks it matters more. Earnings days, ex-div days, and rebalance days distort. If you spot a volume spike that's clearly mechanical (e.g. quarterly rebalance), say so.

### 3. Level context

Where did the pattern fire? Three buckets:

- **At support / resistance** — within 0.5 ATRs of a prior swing high or swing low from the last 90 bars.
- **In a confluence zone** — at a swing level *and* near a key EMA, or at a round number near a swing level.
- **In open air** — nowhere close to a level.

Patterns at levels carry more weight than patterns in open air. A bearish engulfing at a tested resistance is the high-conviction case; a bearish engulfing in the middle of a trending run is much weaker.

When you find prior swing levels, use a simple rule:

- **Swing high**: a bar whose high is greater than the two bars before *and* the two bars after.
- **Swing low**: mirror.

(For analysis purposes, you can use the future-bars-included version — you're looking back at history. For real-time decisions in a strategy, the strategy needs to be lookahead-safe. Different contract, different rule.)

## Confirmation: the next-bar rule

A pattern "fires" on bar `i`. It's **confirmed** when bar `i+1` does something consistent with the pattern's hypothesis:

- **Bullish reversal patterns** (hammer, bullish engulfing, morning star, piercing line) — confirmed by `bars[i+1].close > bars[i].high`, or at minimum `bars[i+1].close > bars[i].close`.
- **Bearish reversal patterns** (hanging man, bearish engulfing, evening star, dark cloud cover) — mirror.
- **Continuation patterns** (three white soldiers, three black crows, marubozu) — confirmed by the trend continuing for another 1-2 bars in the pattern's direction.
- **Indecision patterns** (doji, spinning top) — "confirmed" only in the sense that the next bar's direction tells you which way the indecision broke.

In your `scan.md` output, distinguish:

- **Fired** — the pattern exists on bar `i`.
- **Confirmed** — fired *and* bar `i+1` exists and meets the confirmation criterion.
- **Pending** — fired on the most recent bar; no `i+1` to confirm yet.
- **Failed** — fired but `i+1` went the wrong way.

Pending patterns are the most useful for "what's setting up right now"; confirmed patterns are the most useful for "what worked recently"; failed patterns are useful too, especially clustered together — repeated failed bullish reversals in a downtrend confirm the downtrend.

## Timeframe rules

Candlestick patterns assume **daily** bars in most of the classical literature. They generalize to weekly and 4-hour reasonably well. They generalize poorly to anything intraday smaller than 1-hour, because shorter timeframes are dominated by execution-driven noise (auctions, MOC orders, low-liquidity gaps) and the "bullish engulfing" shape has no underlying behavioral basis on a 5m chart.

Rules of thumb the skill follows:

- **1d, 1w**: full pattern catalog applies. Report normally.
- **4h**: patterns apply but confidence drops. Bias toward "moderate" strength even when the shape is textbook.
- **1h**: only the most robust patterns (engulfing, hammer at swing low, evening star) carry signal. Indecision patterns (doji, spinning top) are essentially noise.
- **15m, 5m, 1m**: noise. If the user runs a scan here, surface this explicitly: "Note: pattern base rates are weak on intraday timeframes < 1h. Treating fires as inspection candidates, not setups."

This isn't gatekeeping. It's calibrating confidence to base rates.

## Indicator interpretation: extremes vs trends

Two failure modes when reading indicators:

### Failure mode 1: "RSI 70 = overbought"

The classical 70/30 thresholds were calibrated for ranging markets in the 1970s on a small set of commodities. They're useful but not universal.

- In a **strong uptrend**, RSI spends a lot of time > 70 and can persist there for weeks. "RSI overbought, sell" gets you run over.
- In a **range**, RSI oscillating between 30 and 70 is the *whole point*; the extremes are tradable.
- In **transition** (trend just starting or just ending), RSI behavior is most informative — a long-trend market that fails to make a new high while RSI makes a lower high is a classic bearish divergence.

The skill reports:

- The current **value**.
- The **90-day percentile** of that value (more honest than "is it 70?").
- **Divergences** with price (price makes a new high; RSI doesn't) — these matter more than absolute levels.

Same logic applies to MACD, Stochastic, Williams %R.

### Failure mode 2: "MA cross = signal"

"Golden cross" (50-EMA over 200-EMA) and "death cross" (50 below 200) are lagging by design. By the time the cross prints, the move is well underway. They're useful as **trend confirmation**, not as **trigger signals**.

The skill reports:

- The current **EMA stack** (which EMA is above which).
- The **distance** of price from each EMA in % and in ATRs.
- The **slope** of each EMA over the last 10 bars (positive, flat, negative).

"20 > 50 > 200, all positively sloped, close 0.4 ATR above 20-EMA" is more useful than "golden cross fired 30 bars ago".

## Volatility regime

ATR (Average True Range) is the simplest measure. Two derived facts that matter:

- **Compressed volatility** — ATR(14) in the bottom 25% of its 90-day distribution. This is the "coiled spring" state — breakouts happen from compression, in either direction. You don't know the direction; you know the timing is interesting.
- **Expanded volatility** — ATR(14) in the top 25%. Trend continuation in this state is unlikely (mean reversion of vol); trend exhaustion is common.

Report ATR(14) and its 90-day percentile in every snapshot.

Bollinger Band width is the same idea, different scaling — it's volatility normalized to price. Both are fine; pick one and stick with it across the skill's outputs.

## Support and resistance

Three sources, in order of reliability:

1. **Prior swing highs and swing lows** (the swing-level definition from earlier). These are the literal levels traders mark on charts; they're the most likely to "matter" because they're the most visible.
2. **Round numbers** ($100, $500, etc., or for indices, integer-thousand levels). Behavioral, not technical, but real.
3. **Moving averages** that the market has tested before. The 200-day EMA on indices is famous for a reason — it's defended often enough that defense becomes self-fulfilling.

For each S/R level in your snapshot, report:

- **Price** of the level.
- **Distance** from current close (in % and ATRs).
- **How many bars ago** it last printed.
- **How many times tested** since (rough — count touches within 0.5 ATR).

A level that printed once 80 bars ago and was never tested is weaker than a level tested four times in the last 30 bars.

## When patterns and indicators disagree

Common case. Examples:

- Bullish engulfing on a daily bar, but RSI > 80 and price 3 ATRs above the 20-EMA.
- Bearish engulfing at the 200-day EMA, but trend is strongly down (50 < 200 EMA, slope negative).

Don't pick a side. Report both observations. The user calibrates.

The honest framing: "Bullish reversal pattern fired, but momentum is extended (RSI 88th pct, price 3.1 ATRs above 20-EMA). Two ways this resolves: (a) pattern is meaningless mid-trend, market continues up; (b) momentum mean-reverts and the pattern marks a top. The pattern alone doesn't tell you which."

That's analysis. "Confused signal, skip it" is not — you don't know what the user is looking for.

## What to never do in this skill

- **Never predict price levels.** "Target $200" is invention. "Resistance at $200 from prior swing high" is observation.
- **Never count Elliott waves.** Wave counting is subjective and the post-hoc adjustments make it untestable. If a user asks for a wave count, decline politely and offer a swing-level analysis instead.
- **Never mention astrology, Gann angles, harmonic patterns, or other unfalsifiable systems.** Stick to indicators with public studies behind them.
- **Never extrapolate from one pattern to "the market will".** One bullish hammer doesn't say what the market will do.
- **Never grade the user's positions.** If they mention their positions, restate conditions on those symbols. Don't say "you should have stopped out by now."

## What "good output" looks like

A scan.md is good when:

- Headline is a single sentence with the count and the directional split.
- Each fired pattern has trend context, volume context, level context, confirmation status.
- "Strength" is honest — not all `strong` if most are noise.
- Conflicting signals are surfaced, not hidden.
- The path forward is reproducible — same cache + same params → same JSON output.

A snapshot.md is good when:

- Headline is a single dense sentence (trend regime, momentum status, vol regime).
- Each section has the value, the percentile, and the interpretation in analyst voice.
- S/R levels are listed with distance + recency.
- Conflicting signals are surfaced explicitly.
- No trade calls anywhere.

A brainstorm response is good when:

- 2-3 concrete setups, not 10 vague ideas.
- Each setup names what to look for, the hypothesis, what could break it, the scan to run.
- Ends with "Want me to scan?", not "You should look at...".

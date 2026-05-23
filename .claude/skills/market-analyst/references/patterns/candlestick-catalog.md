# Candlestick pattern catalog

The complete list of patterns the `market-analyst` skill recognizes. Definitions are per Nison (1991) and Bulkowski's *Encyclopedia of Candlestick Charts* (2008), with the project's confirmation and context rules layered on top.

Each entry has:

- **Definition** — the geometric rule (in terms of `O`, `H`, `L`, `C` of one or more consecutive bars).
- **Lean** — `bullish` / `bearish` / `neutral` (indecision).
- **Family** — `reversal` (most useful against the trend) or `continuation` (most useful with the trend) or `indecision`.
- **Strength rules** — what makes this pattern fire `weak` / `moderate` / `strong`.
- **Confirmation** — what bar `i+1` must do to confirm.
- **Common false positives** — geometric shapes that look like the pattern but aren't.

The `strength` field in scan output combines the geometric match quality, the trend context, the volume context, and the level context. A "textbook hammer at major support with 2× volume confirmation" is `strong`; the same geometric hammer in open air with thin volume is `weak` regardless of how clean the body/wick ratio is.

## Single-bar patterns

### Doji

- **Definition**: `|C - O| / (H - L) < 0.1` — body is < 10% of the bar's range.
- **Lean**: neutral.
- **Family**: indecision.
- **Strength rules**:
  - `strong` only if at a prior S/R level after a clear trend (3+ bars in same direction). Otherwise `weak`.
  - **Dragonfly** variant (long lower wick, tiny upper wick, close ≈ high) leans bullish at support.
  - **Gravestone** variant (long upper wick, tiny lower wick, close ≈ low) leans bearish at resistance.
- **Confirmation**: next bar's direction reveals which way indecision broke. A doji + bullish next bar = bullish reversal candidate.
- **False positives**: every "small body" bar in chop is a doji-shaped bar. Without trend context, these are meaningless.

### Marubozu

- **Definition**: `(H - L) / (H - L) ≈ 1` and `|C - O| / (H - L) > 0.95` — body fills almost the entire range. Bullish marubozu: `C > O`. Bearish: `C < O`.
- **Lean**: matches the body direction.
- **Family**: continuation.
- **Strength rules**:
  - `strong` if volume > 1.5× avg and the bar is in the trend's direction.
  - `weak` if it's a reversal attempt (counter-trend marubozu) — those tend to fail in trending regimes.
- **Confirmation**: next bar continues in the marubozu's direction.
- **False positives**: any wide-range trending bar; the no-wicks constraint is the discriminator.

### Hammer

- **Definition**: a bar where (a) lower wick is ≥ 2× body, (b) upper wick is ≤ 10% of range, (c) body is in the upper third of the range, (d) the bar is preceded by a downtrend (3+ red bars or sustained move down).
- **Lean**: bullish (reversal).
- **Family**: reversal.
- **Strength rules**:
  - `strong` if (a) lower wick ≥ 3× body, (b) bar fires at prior support, (c) volume above avg.
  - `moderate` if 2 of the 3.
  - `weak` if 1 of the 3 or in open air.
- **Confirmation**: `bars[i+1].close > bars[i].high`.
- **False positives**: same geometric shape in an uptrend is a "hanging man" (bearish lean), not a hammer. Trend context is the discriminator.

### Hanging man

- **Definition**: same geometry as a hammer, but the bar is preceded by an uptrend.
- **Lean**: bearish (reversal).
- **Family**: reversal.
- **Strength rules**: mirror of hammer. Volume confirmation matters more here — hanging men in low-volume uptrends often fail.
- **Confirmation**: `bars[i+1].close < bars[i].low`.
- **False positives**: see hammer. The geometric shape is the same; the trend it sits in is the discriminator.

### Inverted hammer

- **Definition**: a bar where (a) upper wick is ≥ 2× body, (b) lower wick is ≤ 10% of range, (c) body is in the lower third of the range, (d) preceded by a downtrend.
- **Lean**: bullish (reversal).
- **Family**: reversal.
- **Strength rules**: typically `moderate` — inverted hammers are less reliable than hammers without strong follow-through.
- **Confirmation**: `bars[i+1].close > bars[i].high` (decisively, not just by a tick).
- **False positives**: the "shooting star" geometric shape preceded by a downtrend is an inverted hammer.

### Shooting star

- **Definition**: same geometry as an inverted hammer, but preceded by an uptrend.
- **Lean**: bearish (reversal).
- **Family**: reversal.
- **Strength rules**: stronger when it occurs at prior resistance.
- **Confirmation**: `bars[i+1].close < bars[i].low`.
- **False positives**: see inverted hammer; trend context discriminates.

### Spinning top

- **Definition**: a bar where (a) body is in the middle of the range, (b) both wicks are ≥ 1× body, (c) `|C - O| / (H - L) < 0.3`.
- **Lean**: neutral.
- **Family**: indecision.
- **Strength rules**: rarely above `weak`. Useful as a flag, not a signal. Multiple consecutive spinning tops at a key level become more interesting (consolidation).
- **Confirmation**: next bar's direction.
- **False positives**: most quiet-volume bars look like spinning tops.

## Two-bar patterns

### Bullish engulfing

- **Definition**: bar `i-1` is bearish (`C < O`); bar `i` is bullish (`C > O`); `bars[i].open < bars[i-1].close` and `bars[i].close > bars[i-1].open` (body of `i` engulfs body of `i-1`).
- **Lean**: bullish (reversal).
- **Family**: reversal.
- **Strength rules**:
  - `strong` if (a) the engulfing bar's volume > 1.5× avg, (b) the prior trend is at least 3 bars down, (c) bar fires at prior support.
  - `moderate` for 2 of 3; `weak` for 1 or 0.
  - Larger engulfing relative to prior body = stronger.
- **Confirmation**: `bars[i+1].close > bars[i].close`.
- **False positives**: a wide-range green bar after a small red bar in chop. Trend context is the discriminator.

### Bearish engulfing

- **Definition**: mirror — bar `i-1` bullish, bar `i` bearish, body of `i` engulfs body of `i-1`.
- **Lean**: bearish (reversal).
- **Family**: reversal.
- **Strength rules**: mirror.
- **Confirmation**: `bars[i+1].close < bars[i].close`.
- **False positives**: see bullish engulfing.

### Piercing line

- **Definition**: bar `i-1` bearish, bar `i` opens below bar `i-1`'s low *and* closes above the midpoint of bar `i-1`'s body.
- **Lean**: bullish (reversal).
- **Family**: reversal.
- **Strength rules**: `moderate` typically; `strong` requires volume confirmation + prior downtrend + level context. The "deeper the penetration into the prior body, the stronger" rule applies — close > 70% of the way up the prior body is best.
- **Confirmation**: `bars[i+1].close > bars[i].close`.
- **False positives**: a strong green bar after a red bar without the gap-down open. The discriminator is `bars[i].open < bars[i-1].low`.

### Dark cloud cover

- **Definition**: bar `i-1` bullish, bar `i` opens above bar `i-1`'s high *and* closes below the midpoint of bar `i-1`'s body.
- **Lean**: bearish (reversal).
- **Family**: reversal.
- **Strength rules**: mirror of piercing line. Deeper penetration = stronger.
- **Confirmation**: `bars[i+1].close < bars[i].close`.
- **False positives**: see piercing line.

### Bullish harami

- **Definition**: bar `i-1` is bearish with a large body; bar `i` is bullish with a small body **inside** bar `i-1`'s body (`bars[i].open > bars[i-1].close` and `bars[i].close < bars[i-1].open`).
- **Lean**: bullish (reversal — softer than engulfing).
- **Family**: reversal.
- **Strength rules**: `moderate` at best. Harami are weaker than engulfing because the second bar shows hesitation, not commitment. Strength upgrades only with strong context (S/R level + downtrend + volume).
- **Confirmation**: `bars[i+1].close > bars[i].high` (decisive — harami often fail without good follow-through).
- **False positives**: a small green bar after a red bar in chop. The inside-body constraint discriminates.

### Bearish harami

- **Definition**: mirror.
- **Lean**: bearish.
- **Strength rules**: mirror.
- **Confirmation**: mirror.

## Three-bar patterns

### Morning star

- **Definition**:
  - Bar `i-2` is a long bearish bar (body > 60% of range).
  - Bar `i-1` is a small body (any direction), gapping below bar `i-2`'s close (or close to it).
  - Bar `i` is a long bullish bar that closes above the midpoint of bar `i-2`'s body.
- **Lean**: bullish (strong reversal).
- **Family**: reversal.
- **Strength rules**: morning stars are among the more reliable patterns. `moderate` is the default; `strong` if (a) volume on bar `i` > avg, (b) the prior downtrend is at least 5 bars, (c) the gap between `i-2` and `i-1` is clean. The "doji morning star" variant (bar `i-1` is a doji) is the strongest.
- **Confirmation**: `bars[i+1].close > bars[i].close`.
- **False positives**: any "down bar, small bar, up bar" sequence in chop. The gap and the deep penetration into bar `i-2`'s body discriminate.

### Evening star

- **Definition**: mirror.
- **Lean**: bearish (strong reversal).
- **Family**: reversal.
- **Strength rules**: mirror.
- **Confirmation**: mirror.

### Three white soldiers

- **Definition**: three consecutive bullish bars where (a) each opens within the prior bar's body, (b) each closes near its own high, (c) each closes above the prior bar's close.
- **Lean**: bullish (strong continuation, or a strong reversal from a downtrend).
- **Family**: continuation (or reversal in context).
- **Strength rules**:
  - `strong` after a downtrend or a base, with volume rising across the three bars.
  - `weak` in an already-extended uptrend — three white soldiers late in a trend often mark exhaustion, not continuation.
  - Each bar should have a body > 60% of its range.
- **Confirmation**: `bars[i+1].close >= bars[i].close` (trend continues).
- **False positives**: any three green bars in a row. The "open within prior body" and "close near high" constraints discriminate.

### Three black crows

- **Definition**: mirror.
- **Lean**: bearish.
- **Strength rules**: mirror.
- **Confirmation**: mirror.

## Notes on what's NOT in this catalog

The catalog above is the conservative subset. Excluded deliberately:

- **Three line strike, breakaway, advance block, deliberation, in-neck, on-neck, thrust** — rarer patterns with weak base rates per Bulkowski. If the user asks for them, surface that they're not in the catalog and ask whether they want them added (route to architect for a catalog expansion).
- **"Three inside up", "three outside up", "tweezer top/bottom"** — these are essentially decorated versions of patterns already in the catalog (harami with confirmation, engulfing with shadow rules). Reporting them as separate patterns adds noise.
- **Gartley, harmonic patterns, Fibonacci-based patterns** — these are not candlestick patterns; they're geometric patterns over many bars and rely on retracement ratios. They're outside the scope of `market-analyst`'s mode 1.
- **Elliott waves** — see best-practices.md. Excluded.

## How "strength" is computed in scan output

The patterns module (when it exists) should expose a `Pattern` record with at least these fields:

```python
class FiredPattern(BaseModel):
    pattern: str                      # canonical name from this catalog
    bar_index: int
    event_ts: datetime                # UTC
    direction: Literal["bullish", "bearish", "neutral"]
    geometric_score: float            # 0.0-1.0, how cleanly the bar(s) match the definition
    trend_context: Literal["with_trend", "against_trend", "in_chop"]
    volume_context: Literal["high", "above_avg", "below_avg", "thin"]
    level_context: Literal["at_level", "near_level", "open_air"]
    confirmation_status: Literal["confirmed", "pending", "failed", "no_next_bar"]
    strength: Literal["weak", "moderate", "strong"]
    notes: list[str]                  # human-readable observations
```

`strength` is a derived field; the patterns module should compute it as a function of the four contexts. A simple rule that works well:

- Start at `weak`.
- `geometric_score > 0.8` → +1 step.
- `trend_context` matches family (reversal against trend, or continuation with trend) → +1 step.
- `volume_context == "high"` → +1 step.
- `level_context == "at_level"` → +1 step.

Cap at `strong`. So `strong` requires 4+ favorable contexts; `moderate` requires 2-3; `weak` requires 0-1.

The skill's report uses this `strength` value directly. If everything in a scan fires `weak`, the headline says so honestly.

## Pattern naming canonicalization

When the user names a pattern colloquially, map to the canonical name:

| User says                          | Canonical name            |
|------------------------------------|---------------------------|
| "doji"                             | `doji`                    |
| "dragonfly", "dragonfly doji"      | `dragonfly_doji`          |
| "gravestone", "gravestone doji"    | `gravestone_doji`         |
| "hammer", "bullish hammer"         | `hammer`                  |
| "hanging man"                      | `hanging_man`             |
| "inverted hammer"                  | `inverted_hammer`         |
| "shooting star"                    | `shooting_star`           |
| "spinning top"                     | `spinning_top`            |
| "marubozu", "marubozu bullish"     | `marubozu`                |
| "engulfing", "bullish engulfing"   | `bullish_engulfing`       |
| "bearish engulfing"                | `bearish_engulfing`       |
| "piercing", "piercing line"        | `piercing_line`           |
| "dark cloud", "dark cloud cover"   | `dark_cloud_cover`        |
| "harami", "bullish harami"         | `bullish_harami`          |
| "bearish harami"                   | `bearish_harami`          |
| "morning star"                     | `morning_star`            |
| "evening star"                     | `evening_star`            |
| "three white soldiers", "soldiers" | `three_white_soldiers`    |
| "three black crows", "crows"       | `three_black_crows`       |

If the user names a pattern not in this table, ask — don't guess. Common cases: "tweezer", "fakeout", "false break" — these aren't single-pattern names in this catalog and need clarification.

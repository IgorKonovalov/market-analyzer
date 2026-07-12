# ADR-0083 — Squeeze and counter-trend volume are defined against canonical anchors, not proxied

> **Status:** proposed
> **Date:** 2026-07-12
> **Related plan(s):** [0090-squeeze-and-counter-trend-volume](../plans/0090-squeeze-and-counter-trend-volume.md)

## Context

The technical-analysis surface ([ADR-0023](0023-technical-analysis-surface.md)) exposes a Bollinger read (`bb_upper`/`bb_middle`/`bb_lower`/`bb_pct_b`) and a trailing ATR percentile (`atr_pct90`), and a separate `volume_confirmation` tool that scores how well volume backs the recent move. Two gaps surfaced from real `market-analyst` reads on `BTC-USD` and `ETH-USD` 1d (2026-07-12), each confirmed symbol-independent:

1. **There is no canonical squeeze metric, and the only compression signal the snapshot exposes — `atr_pct90` — can flatly contradict the actual Bollinger band-width.** On BTC, `atr_pct90` sat at the 4.4th percentile ("extreme compression") while the computed band-width was 36th percentile (below-median). On ETH the contradiction was wider and opposite in conclusion: `atr_pct90` = 3.3rd percentile while band-width was **73rd percentile and actively expanding** (18.5% → 21.9% over the last 10 bars). The mechanism is structural, not noise: ATR's 90-day percentile baseline is dominated by the February crash's large ranges, while band-width (standard deviation ÷ mean over a rolling 20-bar window that has since rolled past the crash) has re-widened. The two metrics measure different things over different windows and will disagree on any asset that had a large move one to four months back — i.e. most of the crypto universe at any given time. A user trusting `atr_pct90` as a squeeze proxy on ETH that day would have been wrong.

2. **Counter-trend volume is only available as a single aggregate score, and "counter-trend" is defined inconsistently across tools.** `volume_confirmation` returned 0.53 (BTC) / 0.55 (ETH), both `confirmed=false` — while a per-bar decomposition showed the same textbook bearish divergence both times: down-bars on average-or-heavier relative volume, rallies on thin volume. The 0.5-ish score averages that shape away; there is no way to see *which* bars carried the opposing volume. Worse, `volume_confirmation` fixes "the trend" as the sign of the net move over its lookback window (`close[-1]` vs `close[-1-lookback]`), which can disagree with the snapshot's own `trend` label (EMA/ADX + Ichimoku veto, [ADR-0067](0067-ichimoku-in-trend-classification.md)) and with Supertrend/Ichimoku. So "counter-trend" means different things depending on which tool you ask.

Both gaps are definitional, not merely missing features: the question is not "add another indicator" but "what *is* a squeeze" and "counter-trend relative to *what*". Those are decisions that a future maintainer will want to revisit, which is what makes this an ADR rather than a code comment.

## Decision

We will define both conditions against explicit, canonical, trailing anchors rather than proxies.

**Squeeze.** The canonical compression metric is the **Bollinger band-width** `(upper − lower) / middle` and its trailing percentile rank `bb_width_pct90`, computed with the same `_percentile_rank` machinery already used for `rsi_pct90` / `atr_pct90` / `vol_pct90`. In addition we add a **Keltner channel** indicator and a boolean **`squeeze_on`** flag using the classic TTM definition — the Bollinger band (20, 2.0) sitting *inside* the Keltner channel (20, 1.5 × ATR) on the latest bar. `atr_pct90` remains in the snapshot as a volatility-percentile fact, but it is no longer the thing a caller reads to judge a squeeze; `bb_width_pct90` and `squeeze_on` are. All three are trailing and lookahead-safe by construction, per ADR-0023's seam.

**Counter-trend volume.** A new `counter_trend_volume` read classifies each bar in the trailing window as with-trend or counter-trend **relative to the snapshot's canonical `trend` label** — the same EMA/ADX + Ichimoku-veto classification every other tool already reports — and reports the per-bar decomposition (bar timestamp, up/down direction, relative volume, counter-trend flag) plus the aggregate counter-trend volume share. When the anchor trend is `sideways`, there is no trend to run counter to; the read says so explicitly (counter-trend is undefined, not silently forced onto a net-move sign). The existing `volume_confirmation` tool and its net-move framing are left unchanged; the new read is the one that answers "counter-trend relative to the established trend".

## Consequences

### Positive
- A caller asking "is this a squeeze" gets one number (`bb_width_pct90`) and one boolean (`squeeze_on`) that mean what the textbook means, instead of an ATR percentile that can say the opposite.
- "Counter-trend" has a single definition across the whole surface — the snapshot trend — so the volume read, the trend label, and Supertrend/Ichimoku no longer quietly disagree about which way the trend points.
- The per-bar decomposition exposes the divergence *shape* (which bars carried opposing volume) that the aggregate score hides.
- Both additions reuse existing seams (`_percentile_rank`, `analysis/indicators.py`, the snapshot's `trend`) — no new anti-lookahead surface to re-verify beyond the per-function truncation-invariance tests ADR-0023 already mandates.

### Negative
- **We now own a Keltner channel and a squeeze definition with tunable constants** (band-width period, the two multipliers, the ATR period). Like the candlestick thresholds ADR-0023 accepted, there is no single "correct" reference — TTM's 20/2.0/1.5 is a convention, not a law — so these become project constants we own and may re-tune. Bugs in the ATR-based Keltner math propagate to `squeeze_on`.
- **Two overlapping counter-trend notions coexist.** `volume_confirmation` keeps its net-move anchor; the new tool uses the snapshot trend. A caller could compare them and be confused. Mitigation: the models and docs state their anchor explicitly, and the new tool is the documented answer for trend-relative counter-trend.
- Additive schema growth: three new keys in the snapshot `indicators` dict and a new tool + models. Every snapshot consumer sees the new keys (additive, but the pinned field-set test must be updated).

### Neutral
- `squeeze_on` is a categorical encoded as a float (`1.0`/`0.0`) inside the `indicators` dict, matching the existing precedent of `supertrend_direction` (`±1.0`). This keeps the snapshot schema a flat `dict[str, float | None]` rather than introducing a nested typed sub-model.

## Alternatives considered

### Alternative A — Keep `atr_pct90` as the squeeze proxy; add nothing
Rejected. It is the status quo, and it is the bug: `atr_pct90` and band-width demonstrably reach opposite conclusions on real, current data, and the divergence is structural (crash-dominated ATR baseline vs rolling band-width window), so it will keep recurring. A proxy that is wrong on ETH today is not a squeeze signal.

### Alternative B — `bb_width_pct90` only; skip Keltner / `squeeze_on`
Rejected for this plan (though it was a live option in the interview). The band-width percentile resolves the contradiction, but the *canonical* squeeze most practitioners mean is the TTM BB-inside-Keltner construction. Since ATR already exists, the Keltner add is cheap, and shipping the percentile without the boolean squeeze flag would leave the well-known form unrepresented. We accept owning the Keltner constants to get it.

### Alternative C — Anchor counter-trend to the net-move sign (as `volume_confirmation` does)
Rejected as the *canonical* anchor. It is internally consistent but is exactly the source of the cross-tool ambiguity: it can label the trend "up" while the snapshot and Supertrend read sideways/down. Anchoring to the snapshot `trend` gives the surface one definition of "the trend". We keep net-move only where it already lives (`volume_confirmation`), unchanged.

### Alternative D — Parameterize the counter-trend anchor (caller picks snapshot-trend vs net-move)
Rejected. Flexibility here is a liability: it pushes a definitional choice most callers should not have to make onto every caller, and it re-admits the ambiguity we are trying to remove. One canonical anchor, documented.

## Notes
- Evidence: `market-analyst` session 2026-07-12 — BTC `atr_pct90` 4.4th pct vs band-width 36th; ETH `atr_pct90` 3.3rd pct vs band-width 73rd (expanding); `volume_confirmation` 0.53 / 0.55 both unconfirmed while the per-bar shape showed heavy counter-trend down-bars against thin rallies on both names.
- `bb_width_pct90`'s percentile mechanics ride ADR-0023 (another trailing percentile); the decision recorded here is the *semantics* — that band-width, not ATR, is the squeeze anchor, and that counter-trend is anchored to the snapshot trend.

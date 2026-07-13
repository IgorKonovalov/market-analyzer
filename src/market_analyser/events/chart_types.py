"""Shared chart value-types composed by the chart event payloads (ADR-0017).

These are the drawing primitives the `chart.*` envelope schemas in
`events/payloads.py` compose — an overlay descriptor, a marker, a trendline and
its anchor point — not events in their own right (none appears in
`TYPE_REGISTRY`). Split out of `events/__init__.py` in Plan 0072 phase 2 so
`payloads.py` holds the ~20 envelope schemas and this module holds the value-
types they reference; both are re-exported from `events/__init__.py`, so
`from market_analyser.events import OverlaySpec, Marker, ...` is unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class OverlaySpec(BaseModel):
    """Chart overlay descriptor. The literal set is intentionally narrow — adding
    a new kind is additive (new literal value, possibly new optional fields)
    and does NOT bump `chart.show`/`chart.update` payload versions.

    Two families share this one model, kept disjoint by `_validate_kind_fields`:
    the indicator overlays (`ema`/`sma`/`rsi`/`macd`/`bbands`/`supertrend`,
    carrying an optional `period`, plus `supertrend`'s ATR `multiplier`) and the
    generic `price_line` (a horizontal line at `price` with a `label` and an
    optional support/resistance `role`) — the channel the agent uses to push S/R
    levels from `analyze_symbol` (Plan 0047). The new fields are all
    optional/defaulted, so an indicator overlay still serialises to exactly
    `{kind, period}` under the bus's `exclude_none` dump — existing overlays are
    byte-unchanged on the wire.

    `supertrend` (Plan 0049) is additive like `price_line` was: a new indicator
    kind carrying `period` + the optional `multiplier`. The renderer mirror and
    client-side draw land in phase 9 (ui-builder).

    `ichimoku` (Plan 0073, ADR-0067) is the same additive move: a new indicator
    kind carrying its own four optional period fields (`conversion`/`base`/
    `span_b`/`displacement`); absent periods mean the renderer applies the classic
    9/26/52/26 defaults. Like the other indicator kinds it accepts no
    `price`/`label`/`role`. The displaced, filled cloud render lands in phase 4
    (ui-builder).

    `obv` (Plan 0076) is the leanest additive move yet: On-Balance Volume is
    cumulative and unparameterized, so the kind carries no fields at all — a bare
    `obv` overlay serialises to exactly `{kind}`. Like the other indicator kinds it
    accepts no `price`/`label`/`role`. The renderer computes OBV from the bars it
    holds and draws it in a separate auto-scaled pane (phase 2, ui-builder).

    `bbands` (Plan 0082) needs no new field either: Bollinger Bands carry `period`
    (default 20, applied by the renderer when absent) and reuse the existing
    `multiplier` field as the standard-deviation multiplier `k` (default 2.0) — the
    same field `supertrend` reuses for its ATR multiplier. The three bands (SMA
    middle band, plus/minus `k` population standard deviations) are computed
    client-side and drawn on the price pane (phase 2, ui-builder). Like the other
    indicator kinds it accepts no `price`/`label`/`role`.

    `fibonacci` / `pivot_points` / `anchored_vwap` (Plan 0092) are the price-
    structure geometry overlays, each drawn on the price pane. They carry their own
    optional parameters (kept disjoint from the other kinds by the validator):
    `fibonacci` an optional `fib_kind` (retracement/extension) plus an optional
    explicit swing anchor (`high_anchor_ts`/`high_anchor_price` +
    `low_anchor_ts`/`low_anchor_price`, supplied whole or not at all); `pivot_points`
    an optional `method` (floor/camarilla/woodie); `anchored_vwap` an optional
    `anchor_ts`. When the anchor/method params are absent the renderer auto-anchors
    from the bars it holds (dominant swing / last completed bar / dominant-swing
    start), the ADR-0077 client path; the agent overrides via `show_chart`/
    `update_chart`. Like the indicator kinds they accept no `price`/`label`/`role`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "ema",
        "sma",
        "rsi",
        "macd",
        "bbands",
        "price_line",
        "supertrend",
        "ichimoku",
        "obv",
        # Plan 0091: momentum oscillators, each drawn in its own v5 sub-pane by the
        # renderer (client-computed from bars). Fieldless like `obv` — the renderer
        # applies the classic default periods; they accept no price/label/role.
        "stochastic",
        "stoch_rsi",
        "cci",
        "williams_r",
        "roc",
        # Plan 0091 phase 7: volume-weighted money-flow, each in its own sub-pane.
        "mfi",
        "cmf",
        "ad_line",
        # Plan 0092: price-structure geometry overlays (price pane).
        "fibonacci",
        "pivot_points",
        "anchored_vwap",
    ]
    period: int | None = None
    multiplier: float | None = None  # supertrend's ATR multiplier; None on other kinds
    # `ichimoku`-only period fields (None on other kinds); absent -> classic defaults.
    conversion: int | None = None
    base: int | None = None
    span_b: int | None = None
    displacement: int | None = None
    # `price_line`-only fields (None on indicator overlays, enforced below).
    price: float | None = None
    label: str | None = None
    role: Literal["support", "resistance"] | None = None
    # Plan 0092 `fibonacci`-only params (None on other kinds); anchors absent ->
    # the renderer auto-anchors to the dominant swing.
    fib_kind: Literal["retracement", "extension"] | None = None
    high_anchor_ts: datetime | None = None
    high_anchor_price: float | None = None
    low_anchor_ts: datetime | None = None
    low_anchor_price: float | None = None
    # Plan 0092 `pivot_points`-only param (None -> renderer default "floor").
    method: Literal["floor", "camarilla", "woodie"] | None = None
    # Plan 0092 `anchored_vwap`-only param (None -> renderer auto-anchors).
    anchor_ts: datetime | None = None

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> OverlaySpec:
        """Keep the overlay families disjoint: `price_line` requires both `price`
        and `label`; the indicator kinds accept none of the geometry fields; each
        Plan-0092 structure kind accepts only its own optional params (a
        `fibonacci` explicit anchor is all-or-none)."""
        structure_fields = (
            "fib_kind",
            "high_anchor_ts",
            "high_anchor_price",
            "low_anchor_ts",
            "low_anchor_price",
            "method",
            "anchor_ts",
        )

        def _forbid(names: tuple[str, ...]) -> None:
            offending = [n for n in names if getattr(self, n) is not None]
            if offending:
                raise ValueError(f"{self.kind} overlay does not accept {', '.join(offending)}")

        if self.kind == "price_line":
            if self.price is None or self.label is None:
                raise ValueError("price_line overlay requires both 'price' and 'label'")
            _forbid(structure_fields)
            return self

        if self.price is not None or self.label is not None or self.role is not None:
            raise ValueError(f"{self.kind} overlay does not accept price/label/role")

        if self.kind == "fibonacci":
            _forbid(("method", "anchor_ts"))
            anchors = (
                self.high_anchor_ts,
                self.high_anchor_price,
                self.low_anchor_ts,
                self.low_anchor_price,
            )
            if any(a is not None for a in anchors) and any(a is None for a in anchors):
                raise ValueError(
                    "fibonacci overlay anchor must be supplied whole (all four) or none"
                )
        elif self.kind == "pivot_points":
            _forbid(
                (
                    "fib_kind",
                    "high_anchor_ts",
                    "high_anchor_price",
                    "low_anchor_ts",
                    "low_anchor_price",
                    "anchor_ts",
                )
            )
        elif self.kind == "anchored_vwap":
            _forbid(
                (
                    "fib_kind",
                    "high_anchor_ts",
                    "high_anchor_price",
                    "low_anchor_ts",
                    "low_anchor_price",
                    "method",
                )
            )
        else:
            _forbid(structure_fields)
        return self


class Marker(BaseModel):
    """`chart.highlight` marker: a single annotation to render on the chart.

    The base shape is a point-in-time arrow at `event_ts` keyed by `kind`
    (bullish/bearish). Plan 0049 (ADR-0045) adds first-class pattern identity and
    an optional bar span, all additively so the existing `highlight_pattern` tool
    keeps emitting `{event_ts, kind, label?}` markers unchanged:

    - `pattern` — the detector name (`"morning_star"`); identity, not the
      free-text `label`. Lets the renderer key dedup on `(event_ts, pattern, kind)`
      so two distinct patterns on the same bar/direction stop collapsing.
    - `kind="neutral_marker"` — so neutral patterns (doji, neutral marubozu) can be
      emitted faithfully; `kind` stays the rendering discriminator.
    - `span_start_ts`/`span_end_ts` — present (together) for a multi-bar pattern's
      span; absent for single-bar patterns (≡ a point marker on `event_ts`).
    - `strength` — the detector's conviction score, so the renderer styles without
      re-deriving it.

    Under the bus's `exclude_none` dump an unset-span point marker still serialises
    to exactly its set fields — the wire stays clean.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_ts: datetime
    kind: Literal["bullish_marker", "bearish_marker", "neutral_marker"]
    label: str | None = None
    pattern: str | None = None
    span_start_ts: datetime | None = None
    span_end_ts: datetime | None = None
    strength: float | None = None

    @model_validator(mode="after")
    def _validate_span(self) -> Marker:
        """A span must be supplied whole and forward-ordered: both endpoints
        together (a half-span is meaningless) and `span_end_ts >= span_start_ts`."""
        if (self.span_start_ts is None) != (self.span_end_ts is None):
            raise ValueError("span requires both span_start_ts and span_end_ts (or neither)")
        if (
            self.span_start_ts is not None
            and self.span_end_ts is not None
            and self.span_end_ts < self.span_start_ts
        ):
            raise ValueError("span_end_ts must be >= span_start_ts")
        return self


class TrendPoint(BaseModel):
    """A single `(time, price)` anchor of a chart trendline (ADR-0049).

    `ts` is a timestamp, not a bar index — consistent with `Marker.event_ts` /
    `span_*_ts`, so the renderer maps it the same way and the line survives a
    bar-set change as long as the anchor times stay in range."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    price: float


class TrendlineSpec(BaseModel):
    """A sloped multi-point line on the chart (ADR-0049, Plan 0052): a
    head-and-shoulders neckline or one bounding trendline of a triangle/wedge.

    Carried on the dedicated `chart.trendlines v1` event (ADR-0059, Plan 0064) —
    its own channel, so a `chart.show` can no longer wipe the lines and they are
    recomputed from current bars rather than persisted. `style` is the
    forming-vs-confirmed cue (`dashed` = forming, `solid` = confirmed);
    `role`/`pattern` give the renderer theming and identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: list[TrendPoint]
    role: (
        Literal["neckline", "upper_trendline", "lower_trendline", "projection", "skeleton", "base"]
        | None
    ) = None
    style: Literal["solid", "dashed"] = "solid"
    label: str | None = None
    pattern: str | None = None

    @model_validator(mode="after")
    def _validate_points(self) -> TrendlineSpec:
        """A line needs at least two anchors — a one-point 'line' is undrawable."""
        if len(self.points) < 2:
            raise ValueError("trendline requires at least 2 points")
        return self


__all__ = [
    "Marker",
    "OverlaySpec",
    "TrendPoint",
    "TrendlineSpec",
]

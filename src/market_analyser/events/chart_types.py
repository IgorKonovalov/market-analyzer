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
    client-side draw land in phase 9 (ui-builder)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ema", "sma", "rsi", "macd", "bbands", "price_line", "supertrend"]
    period: int | None = None
    multiplier: float | None = None  # supertrend's ATR multiplier; None on other kinds
    # `price_line`-only fields (None on indicator overlays, enforced below).
    price: float | None = None
    label: str | None = None
    role: Literal["support", "resistance"] | None = None

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> OverlaySpec:
        """Keep the two overlay families disjoint: `price_line` requires both
        `price` and `label` (a labelless line is useless on the chart); the
        indicator kinds accept neither `price`/`label`/`role`."""
        if self.kind == "price_line":
            if self.price is None or self.label is None:
                raise ValueError("price_line overlay requires both 'price' and 'label'")
        elif self.price is not None or self.label is not None or self.role is not None:
            raise ValueError(f"{self.kind} overlay does not accept price/label/role")
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
    role: Literal["neckline", "upper_trendline", "lower_trendline"] | None = None
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

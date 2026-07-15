"""Freeform chart-annotation drawing types (ADR-0091, Plan 0097).

`DrawingSpec` is the ONE shape describing a freeform chart drawing — the eleven
geometry kinds the drawing dock supports (six from Plan 0097, five trading-idea
kinds from Plan 0104) — defined here in the sidecar and mirrored to TS, so both
annotation sources ride the same model:

- **Agent source** (over the wire): the `annotate_chart` MCP tool validates a
  set of these and publishes them on the `chart.annotations v1` event — a
  declarative replace of the *agent* annotation set for a symbol.
- **User source** (renderer-local): the drawing dock persists the same shape in
  `localStorage['ma.userDrawings']`; user drawings never cross the wire.

`provenance` is the discriminator that scopes the renderer's merge and edit
affordances (user drawings are editable, agent drawings hide-only). Geometry is
anchored to `(time, price)` data coordinates — a drawing is a claim about price
over time, keyed per SYMBOL and rendered across every timeframe (ADR-0091), so
no `timeframe` appears anywhere in this module.

Sibling of `chart_types.py` (the `chart.show`/`chart.update` value-types)
rather than an addition to it: `OverlaySpec`/`Marker`/`TrendlineSpec` describe
agent-computed *analysis output*, while a `DrawingSpec` is a freeform mark from
either source with its own per-kind geometry rules. Plan 0104 extends the
`kind` set in place with the trading-idea kinds (long/short position, the three
range measures) and the ADR-0029 advisory fields (`rationale`/`basis`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

# hline/vline and the two position kinds are fixed by a single anchor (the
# price, resp. the time, of that anchor; a position's one anchor is its entry);
# every other kind needs its two defining anchors.
_POINT_COUNT_BY_KIND: dict[str, int] = {
    "trendline": 2,
    "ray": 2,
    "hline": 1,
    "vline": 1,
    "rect": 2,
    "fib": 2,
    # Plan 0104: trading-idea kinds.
    "long_position": 1,
    "short_position": 1,
    "date_range": 2,
    "price_range": 2,
    "date_price_range": 2,
}

# The two kinds carrying an entry/stop/target triple (ADR-0099): the anchor is
# the entry, and `stop`/`target` are required prices whose ordering encodes the
# direction. Risk-reward is DERIVED at render (`|target-entry| / |entry-stop|`),
# never stored on the wire.
_POSITION_KINDS: frozenset[str] = frozenset({"long_position", "short_position"})


class TimePricePoint(BaseModel):
    """A single `(time, price)` anchor of a drawing.

    Same shape as `TrendPoint`/`PivotPoint` but owned by the drawing layer:
    `ts` is a timestamp, not a bar index, so a drawing survives timeframe
    switches and bar-set changes as long as its anchor times stay mappable
    (the renderer routes off-grid times through the ADR-0059 logical-coordinate
    fallback). For an `hline` only `price` is meaningful; for a `vline` only
    `ts` — the partner coordinate is carried but ignored by the renderer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    price: float


class DrawingStyle(BaseModel):
    """Optional per-drawing style; absent fields fall to renderer defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    color: str | None = None
    width: int | None = Field(default=None, gt=0)


class DrawingSpec(BaseModel):
    """One freeform chart drawing (ADR-0091): the shared wire type (agent
    source, `chart.annotations v1`) AND the renderer's persistence record
    (user source, `ma.userDrawings`).

    Kinds and their geometry (`points` counts enforced by the validator):

    - `trendline` — segment between 2 anchors.
    - `ray` — from anchor 1 through anchor 2, extended to the visible edge.
    - `hline` — horizontal line at the single anchor's `price`.
    - `vline` — vertical line at the single anchor's `ts`.
    - `rect` — zone spanning the 2 anchors as opposite corners.
    - `fib` — Fibonacci retracement grid anchored to 2 points (the renderer
      draws the standard 0/23.6/38.2/50/61.8/100 levels between them).
    - `long_position` / `short_position` (Plan 0104) — one anchor at
      `(time, entry)` plus a required `stop` and `target` price. Ordering
      encodes direction and is validated: long needs `stop < entry < target`,
      short needs `target < entry < stop`. Risk-reward is DERIVED at render,
      never stored.
    - `date_range` / `price_range` / `date_price_range` (Plan 0104) — two
      anchors; the bar-count / Δt / Δprice / % readouts are DERIVED at render.

    `rationale`/`basis` (Plan 0104, ADR-0029) are optional here — a user's
    private position note needs neither — but the `annotate_chart` tool
    requires both, non-empty, on an *agent-placed* position kind: a directional
    box the agent draws is a recommendation, and a recommendation must carry its
    rationale and basis (the advisory boundary extended to geometry).

    `id` is the stable identity for dedup, edit, and hide tracking; it defaults
    at construction (the `Annotation.id` precedent) so the renderer and the
    agent may omit it, but a re-pushing agent should supply its own stable ids
    so the renderer's hide state survives the re-push."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "trendline",
        "ray",
        "hline",
        "vline",
        "rect",
        "fib",
        "long_position",
        "short_position",
        "date_range",
        "price_range",
        "date_price_range",
    ]
    points: list[TimePricePoint]
    stop: float | None = None
    target: float | None = None
    rationale: str | None = None
    basis: str | None = None
    provenance: Literal["agent", "user"]
    style: DrawingStyle | None = None
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)

    @model_validator(mode="after")
    def _validate_geometry(self) -> DrawingSpec:
        """Each kind is fixed by an exact anchor count, and the position kinds
        carry an extra stop/entry/target invariant — anything else is malformed
        geometry, rejected (never silently dropped or truncated).

        `stop`/`target` belong to the position kinds ALONE: a line or range that
        carries them is malformed input, rejected rather than silently ignored."""
        expected = _POINT_COUNT_BY_KIND[self.kind]
        if len(self.points) != expected:
            raise ValueError(
                f"{self.kind} drawing requires exactly {expected} "
                f"point{'s' if expected != 1 else ''}, got {len(self.points)}"
            )
        if self.kind in _POSITION_KINDS:
            if self.stop is None or self.target is None:
                raise ValueError(f"{self.kind} requires both a stop and a target price")
            entry = self.points[0].price
            if self.kind == "long_position":
                if not (self.stop < entry < self.target):
                    raise ValueError(
                        "long_position requires stop < entry < target, got "
                        f"stop={self.stop}, entry={entry}, target={self.target}"
                    )
            else:  # short_position
                if not (self.target < entry < self.stop):
                    raise ValueError(
                        "short_position requires target < entry < stop, got "
                        f"stop={self.stop}, entry={entry}, target={self.target}"
                    )
        elif self.stop is not None or self.target is not None:
            raise ValueError(f"{self.kind} must not carry stop/target (position kinds only)")
        return self


__all__ = [
    "DrawingSpec",
    "DrawingStyle",
    "TimePricePoint",
]

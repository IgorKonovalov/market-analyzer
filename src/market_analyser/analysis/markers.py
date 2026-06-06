"""Pattern-sweep markers: map detected patterns to `chart.highlight` markers
(Plan 0049, ADR-0045).

Pure, no IO — the shared core behind BOTH the `scan_patterns` MCP tool (agent
trigger) and the `POST /scan_patterns` route (UI trigger), so the two paths emit
byte-identical markers for identical bars and cannot drift. Sweep results are
*derived* and never persisted: the same bars always yield the same markers, so
they are recomputed on demand rather than stored (ADR-0045).

This module is the one place the analysis layer references the `chart.highlight`
`Marker` wire type. It has no event-bus or persistence dependency — it maps data
to data — so the layering stays clean: the tool/route own the IO (fetch + publish)
and call this for the pure transform.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from market_analyser.analysis.patterns import detect_patterns, resolve_span
from market_analyser.analysis.types import Direction, PatternHit
from market_analyser.data.types import Bar
from market_analyser.events import Marker

_MarkerKind = Literal["bullish_marker", "bearish_marker", "neutral_marker"]

# A pattern's direction is its marker's rendering discriminator; neutral patterns
# (doji, neutral marubozu) map to the `neutral_marker` kind added in phase 2.
_DIRECTION_TO_KIND: dict[Direction, _MarkerKind] = {
    "bullish": "bullish_marker",
    "bearish": "bearish_marker",
    "neutral": "neutral_marker",
}


def patterns_to_markers(hits: Sequence[PatternHit], bars: Sequence[Bar]) -> list[Marker]:
    """Map detected pattern hits to span-bearing `chart.highlight` markers.

    The completing bar gives each marker's `event_ts`; the hit's direction maps to
    the marker `kind`; a multi-bar pattern carries a resolved
    `(span_start_ts, span_end_ts)` while a single-bar pattern leaves the span unset
    (≡ a point marker). `strength` rides along so the renderer styles by conviction
    without re-deriving it. Pure — no event bus, no persistence.
    """
    markers: list[Marker] = []
    for hit in hits:
        span_start_ts: datetime | None = None
        span_end_ts: datetime | None = None
        if hit.span_bars > 1:
            span_start_ts, span_end_ts = resolve_span(hit, bars)
        markers.append(
            Marker(
                event_ts=bars[hit.bar_index].event_ts,
                kind=_DIRECTION_TO_KIND[hit.direction],
                pattern=hit.pattern,
                strength=hit.strength,
                span_start_ts=span_start_ts,
                span_end_ts=span_end_ts,
            )
        )
    return markers


def markers_for_range(
    bars: Sequence[Bar],
    *,
    patterns: Sequence[str] | None = None,
    min_strength: float | None = None,
) -> list[Marker]:
    """Detect every pattern over `bars`, apply the optional filters, and map the
    survivors to markers.

    This is the shared scan core: the MCP tool and the HTTP route each fetch their
    own bars, then call this — so identical bars + filters produce identical
    markers. `patterns` keeps only the named detectors; `min_strength` keeps only
    hits at or above the threshold. Both default to off (the full sweep).
    """
    hits = detect_patterns(bars)
    if patterns is not None:
        wanted = set(patterns)
        hits = [h for h in hits if h.pattern in wanted]
    if min_strength is not None:
        hits = [h for h in hits if h.strength >= min_strength]
    return patterns_to_markers(hits, bars)


__all__ = ["markers_for_range", "patterns_to_markers"]

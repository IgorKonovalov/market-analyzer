"""Pydantic types for annotations (Plan 0006 phase 2).

`Annotation` is the boundary-validated model that crosses every seam: MCP tools
in, repository round-trip, HTTP read route out. Per CLAUDE.md, validation
happens here once; downstream code may trust the values.

`id` and `created_at` default at construction time so the MCP tool can build an
`Annotation` from agent-supplied fields without thinking about identity or
wall-clock — the repository then stores the populated row as-is.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Per ADR-0007/ADR-0028. This is the canonical *set*; the per-timeframe details
# (bar duration, Yahoo interval, resampled-from, history cap) live in the
# `data/timeframes.py` registry. The two views are kept honest by a parity test
# (`tests/data/test_timeframes.py`) rather than a cross-layer import, so this
# frozenset stays free of any `data` dependency. Widen both together.
SUPPORTED_TIMEFRAMES: frozenset[str] = frozenset({"1d", "1h", "15m", "1w"})


class AnnotationKind(StrEnum):
    """The set of marker kinds the chart can render. New kinds land via plan + UI work."""

    BULLISH_MARKER = "bullish_marker"
    BEARISH_MARKER = "bearish_marker"


class Annotation(BaseModel):
    """An agent-written chart annotation. Boundary-validated; trustable downstream."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: str
    event_ts: datetime
    kind: AnnotationKind
    label: str | None = None
    agent_id: str = Field(default="unknown", min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @field_validator("symbol", mode="before")
    @classmethod
    def _symbol_upper(cls, v: object) -> str:
        if not isinstance(v, str) or not v:
            raise ValueError("symbol must be a non-empty string")
        return v.upper()

    @field_validator("timeframe")
    @classmethod
    def _timeframe_supported(cls, v: str) -> str:
        if v not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"timeframe {v!r} not supported (supported: {sorted(SUPPORTED_TIMEFRAMES)})",
            )
        return v

    @field_validator("event_ts", "created_at")
    @classmethod
    def _utc_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return v.astimezone(UTC)


__all__ = ["SUPPORTED_TIMEFRAMES", "Annotation", "AnnotationKind"]

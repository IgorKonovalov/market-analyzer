"""Shared MCP-boundary validation helpers (Plan 0017).

These were defined inline inside `mcp_app.create_mcp_components`; extracting the
tools to per-module `register_*` functions left several of them needing the same
checks, so they live here as a single package-internal home. Behaviour is
verbatim from the original inline definitions — no logic change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_analyser.annotations.types import SUPPORTED_TIMEFRAMES
from market_analyser.api.events import OverlaySpec


def _require_supported_timeframe(timeframe: str) -> None:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"timeframe {timeframe!r} not supported (supported: {sorted(SUPPORTED_TIMEFRAMES)})",
        )


def _require_non_empty_symbol(symbol: str) -> None:
    if not symbol:
        raise ValueError("symbol must be a non-empty string")


def _require_ordered_range(range_start: datetime | None, range_end: datetime | None) -> None:
    if range_start is not None and range_end is not None and range_end < range_start:
        raise ValueError(
            f"range_end {range_end.isoformat()} must be >= range_start {range_start.isoformat()}",
        )


def _parse_overlays(raw: list[dict[str, Any]] | None) -> list[OverlaySpec] | None:
    if raw is None:
        return None
    return [OverlaySpec.model_validate(item) for item in raw]


__all__ = [
    "_parse_overlays",
    "_require_non_empty_symbol",
    "_require_ordered_range",
    "_require_supported_timeframe",
]

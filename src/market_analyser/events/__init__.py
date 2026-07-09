"""SSE event bus + typed envelope schemas (ADR-0017, Plan 0007 phase 2).

This package is split into three modules (Plan 0072 phase 2):

- `payloads` — the ~20 per-version Pydantic envelope schemas + the `TYPE_REGISTRY`.
- `chart_types` — the shared chart value-types (`OverlaySpec`, `Marker`,
  `TrendPoint`, `TrendlineSpec`) the `chart.*` schemas compose.
- `bus` — the `EventBus` / `Subscription` pub/sub runtime and the `Envelope`
  wire type.

Everything is re-exported here so the public import surface is unchanged:
`from market_analyser.events import EventBus, ChartShowPayloadV1, ...` resolves
exactly as before the split.
"""

from __future__ import annotations

from market_analyser.events.bus import (
    DEFAULT_QUEUE_CAP,
    Envelope,
    EventBus,
    Subscription,
    UnknownEventTypeError,
)
from market_analyser.events.chart_types import (
    Marker,
    OverlaySpec,
    TrendlineSpec,
    TrendPoint,
)
from market_analyser.events.payloads import (
    TYPE_REGISTRY,
    AlertTriggeredPayloadV1,
    ChartHighlightPayloadV1,
    ChartShowPayloadV1,
    ChartTrendlinesPayloadV1,
    ChartUpdateDroppedPayloadV1,
    ChartUpdatePayloadV1,
    DefiPnlCompletedPayloadV1,
    DefiPnlFailedPayloadV1,
    DefiPnlStartedPayloadV1,
    DefiScanCompletedPayloadV1,
    DefiScanFailedPayloadV1,
    DefiScanProgressPayloadV1,
    DefiScanStartedPayloadV1,
    ForecastCompletedPayloadV1,
    GapWindow,
    OhlcvBackfilledPayloadV1,
    OhlcvBackfillFailedPayloadV1,
    OhlcvBackfillStartedPayloadV1,
    RecommendationCompletedPayloadV1,
    RunCompletedPayloadV1,
    SignalEvaluatedPayloadV1,
)

__all__ = [
    "DEFAULT_QUEUE_CAP",
    "TYPE_REGISTRY",
    "AlertTriggeredPayloadV1",
    "ChartHighlightPayloadV1",
    "ChartShowPayloadV1",
    "ChartTrendlinesPayloadV1",
    "ChartUpdateDroppedPayloadV1",
    "ChartUpdatePayloadV1",
    "DefiPnlCompletedPayloadV1",
    "DefiPnlFailedPayloadV1",
    "DefiPnlStartedPayloadV1",
    "DefiScanCompletedPayloadV1",
    "DefiScanFailedPayloadV1",
    "DefiScanProgressPayloadV1",
    "DefiScanStartedPayloadV1",
    "Envelope",
    "EventBus",
    "ForecastCompletedPayloadV1",
    "GapWindow",
    "Marker",
    "OhlcvBackfillFailedPayloadV1",
    "OhlcvBackfillStartedPayloadV1",
    "OhlcvBackfilledPayloadV1",
    "OverlaySpec",
    "RecommendationCompletedPayloadV1",
    "RunCompletedPayloadV1",
    "SignalEvaluatedPayloadV1",
    "Subscription",
    "TrendPoint",
    "TrendlineSpec",
    "UnknownEventTypeError",
]

"""Crypto Fear & Greed adapter — Plan 0011 (ADR-0019, ADR-0007); historized Plan 0055.

One HTTP call to Alternative.me's free, unauthenticated index endpoint
(`GET https://api.alternative.me/fng/?limit=1`) returns the current crypto
Fear & Greed reading: a 0-100 value plus a five-bucket classification. The call
goes through `ResilientHttpClient` (shared TTL cache / retry / backoff /
concurrency cap) — the index updates roughly once a day, so a 5-minute TTL
absorbs the "agent asks twice in a minute" pattern without ever serving a stale
reading in practice.

Upstream encodes both `value` and `timestamp` as strings; the adapter coerces
them and hands the result to `MarketSentimentSample`, which validates the range
(`0..100`) and the label (`Literal` over the five canonical buckets) at the
boundary — an out-of-range value or an unknown label raises rather than being
silently truncated or passed through.

Plan 0055 phase 2 (ADR-0051) adds the historized side:

- `fetch_series` implements `MetricSeriesSource` for `fng.value` — `?limit=0`
  returns the FULL daily history (back to 2018-02-01) in one keyless call.
- `backfill_series` upserts that history into the wired metric store; re-runs
  are idempotent because a same-value re-upsert is a repository no-op.
- `fetch_current` write-throughs the current reading to the store (keyed by the
  upstream publish timestamp, so a second call the same day is a no-op). The
  write-through is best-effort: a storage error is logged and the sample is
  still returned — persistence must never break the live read.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol, never by importing this class.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.metric_series import SERIES_FNG_VALUE, MetricPoint
from market_analyser.data.sources import MarketSentimentSource
from market_analyser.data.types import MarketSentimentSample
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_FNG_URL = "https://api.alternative.me/fng/"
_SOURCE = "alternative.me-fng"

# 5-minute TTL: the index publishes daily, so this is generous (ADR-0019).
_DEFAULT_TTL_SECONDS = 300.0

_logger = logging.getLogger(__name__)


class CryptoFearGreedError(ValueError):
    """The upstream payload was missing its `data` array, the leading entry, or
    (Plan 0055) a per-entry field the series parse requires — raised at the
    adapter boundary before model construction. A shape drift in the upstream
    API surfaces as this typed error, never as a silently-skipped point."""


class CryptoFearGreedAdapter(MarketSentimentSource):
    """Fetches the current crypto Fear & Greed reading from Alternative.me."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        metric_store: MetricPointsRepository | None = None,
    ) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="crypto-fng",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
            )
        )
        self._metric_store = metric_store

    def fetch_current(self) -> MarketSentimentSample:
        """Return the current reading. Raises `ResilientHttpError` on upstream
        exhaustion, `CryptoFearGreedError` on a shape-broken payload, and
        `pydantic.ValidationError` on an out-of-range value or unknown label.

        When a metric store is wired, the reading is also write-through
        appended to the `fng.value` series, keyed by the upstream publish
        timestamp — at most one point per published reading, and best-effort:
        a storage failure is logged, never raised (the live read must not
        depend on persistence)."""
        response = self._http.get(_FNG_URL, params={"limit": 1}, expect_json=True)
        sample = self._parse(response.json())
        if self._metric_store is not None:
            point = MetricPoint(
                series_id=SERIES_FNG_VALUE,
                ts=int(sample.published_at.timestamp()),
                value=float(sample.value),
            )
            try:
                self._metric_store.upsert_points([point])
            except Exception:
                _logger.warning(
                    "fng.value write-through failed; returning the live reading anyway",
                    exc_info=True,
                )
        return sample

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[MetricPoint]:
        """`MetricSeriesSource` (ADR-0051): the full daily F&G history in one
        keyless call (`?limit=0`, back to 2018-02-01), optionally clipped to the
        inclusive `[start, end]` epoch-second window, sorted by `ts` ascending.

        Raises `CryptoFearGreedError` on a shape-broken payload (a missing or
        non-numeric per-entry field is upstream drift, surfaced typed — never a
        silently-dropped point) and `ValueError` for a series id this source
        does not produce."""
        if series_id != SERIES_FNG_VALUE:
            raise ValueError(
                f"CryptoFearGreedAdapter produces only {SERIES_FNG_VALUE!r}, not {series_id!r}",
            )
        response = self._http.get(_FNG_URL, params={"limit": 0}, expect_json=True)
        points = self._parse_series(response.json())
        if start is not None:
            points = [p for p in points if p.ts >= start]
        if end is not None:
            points = [p for p in points if p.ts <= end]
        return points

    def backfill_series(self) -> int:
        """Fetch the full history and upsert it into the wired metric store,
        returning how many points were newly inserted. Idempotent: a re-run
        re-upserts the same `(series_id, ts, value)` rows, which the repository
        skips, so the row count is unchanged. A historical value that *changed*
        upstream raises `MetricPointConflictError` (ADR-0051 immutability) —
        a source-quality problem to surface, not absorb."""
        if self._metric_store is None:
            raise ValueError("backfill_series requires a wired metric store")
        points = self.fetch_series(SERIES_FNG_VALUE)
        return self._metric_store.upsert_points(points)

    def _parse(self, payload: Any) -> MarketSentimentSample:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            raise CryptoFearGreedError("alternative.me-fng: payload missing 'data' entries")
        entry = data[0]
        return MarketSentimentSample(
            market="crypto",
            value=int(entry["value"]),
            classification=entry["value_classification"],
            published_at=datetime.fromtimestamp(int(entry["timestamp"]), tz=UTC),
            source=_SOURCE,
            window="current",
        )

    def _parse_series(self, payload: Any) -> list[MetricPoint]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            raise CryptoFearGreedError("alternative.me-fng: payload missing 'data' entries")
        points: list[MetricPoint] = []
        for entry in data:
            if not isinstance(entry, dict):
                raise CryptoFearGreedError("alternative.me-fng: non-object 'data' entry")
            try:
                ts = int(entry["timestamp"])
                value = float(entry["value"])
            except (KeyError, TypeError, ValueError) as err:
                raise CryptoFearGreedError(
                    f"alternative.me-fng: series entry missing/non-numeric field ({err})",
                ) from err
            if not 0.0 <= value <= 100.0:
                raise CryptoFearGreedError(
                    f"alternative.me-fng: series value {value!r} outside [0, 100]",
                )
            points.append(MetricPoint(series_id=SERIES_FNG_VALUE, ts=ts, value=value))
        # Upstream returns newest-first; the series contract is ts-ascending.
        points.sort(key=lambda p: p.ts)
        return points


__all__ = ["CryptoFearGreedAdapter", "CryptoFearGreedError"]

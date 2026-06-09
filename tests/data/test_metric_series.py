"""Plan 0055 phase 1 — the metric-series registry and `MetricPoint` boundary model.

The registry is the schema for the generic `metric_points` table (ADR-0051):
lookups of unregistered ids must fail loudly, enumeration must be
deterministic, and the boundary model must refuse non-finite values.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from market_analyser.data import metric_series
from market_analyser.data.metric_series import (
    MetricPoint,
    MetricSeriesSpec,
    UnknownMetricSeriesError,
    get_series_spec,
    is_registered,
    registered_series,
)
from market_analyser.data.sources import MetricSeriesSource


def _spec(series_id: str) -> MetricSeriesSpec:
    return MetricSeriesSpec(
        series_id=series_id,
        description="test-only series",
        source="test",
        cadence="test",
    )


def test_get_series_spec_returns_registered_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(metric_series.SERIES_REGISTRY, "test.metric", _spec("test.metric"))

    spec = get_series_spec("test.metric")

    assert spec.series_id == "test.metric"
    assert is_registered("test.metric")


def test_get_series_spec_raises_on_unregistered_id() -> None:
    with pytest.raises(UnknownMetricSeriesError, match=r"totally\.unknown"):
        get_series_spec("totally.unknown")
    assert not is_registered("totally.unknown")


def test_registered_series_enumeration_is_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(metric_series.SERIES_REGISTRY, "zzz.last", _spec("zzz.last"))
    monkeypatch.setitem(metric_series.SERIES_REGISTRY, "aaa.first", _spec("aaa.first"))

    series = registered_series()

    assert series == tuple(sorted(series))
    assert "aaa.first" in series
    assert "zzz.last" in series
    assert series.index("aaa.first") < series.index("zzz.last")


def test_metric_point_rejects_non_finite_value() -> None:
    with pytest.raises(ValidationError):
        MetricPoint(series_id="test.metric", ts=0, value=float("nan"))
    with pytest.raises(ValidationError):
        MetricPoint(series_id="test.metric", ts=0, value=float("inf"))


def test_metric_point_rejects_negative_ts_and_empty_series_id() -> None:
    with pytest.raises(ValidationError):
        MetricPoint(series_id="test.metric", ts=-1, value=1.0)
    with pytest.raises(ValidationError):
        MetricPoint(series_id="", ts=0, value=1.0)


def test_metric_series_source_protocol_is_runtime_checkable() -> None:
    """A structural conformer satisfies the Protocol; an unrelated object does not."""

    class Conforming:
        def fetch_series(
            self,
            series_id: str,
            start: int | None = None,
            end: int | None = None,
        ) -> list[MetricPoint]:
            return []

    class NotConforming:
        pass

    assert isinstance(Conforming(), MetricSeriesSource)
    assert not isinstance(NotConforming(), MetricSeriesSource)

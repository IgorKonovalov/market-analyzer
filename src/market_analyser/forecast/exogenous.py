"""Exogenous metric-series join for the forecast pipeline (Plan 0059 phase 1).

ADR-0054 rule 1: an exogenous feature value for bar ``i`` may read only metric
points with ``ts`` **strictly before the bar's open time**. The join is made
through the ADR-0051 ``as_of`` primitive — the only read the forecast pipeline
is allowed to make against the metric store — bounded at ``open_epoch - 1``
(``as_of`` is inclusive, so the minus-one second turns it into the strict
``ts < T_open`` the ADR demands). For daily series on daily bars this degrades
to "yesterday's value" by construction: even a point timestamped exactly at the
bar's open is invisible to that bar and becomes visible one bar later, so
publication-lag lookahead is structurally impossible regardless of how a source
timestamps its points.

A missing value (series not yet warm at that bound) is **NaN**, never zero — a
zero would be a fabricated observation (ADR-0054 alternative C, rejected). Row
dropping happens downstream in the feature pipeline (phase 2); this module only
reports honestly what was observable.

Determinism: series are joined in the caller-supplied order, bars in series
order; the output carries plain tuples/dicts built by iteration over those, no
set iteration and no wall-clock reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from market_analyser.data.metric_series import MetricPoint
from market_analyser.data.types import Bar


class MetricAsOfLookup(Protocol):
    """The one join primitive this module consumes (ADR-0051): the latest point
    with ``point.ts <= ts``, or ``None``. Satisfied structurally by
    `persistence.repositories.metric_points.MetricPointsRepository` — the
    Protocol keeps `forecast/` free of a persistence import."""

    def as_of(self, series_id: str, ts: int) -> MetricPoint | None: ...


@dataclass(frozen=True)
class ExogenousColumns:
    """Per-series, per-bar joined values, aligned to the source bar list.

    ``columns[series_id][i]`` is the lag-1 as-of value for bar ``i`` or NaN when
    no point exists strictly before that bar's open. ``last_point_ts`` records,
    per series, the timestamp of the freshest point actually consumed (the last
    bar's join) — the provenance input for ``series_inputs`` (phase 3)."""

    series_ids: tuple[str, ...]
    columns: dict[str, tuple[float, ...]]
    last_point_ts: dict[str, int | None]

    def n_bars(self) -> int:
        """Length of every column (0 when built over no bars)."""

        if not self.series_ids:
            return 0
        return len(self.columns[self.series_ids[0]])


def lag1_bound(bar: Bar) -> int:
    """The as-of bound for ``bar``: its open epoch second minus one, so the
    inclusive ``as_of`` lookup admits only points with ``ts < T_open``."""

    return int(bar.event_ts.timestamp()) - 1


def build_exogenous_columns(
    bars: Sequence[Bar],
    series_ids: Sequence[str],
    lookup: MetricAsOfLookup,
) -> ExogenousColumns:
    """Join every series in ``series_ids`` onto ``bars`` lag-1 as-of.

    Column entry ``i`` reads only points strictly before bar ``i``'s open time;
    a bar with no observable point gets ``float("nan")``. The lookup validates
    series registration itself (the registry is the schema, ADR-0051), so an
    unregistered id fails loudly on the first call.
    """

    ids = tuple(series_ids)
    columns: dict[str, tuple[float, ...]] = {}
    last_point_ts: dict[str, int | None] = {}
    for series_id in ids:
        values: list[float] = []
        last_ts: int | None = None
        for bar in bars:
            point = lookup.as_of(series_id, lag1_bound(bar))
            if point is None:
                values.append(float("nan"))
            else:
                values.append(point.value)
                last_ts = point.ts
        columns[series_id] = tuple(values)
        last_point_ts[series_id] = last_ts
    return ExogenousColumns(series_ids=ids, columns=columns, last_point_ts=last_point_ts)


__all__ = [
    "ExogenousColumns",
    "MetricAsOfLookup",
    "build_exogenous_columns",
    "lag1_bound",
]

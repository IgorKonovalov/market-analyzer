"""Phase-2 done-when for Plan 0059: feature-set v2 + row policy + versioning.

The three tests the plan names:

* **Versioning** — the Plan 0036 change-matrix extends to the feature set: v1's
  hash is pinned to its literal value (unchanged by this plan), v2 hashes to a
  different id, and hence every v2 model gets a new ``model_version`` (ADR-0040).
* **Row policy** — a bar with a missing exogenous value is absent from the
  training matrix (``None`` row, never zero-filled) and present again once the
  series warms (points covering its past exist on a re-build).
* **Determinism golden** — the byte-identical-probabilities pattern from
  ``test_determinism.py`` passes for a v2 model. (The plan says "on the BTC
  fixture"; no offline BTC bars fixture exists in this repo, so the same
  deterministic synthetic series stands in — phase 4 runs real cached BTC-USD.)

Plus: spot truncation-invariance for v2 rows (cycle + exogenous features are
trailing, so the v1 guarantee must carry over) and the frozen v2 name order.
"""

from __future__ import annotations

import struct

from market_analyser.data.metric_series import MetricPoint
from market_analyser.forecast.exogenous import (
    ExogenousColumns,
    build_exogenous_columns,
)
from market_analyser.forecast.features import (
    EXOGENOUS_SERIES_IDS_V2,
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
    build_feature_rows_v2,
)
from market_analyser.forecast.labels import Direction, LabelParams, build_labels
from market_analyser.forecast.model import ModelParams, align_samples, predict_proba, train
from tests.forecast._synthetic import synthetic_bars

# v1's id, pinned as a literal: this plan must not move it (v1 stays
# reproducible under its own identity — ADR-0054 / plan phase 2 done-when).
PINNED_FEATURE_SET_ID_V1 = "49c020d0794fd2a7"

EXPECTED_V2_EXTRAS = (
    "halving_phase",
    "days_since_halving",
    "mayer_multiple",
    "dist_200w_ma",
    "fng_value",
    "fng_delta_7",
    "btc_dominance",
    "dominance_delta_7",
    "funding_rate",
    "oi_delta_7",
    "mvrv",
)

# v2 rows need 1400 daily closes for dist_200w_ma, so the fixture is long; the
# defined tail (~100 rows) keeps the model trainable while the build stays fast.
N_BARS = 1500
BARS = synthetic_bars(N_BARS)


class _StaticLookup:
    """Every series has one point at ``ts`` with a distinct value — enough to
    warm every column from bar 1 (or bar 0 when ts precedes the first open)."""

    def __init__(self, ts: int, values: dict[str, float]) -> None:
        self._ts = ts
        self._values = values

    def as_of(self, series_id: str, ts: int) -> MetricPoint | None:
        if series_id not in self._values or ts < self._ts:
            return None
        return MetricPoint(series_id=series_id, ts=self._ts, value=self._values[series_id])


def _warm_columns(bars: list = BARS) -> ExogenousColumns:  # type: ignore[type-arg]
    first_open = int(bars[0].event_ts.timestamp())
    values = {sid: 10.0 + 1.0 * i for i, sid in enumerate(EXOGENOUS_SERIES_IDS_V2)}
    lookup = _StaticLookup(first_open - 60, values)
    return build_exogenous_columns(bars, EXOGENOUS_SERIES_IDS_V2, lookup)


def _proba_bytes(dists: list[dict[Direction, float]]) -> bytes:
    out = bytearray()
    for dist in dists:
        for key in sorted(dist, key=lambda d: d.value):
            out += struct.pack("<d", dist[key])
    return bytes(out)


# --------------------------------------------------------------------------- #
# (a) Versioning: the change matrix extends to v1 -> v2.                       #
# --------------------------------------------------------------------------- #


def test_v2_names_are_frozen_and_extend_v1() -> None:
    expected = (*FEATURE_NAMES, *EXPECTED_V2_EXTRAS)
    assert expected == FEATURE_NAMES_V2
    assert len(FEATURE_NAMES_V2) == 27


def test_v1_hash_is_unchanged_and_v2_hash_differs() -> None:
    """v1's id equals its pinned literal (this plan did not move it) and v2's id
    is a different value — so ADR-0040's model_version necessarily changes for
    every model trained on the v2 matrix."""

    assert FEATURE_SET_ID == PINNED_FEATURE_SET_ID_V1
    assert FEATURE_SET_ID_V2 != FEATURE_SET_ID

    from market_analyser.forecast.features import _compute_feature_set_id

    assert _compute_feature_set_id(FEATURE_NAMES_V2) == FEATURE_SET_ID_V2


def test_v2_model_records_v2_feature_set_id() -> None:
    rows = build_feature_rows_v2(BARS, _warm_columns())
    labels = build_labels(BARS, LabelParams(horizon_bars=1, flat_band=0.001))
    train_rows, train_labels = align_samples(rows, labels)
    model = train(
        train_rows, train_labels, ModelParams(seed=1729), feature_set_id=FEATURE_SET_ID_V2
    )
    assert model.feature_set_id == FEATURE_SET_ID_V2


# --------------------------------------------------------------------------- #
# (b) Row policy: missing exogenous -> dropped row; warm series -> present.    #
# --------------------------------------------------------------------------- #


def test_missing_exogenous_value_drops_row_then_warming_restores_it() -> None:
    """With one series cold the matrix is empty (every row carries a NaN
    exogenous value); after the series warms — points now cover the bars'
    past — the same bar indices are present again. Never zero-filled."""

    first_open = int(BARS[0].event_ts.timestamp())
    values = {sid: 10.0 + 1.0 * i for i, sid in enumerate(EXOGENOUS_SERIES_IDS_V2)}

    cold_values = dict(values)
    cold_series = EXOGENOUS_SERIES_IDS_V2[-1]
    del cold_values[cold_series]  # one series has no points at all
    cold_columns = build_exogenous_columns(
        BARS, EXOGENOUS_SERIES_IDS_V2, _StaticLookup(first_open - 60, cold_values)
    )
    cold_rows = build_feature_rows_v2(BARS, cold_columns)
    assert all(row is None for row in cold_rows)

    warm_rows = build_feature_rows_v2(BARS, _warm_columns())
    defined = [i for i, row in enumerate(warm_rows) if row is not None]
    assert defined, "expected defined v2 rows once every series is warm"
    # The restored rows carry the real joined values, not a fabricated zero.
    fng_index = FEATURE_NAMES_V2.index("fng_value")
    first_defined = warm_rows[defined[0]]
    assert first_defined is not None
    assert first_defined.values[fng_index] == 10.0


def test_partial_warmup_drops_exactly_the_cold_prefix_rows() -> None:
    """A series whose first point lands mid-series defines rows only after both
    the value and its DELTA_LOOKBACK-ago endpoint are observable — the row-drop
    boundary moves with the join, not with any fill rule."""

    first_open = int(BARS[0].event_ts.timestamp())
    values = {sid: 10.0 + 1.0 * i for i, sid in enumerate(EXOGENOUS_SERIES_IDS_V2)}
    warm_rows = build_feature_rows_v2(BARS, _warm_columns())
    first_warm = next(i for i, row in enumerate(warm_rows) if row is not None)

    # Re-warm one series only from bar 1420's open onward (inside the defined tail).
    late_ts = int(BARS[1420].event_ts.timestamp()) + 60
    late_series = EXOGENOUS_SERIES_IDS_V2[0]

    class _MixedLookup:
        def as_of(self, series_id: str, ts: int) -> MetricPoint | None:
            start = late_ts if series_id == late_series else first_open - 60
            if series_id not in values or ts < start:
                return None
            return MetricPoint(series_id=series_id, ts=start, value=values[series_id])

    mixed_rows = build_feature_rows_v2(
        BARS, build_exogenous_columns(BARS, EXOGENOUS_SERIES_IDS_V2, _MixedLookup())
    )
    first_mixed = next(i for i, row in enumerate(mixed_rows) if row is not None)
    # Visible from bar 1421 (lag-1), delta needs +DELTA_LOOKBACK more bars.
    assert first_mixed == 1421 + 7
    assert first_mixed > first_warm
    for i in range(first_mixed):
        assert mixed_rows[i] is None


# --------------------------------------------------------------------------- #
# (c) Determinism golden for a v2 model.                                       #
# --------------------------------------------------------------------------- #


def test_v2_predicted_probabilities_are_byte_identical_across_retrains() -> None:
    rows = build_feature_rows_v2(BARS, _warm_columns())
    labels = build_labels(BARS, LabelParams(horizon_bars=1, flat_band=0.001))
    kept_rows, kept_labels = align_samples(rows, labels)
    assert kept_rows, "expected trainable v2 samples"

    model_a = train(
        kept_rows, kept_labels, ModelParams(seed=1729), feature_set_id=FEATURE_SET_ID_V2
    )
    model_b = train(
        kept_rows, kept_labels, ModelParams(seed=1729), feature_set_id=FEATURE_SET_ID_V2
    )

    assert _proba_bytes(predict_proba(model_a, kept_rows)) == _proba_bytes(
        predict_proba(model_b, kept_rows)
    )


# --------------------------------------------------------------------------- #
# Supporting guards.                                                           #
# --------------------------------------------------------------------------- #


def test_v2_rows_are_truncation_invariant_at_spot_indices() -> None:
    """The v1 per-row guarantee carries to v2: rebuilding on ``bars[0..=i]``
    (with columns truncated to match) reproduces row ``i`` byte-identically.
    Spot-checked (the full-series sweep would be O(n^2) at 1500 bars) — the
    exogenous half is already covered per-point by the phase-1 perturbation
    test, and the cycle features read only trailing closes."""

    columns = _warm_columns()
    full = build_feature_rows_v2(BARS, columns)
    defined = [i for i, row in enumerate(full) if row is not None]
    for i in (defined[0], defined[len(defined) // 2], defined[-1]):
        truncated_columns = ExogenousColumns(
            series_ids=columns.series_ids,
            columns={s: columns.columns[s][: i + 1] for s in columns.series_ids},
            last_point_ts=columns.last_point_ts,
        )
        truncated = build_feature_rows_v2(BARS[: i + 1], truncated_columns)
        full_row = full[i]
        trunc_row = truncated[i]
        assert full_row is not None and trunc_row is not None
        assert trunc_row.values == full_row.values


def test_v2_row_width_matches_the_frozen_name_order() -> None:
    rows = build_feature_rows_v2(BARS, _warm_columns())
    for row in rows:
        if row is not None:
            assert len(row.values) == len(FEATURE_NAMES_V2)


def test_v1_builder_is_untouched_by_v2() -> None:
    """v1 rows carry exactly the v1 width — the two sets stay independently
    reproducible (the plan's "v1 remains importable and reproducible")."""

    from market_analyser.forecast.features import build_feature_rows

    v1_rows = build_feature_rows(BARS[:200])
    assert any(row is not None for row in v1_rows)
    for row in v1_rows:
        if row is not None:
            assert len(row.values) == len(FEATURE_NAMES)

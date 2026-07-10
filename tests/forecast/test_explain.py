"""Plan 0063 phase 1 done-when (explanation core): out-of-sample permutation
importances are deterministic, sane, and honest.

Covered:
- **Sanity anchor**: on a synthetic fixture where one feature fully determines
  the label, that feature ranks first with importance strictly above every
  other feature's.
- **Determinism**: two identical `explain_horizon` calls produce byte-identical
  `ForecastExplanation` dumps (seeded permutation, seeded training,
  explicit-order aggregation).
- Ranking order is mean-descending with the feature name as the deterministic
  tie-break, and a single scored fold reports zero spread while multiple folds
  report a real one.
- **Honest emptiness**: no scored folds → no importances, `note` states it,
  nothing is fabricated.
- The wire summary is exactly the top `TOP_N_DRIVERS` of the (already ordered)
  ranking, in order, with the artifact path passed through (`None` when no
  `runs_dir` is wired).
- The artifact model round-trips through its own JSON dump.

The tool-layer half (importances computed ONLY on OOS fold slices, the wire
pin, the artifact writer) lives in `tests/api/test_forecast_tool.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from market_analyser.forecast.explain import (
    DISCLAIMER_CODE,
    NOTE_NO_SCORED_FOLDS,
    NOTE_NO_SCORED_FOLDS_CODE,
    TOP_N_DRIVERS,
    ForecastExplanation,
    ForecastExplanationArtifact,
    HorizonExplanationRecord,
    build_forecast_explanation_artifact,
    explain_horizon,
    summarize_explanation,
)
from market_analyser.forecast.features import FeatureRow
from market_analyser.forecast.labels import Direction
from market_analyser.forecast.model import ModelParams, train
from market_analyser.forecast.result import (
    ExplanationDriver,
    ExplanationSummary,
    ForecastProvenance,
    HorizonForecast,
    MultiHorizonForecastResult,
)
from market_analyser.forecast.validation import ForecastValidation, ScoredFold

FEATURE_NAMES_3 = ("decider", "noise_a", "noise_b")
SEED = 1729


def _uniforms(seed: int) -> _Lcg:
    return _Lcg(seed)


class _Lcg:
    """A tiny deterministic uniform-[0,1) generator — no `random` module, so the
    fixture is reproducible byte-for-byte across runs and platforms."""

    def __init__(self, seed: int) -> None:
        self._state = seed

    def next(self) -> float:
        self._state = (self._state * 1103515245 + 12345) % (2**31)
        return self._state / (2**31)


def _synthetic_rows(n: int, seed: int = 7) -> tuple[list[FeatureRow], list[Direction]]:
    """``n`` samples over three features where `decider` alone determines the
    label (UP iff decider > 0.5) and the other two are pure noise."""

    gen = _uniforms(seed)
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    rows: list[FeatureRow] = []
    labels: list[Direction] = []
    for i in range(n):
        decider = gen.next()
        rows.append(
            FeatureRow(
                bar_index=i,
                event_ts=t0 + timedelta(days=i),
                values=(decider, gen.next(), gen.next()),
            )
        )
        labels.append(Direction.UP if decider > 0.5 else Direction.DOWN)
    return rows, labels


def _scored_folds(rows: list[FeatureRow], labels: list[Direction]) -> list[ScoredFold]:
    """Two expanding-window folds over the fixture: train [0:120) test
    [120:180), train [0:180) test [180:240) — the walk-forward capture shape."""

    params = ModelParams(seed=SEED)
    folds: list[ScoredFold] = []
    for fold_index, (train_end, test_end) in enumerate(((120, 180), (180, 240)), start=1):
        model = train(rows[:train_end], labels[:train_end], params)
        folds.append(
            ScoredFold(
                fold_index=fold_index,
                model=model,
                test_rows=rows[train_end:test_end],
                test_labels=labels[train_end:test_end],
            )
        )
    return folds


def _explain(folds: list[ScoredFold], predict_row: FeatureRow | None) -> ForecastExplanation:
    return explain_horizon(
        horizon_bars=1,
        feature_set_id="test-set",
        feature_names=FEATURE_NAMES_3,
        scored_folds=folds,
        predict_row=predict_row,
        seed=SEED,
    )


def test_fully_determining_feature_ranks_first() -> None:
    """The sanity anchor: a feature that alone determines the label must carry
    the top importance, strictly above every other feature's."""

    rows, labels = _synthetic_rows(240)
    explanation = _explain(_scored_folds(rows, labels), rows[-1])

    assert explanation.n_folds_used == 2
    assert explanation.note is None
    top, *rest = explanation.importances
    assert top.feature == "decider"
    for other in rest:
        assert top.mean > other.mean

    # The ranking is ordered: mean descending, feature name as the tie-break.
    ranked = list(explanation.importances)
    assert ranked == sorted(ranked, key=lambda fi: (-fi.mean, fi.feature))

    # The predict-row map carries the actual as-of feature values, in the
    # frozen name spelling.
    assert explanation.predict_row is not None
    assert tuple(explanation.predict_row) == FEATURE_NAMES_3
    assert explanation.predict_row["decider"] == rows[-1].values[0]


def test_explanation_is_deterministic_across_reruns() -> None:
    """Byte-identical dumps from identical inputs — the seeded-permutation
    claim (ADR-0040 discipline extended to the explanation)."""

    rows_a, labels_a = _synthetic_rows(240)
    rows_b, labels_b = _synthetic_rows(240)
    first = _explain(_scored_folds(rows_a, labels_a), rows_a[-1])
    second = _explain(_scored_folds(rows_b, labels_b), rows_b[-1])

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_spread_is_zero_for_single_fold_and_reported_across_folds() -> None:
    rows, labels = _synthetic_rows(240)
    folds = _scored_folds(rows, labels)

    single = _explain(folds[:1], rows[-1])
    assert single.n_folds_used == 1
    assert all(fi.spread == 0.0 for fi in single.importances)

    double = _explain(folds, rows[-1])
    assert double.n_folds_used == 2
    assert all(fi.spread >= 0.0 for fi in double.importances)


def test_no_scored_folds_yields_honest_empty_explanation() -> None:
    """A no-model horizon carries no importances and says so — never a
    fabricated ranking (the plan's honesty clause)."""

    rows, _ = _synthetic_rows(30)
    explanation = _explain([], rows[-1])

    assert explanation.importances == ()
    assert explanation.n_folds_used == 0
    assert explanation.note == NOTE_NO_SCORED_FOLDS
    # The translatable mirror of `note` is set exactly when `note` is (Plan 0069).
    assert explanation.note_code == NOTE_NO_SCORED_FOLDS_CODE
    assert explanation.disclaimer_code == DISCLAIMER_CODE
    assert explanation.predict_row is not None  # the as-of values still travel

    no_row = _explain([], None)
    assert no_row.predict_row is None


def test_summary_is_top_n_in_ranking_order_with_artifact_passthrough() -> None:
    rows, labels = _synthetic_rows(240)
    explanation = _explain(_scored_folds(rows, labels), rows[-1])

    summary = summarize_explanation(explanation, artifact="forecast/x/explanation.json")
    expected = tuple(
        ExplanationDriver(feature=fi.feature, importance=fi.mean)
        for fi in explanation.importances[:TOP_N_DRIVERS]
    )
    assert summary.top_drivers == expected
    assert len(summary.top_drivers) == min(TOP_N_DRIVERS, len(explanation.importances))
    assert summary.artifact == "forecast/x/explanation.json"

    unwired = summarize_explanation(explanation, artifact=None)
    assert unwired.artifact is None
    assert unwired.top_drivers == expected  # drivers ride the wire regardless

    # Plan 0069: the summary carries the explanation's translatable codes. A
    # scored-folds horizon has the disclaimer code and no note code.
    assert summary.disclaimer_code == explanation.disclaimer_code == DISCLAIMER_CODE
    assert summary.note_code == explanation.note_code is None


def test_artifact_round_trips_through_its_json_dump() -> None:
    rows, labels = _synthetic_rows(240)
    explanation = _explain(_scored_folds(rows, labels), rows[-1])
    summary = summarize_explanation(explanation, artifact="forecast/x/explanation.json")

    validation = ForecastValidation(
        horizon_bars=1,
        n_splits=2,
        n_scored=120,
        skill=0.9,
        baseline_skill=0.5,
        persistence_skill=0.5,
        majority_skill=0.5,
        beats_baseline=True,
        folds=[],
    )
    provenance = ForecastProvenance(
        model_version="v" * 16,
        feature_set_id="test-set",
        training_cutoff=rows[-1].event_ts,
        seed=SEED,
        lib_versions={"scikit-learn": "x"},
        explanation=summary,
    )
    result = MultiHorizonForecastResult(
        symbol="SYN",
        timeframe="1d",
        as_of_bar_ts=rows[-1].event_ts,
        feature_set_id="test-set",
        horizons=[
            HorizonForecast(
                horizon_bars=1,
                prob_up=0.6,
                prob_down=0.3,
                prob_flat=0.1,
                validation=validation,
                edge_margin=0.4,
                edge_strength="clear",
                provenance=provenance,
            )
        ],
    )
    artifact = build_forecast_explanation_artifact(
        result, [explanation], started_at=datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    )

    assert artifact.horizons == (
        HorizonExplanationRecord(
            horizon_bars=1,
            explanation=explanation,
            validation=validation,
            provenance=provenance,
        ),
    )
    assert artifact.local_attribution is None  # the reserved ADR-0058 slot

    round_tripped = ForecastExplanationArtifact.model_validate_json(artifact.model_dump_json())
    assert round_tripped == artifact
    assert round_tripped.model_dump_json() == artifact.model_dump_json()


def test_explanation_summary_wire_shape() -> None:
    """`exclude_none` semantics the renderer Zod relies on: `artifact`/`note_code`
    absent when None, `top_drivers`/`disclaimer_code` always present."""

    summary = ExplanationSummary(top_drivers=())
    wire = summary.model_dump(mode="json", exclude_none=True)
    assert wire == {"top_drivers": [], "disclaimer_code": "disclaimer.importance"}


def test_explanation_summary_disclaimer_code_default_mirrors_explain() -> None:
    """The wire summary's literal `disclaimer_code` default must not drift from
    the explain-module constant it mirrors (Plan 0069 phase 4)."""

    assert ExplanationSummary(top_drivers=()).disclaimer_code == DISCLAIMER_CODE
    assert ExplanationSummary(top_drivers=()).note_code is None

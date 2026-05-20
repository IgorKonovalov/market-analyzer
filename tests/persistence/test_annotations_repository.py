"""Plan 0006 phase 2: AnnotationsRepository + Annotation model round-trip and validation tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from market_analyser.annotations.types import Annotation, AnnotationKind
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)


def _annotation(
    *,
    symbol: str = "AAPL",
    timeframe: str = "1d",
    day: int = 15,
    millis: int = 0,
    kind: AnnotationKind = AnnotationKind.BULLISH_MARKER,
    label: str | None = "hammer at support",
    agent_id: str = "claude-desktop-test",
) -> Annotation:
    return Annotation(
        symbol=symbol,
        timeframe=timeframe,
        event_ts=datetime(2026, 4, day, 0, 0, 0, millis * 1000, tzinfo=UTC),
        kind=kind,
        label=label,
        agent_id=agent_id,
    )


@pytest.fixture
def repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


def test_insert_then_list_for_round_trips_all_fields(repo: AnnotationsRepository) -> None:
    """Round-trip preserves every field, including event_ts UTC + ms precision and agent_id."""
    original = _annotation(day=15, millis=234, label="hammer at support")
    repo.insert(original)

    out = repo.list_for(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert len(out) == 1
    got = out[0]
    assert got.id == original.id
    assert got.symbol == "AAPL"
    assert got.timeframe == "1d"
    assert got.event_ts == original.event_ts
    assert got.event_ts.microsecond == 234_000  # ms precision intact
    assert got.event_ts.tzinfo is not None
    assert got.kind == AnnotationKind.BULLISH_MARKER
    assert got.label == "hammer at support"
    assert got.agent_id == "claude-desktop-test"
    assert got.created_at == original.created_at


def test_list_for_filters_by_symbol_and_timeframe(repo: AnnotationsRepository) -> None:
    repo.insert(_annotation(symbol="AAPL", timeframe="1d"))
    repo.insert(_annotation(symbol="MSFT", timeframe="1d"))
    repo.insert(_annotation(symbol="AAPL", timeframe="1h"))

    out = repo.list_for(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert [(a.symbol, a.timeframe) for a in out] == [("AAPL", "1d")]


def test_list_for_window_boundaries_are_inclusive(repo: AnnotationsRepository) -> None:
    """`event_ts == start` and `event_ts == end` are both inside the window."""
    repo.insert(_annotation(day=10))  # lower boundary
    repo.insert(_annotation(day=15))  # interior
    repo.insert(_annotation(day=20))  # upper boundary
    repo.insert(_annotation(day=9))  # outside below
    repo.insert(_annotation(day=21))  # outside above

    out = repo.list_for(
        "AAPL",
        "1d",
        datetime(2026, 4, 10, tzinfo=UTC),
        datetime(2026, 4, 20, tzinfo=UTC),
    )
    assert sorted(a.event_ts.day for a in out) == [10, 15, 20]


def test_insert_kind_invalid_raises_validation_error() -> None:
    """Pydantic enum gates `kind` at the model boundary (CLAUDE.md non-negotiables)."""
    with pytest.raises(ValidationError):
        Annotation(
            symbol="AAPL",
            timeframe="1d",
            event_ts=datetime(2026, 4, 15, tzinfo=UTC),
            kind="invalid_kind",  # type: ignore[arg-type]
        )


def test_two_inserts_with_identical_canonical_fields_are_distinct(
    repo: AnnotationsRepository,
) -> None:
    """Schema does not silently dedupe on (symbol, timeframe, event_ts, kind, agent_id)."""
    a = _annotation()
    b = _annotation()
    assert a.id != b.id, "default_factory must generate distinct ids per construction"

    repo.insert(a)
    repo.insert(b)

    out = repo.list_for(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(out) == 2
    assert {x.id for x in out} == {a.id, b.id}


def test_symbol_normalized_to_uppercase_at_model_boundary() -> None:
    a = Annotation(
        symbol="aapl",
        timeframe="1d",
        event_ts=datetime(2026, 4, 15, tzinfo=UTC),
        kind=AnnotationKind.BULLISH_MARKER,
    )
    assert a.symbol == "AAPL"


def test_unsupported_timeframe_rejected_at_model_boundary() -> None:
    with pytest.raises(ValidationError):
        Annotation(
            symbol="AAPL",
            timeframe="5m",
            event_ts=datetime(2026, 4, 15, tzinfo=UTC),
            kind=AnnotationKind.BULLISH_MARKER,
        )


def test_naive_event_ts_rejected_at_model_boundary() -> None:
    with pytest.raises(ValidationError):
        Annotation(
            symbol="AAPL",
            timeframe="1d",
            event_ts=datetime(2026, 4, 15),
            kind=AnnotationKind.BULLISH_MARKER,
        )


def test_default_agent_id_when_omitted() -> None:
    a = Annotation(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2026, 4, 15, tzinfo=UTC),
        kind=AnnotationKind.BULLISH_MARKER,
    )
    assert a.agent_id == "unknown"


def test_list_for_rejects_inverted_window(repo: AnnotationsRepository) -> None:
    with pytest.raises(ValueError, match="start"):
        repo.list_for(
            "AAPL",
            "1d",
            datetime(2026, 5, 1, tzinfo=UTC),
            datetime(2026, 4, 1, tzinfo=UTC),
        )


def test_list_for_rejects_naive_window(repo: AnnotationsRepository) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.list_for(
            "AAPL",
            "1d",
            datetime(2026, 4, 1),
            datetime(2026, 5, 1),
        )


def test_list_for_returns_empty_when_no_matches(repo: AnnotationsRepository) -> None:
    assert (
        repo.list_for(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )
        == []
    )


def test_list_for_uppercases_query_symbol(repo: AnnotationsRepository) -> None:
    """`list_for("aapl", ...)` finds rows stored with `AAPL` (case-insensitive symbol)."""
    repo.insert(_annotation(symbol="AAPL"))
    out = repo.list_for(
        "aapl",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(out) == 1

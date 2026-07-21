"""Plan 0113 phase 1 — offline tests for the curated FOMC meeting-date seed.

`FomcSeedSource` is keyless and pure: given a pinned clock it emits the upcoming
decision-day dates from `_FOMC_DECISION_DAYS`, filtered to the future, sorted
ascending, each a conditions-only `MarketEvent`. These tests are refreshed alongside
the seed table (they pin the curated 2026 prefix); an exhausted seed degrades to an
empty fetch with a refresh note rather than a stale event.
"""

from __future__ import annotations

from datetime import UTC, datetime

from market_analyser.data.adapters.fomc_seed import FomcSeedSource
from market_analyser.data.types import MarketEvent

# The 2026 remaining decision days (statement anchored at 18:00 UTC) as of a
# mid-year clock — the curated prefix; refresh with `_FOMC_DECISION_DAYS`.
_2026_REMAINING = [
    datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
    datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
    datetime(2026, 10, 28, 18, 0, tzinfo=UTC),
    datetime(2026, 12, 9, 18, 0, tzinfo=UTC),
]


def _source(now: datetime) -> FomcSeedSource:
    return FomcSeedSource(clock=lambda: now)


def test_emits_upcoming_dates_sorted_ascending() -> None:
    now = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    fetch = _source(now).fetch_events()

    dates = [event.scheduled_at for event in fetch.events]
    # Prefix-pinned so appending a later year to the seed does not break the test.
    assert dates[: len(_2026_REMAINING)] == _2026_REMAINING
    assert dates == sorted(dates)
    assert all(dt >= now for dt in dates)
    assert fetch.notes == ()


def test_events_are_macro_conditions_with_provenance() -> None:
    now = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    events = _source(now).fetch_events().events

    assert events
    for event in events:
        assert isinstance(event, MarketEvent)
        assert event.category == "macro"
        assert event.source == "fomc_seed"
        assert event.title == "FOMC meeting (rate decision)"
        assert event.symbol is None
        assert event.magnitude is None
        assert event.note is not None  # honest coverage caveat present


def test_past_meetings_are_filtered_by_the_clock() -> None:
    # A clock just after the Jul 29 statement drops it; Sep 16 is the next upcoming.
    now = datetime(2026, 7, 29, 18, 1, tzinfo=UTC)
    dates = [event.scheduled_at for event in _source(now).fetch_events().events]

    assert datetime(2026, 7, 29, 18, 0, tzinfo=UTC) not in dates
    assert dates[0] == datetime(2026, 9, 16, 18, 0, tzinfo=UTC)


def test_exhausted_seed_degrades_to_empty_with_a_refresh_note() -> None:
    # Far past every seeded date: no fabricated event, an honest refresh note instead.
    now = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)
    fetch = _source(now).fetch_events()

    assert fetch.events == []
    assert len(fetch.notes) == 1
    assert "refresh" in fetch.notes[0].lower()


def test_conditions_only_no_action_keys_on_the_wire() -> None:
    now = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    event = _source(now).fetch_events().events[0]

    dumped = event.model_dump(mode="json")
    assert not {"action", "signal", "side", "direction", "recommendation", "call"} & set(dumped)

"""Curated FOMC meeting-date seed — Plan 0113 phase 1 (ADR-0107).

FOMC meeting dates are an official but API-less HTML schedule that changes ~yearly
(effectively curate-once). Rather than scrape the Fed's calendar page, this ships a
small dated table of the confirmed **decision-day** dates (the second day of each
two-day meeting, when the statement is released) and emits the *upcoming* ones as
`MarketEvent`s. Keyless and always available — the macro category's floor when no
FRED key is configured.

The seed carries **only the scheduled date**, never consensus/actual figures
(ADR-0107 Alternative B: dates answer "when", the numbers are a later, separately-
justified addition).

REFRESH CHORE (~yearly): when the Federal Reserve publishes a new year's meeting
schedule (federalreserve.gov/monetarypolicy/fomccalendars.htm), append its
decision-day dates to `_FOMC_DECISION_DAYS`. Past dates need no pruning — they are
filtered out at read time by the wall clock — but dropping fully-past years keeps
the table small. If the Fed *reschedules* a meeting, correct the entry: a stale
seed is the ADR-0107 curated-drift risk, surfaced via each event's `note`.

Conforms to `EventCalendarSource.fetch_events` (ADR-0031); package-internal per
ADR-0007 — reached through the `event_calendar` tool's registry, never imported
directly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from market_analyser.data.sources import EventCalendarSource
from market_analyser.data.types import CalendarFetch, MarketEvent

_SOURCE = "fomc_seed"

# The FOMC statement is released ~14:00 US Eastern on the decision day. Eastern is
# UTC-4 (EDT) for most meetings and UTC-5 (EST) for the Jan/Dec ones, so a single
# UTC hour cannot be exact for every meeting; 18:00 UTC (≈14:00 EDT / ≈13:00 EST)
# is a deliberate day-anchored nominal, and the note discloses the approximation.
_STATEMENT_HOUR_UTC = 18

_NOTE = (
    "FOMC rate-decision date from a curated seed (statement ~14:00 ET; time shown "
    "at a day-anchored 18:00 UTC nominal). Dates only — no consensus/actual figures. "
    "May lag a Fed reschedule until the seed is refreshed."
)

# Confirmed decision-day dates (year, month, day). Refresh per the chore above.
# 2026 FOMC calendar (federalreserve.gov): two-day meetings; the decision day is
# the second day of each pair.
_FOMC_DECISION_DAYS: tuple[tuple[int, int, int], ...] = (
    (2026, 1, 28),
    (2026, 3, 18),
    (2026, 4, 29),
    (2026, 6, 17),
    (2026, 7, 29),
    (2026, 9, 16),
    (2026, 10, 28),
    (2026, 12, 9),
)


def _utcnow() -> datetime:
    """Wall-clock seam; the calendar is wall-clock-sensitive (no as_of)."""
    return datetime.now(tz=UTC)


class FomcSeedSource(EventCalendarSource):
    """Emits the upcoming FOMC decision dates from the curated seed. Keyless and
    never fails — an exhausted seed (all dates in the past) yields an empty fetch
    with a refresh note rather than a stale or fabricated event."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock if clock is not None else _utcnow

    def fetch_events(
        self, *, symbol: str | None = None, window: str | None = None
    ) -> CalendarFetch:
        now = self._clock()
        events = [
            MarketEvent(
                category="macro",
                title="FOMC meeting (rate decision)",
                symbol=None,
                scheduled_at=scheduled_at,
                magnitude=None,
                source=_SOURCE,
                note=_NOTE,
            )
            for scheduled_at in self._upcoming(now)
        ]
        if not events:
            return CalendarFetch(
                events=[],
                notes=(
                    "FOMC seed is exhausted (no upcoming dates) — refresh it from the "
                    "Fed's published calendar",
                ),
            )
        return CalendarFetch(events=events)

    @staticmethod
    def _upcoming(now: datetime) -> list[datetime]:
        """The seed's decision datetimes at or after `now`, ascending. Filtering by
        the wall clock keeps past meetings out without pruning the table."""
        scheduled = [
            datetime(year, month, day, _STATEMENT_HOUR_UTC, 0, tzinfo=UTC)
            for (year, month, day) in _FOMC_DECISION_DAYS
        ]
        return sorted(dt for dt in scheduled if dt >= now)


__all__ = ["FomcSeedSource"]

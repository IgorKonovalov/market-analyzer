"""FRED economic-release-dates adapter — Plan 0113 phase 1 (ADR-0107, ADR-0019, ADR-0038).

The macro category's keyed provider: CPI and PCE *release dates* from the St. Louis
Fed's FRED API. FOMC dates come from a curated seed (`fomc_seed`); FRED answers the
other half of "what macro prints are coming" — the scheduled release calendar of the
Consumer Price Index and the Personal Income & Outlays report (which carries the PCE
price index).

**Keyed, but inert without the key.** Like the LunarCrush social source (ADR-0103),
an absent `fred_api_key` makes the adapter **inert**: no request is issued and the
result is an empty `CalendarFetch` with a "not configured" note — never an exception,
never fabricated dates. The same honest-empty degrade covers every failure mode on
the resilient path (ADR-0019): a rate-limit, transport exhaustion, or a shape-broken
payload for one release is skipped with a note; the other release still returns.

**Dates only, day-level.** FRED serves the release *date* (no intraday time) — the
event is anchored at 12:00 UTC of that date (noon keeps the calendar day stable in
every US timezone) and each event's `note` discloses the day-level granularity. The
release DATE, not the printed CPI/PCE numbers, is the Tier-5 need (ADR-0107 Alt B).

**Auth is the query, not a header — but never logged.** FRED requires `api_key` as a
query parameter (it exposes no header auth). The resilient client logs only the URL
*path* (`_log_failure`; query and headers are never logged, ADR-0038 rule 1), so the
key cannot reach a log even though it rides the query string; `file_type=json` is
pinned (FRED defaults to XML — ADR-0107 risk).

Conforms to `EventCalendarSource.fetch_events` (ADR-0031); package-internal per
ADR-0007 — reached through the `event_calendar` tool's registry, never imported
directly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.sources import EventCalendarSource
from market_analyser.data.types import CalendarFetch, MarketEvent
from market_analyser.persistence.secrets import SecretsStore

_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
_SOURCE = "fred"

# Release dates change rarely; a long TTL absorbs repeat calendar reads.
_DEFAULT_TTL_SECONDS = 3600.0

# The FRED releases the macro calendar surfaces. `release_id` is FRED's stable id
# (10 = Consumer Price Index; 54 = Personal Income and Outlays, which contains the
# PCE price index). `title` is the event label; `note` discloses the day-level
# granularity that FRED's date-only response imposes.
_RELEASES: tuple[dict[str, Any], ...] = (
    {
        "release_id": 10,
        "title": "CPI release",
        "note": "FRED scheduled release date (day-level; FRED provides no intraday time).",
    },
    {
        "release_id": 54,
        "title": "PCE release (Personal Income & Outlays)",
        "note": "FRED scheduled release date (day-level; FRED provides no intraday time).",
    },
)

_UNCONFIGURED_NOTE = (
    "FRED unconfigured (fred_api_key) — CPI/PCE release dates omitted until a key is "
    "set (the source is inert: zero requests)."
)


def _utcnow() -> datetime:
    """Wall-clock seam; the calendar is wall-clock-sensitive (no as_of)."""
    return datetime.now(tz=UTC)


class FredReleasesSource(EventCalendarSource):
    """Fetches upcoming CPI/PCE release dates from FRED. Inert (honest-empty, no
    request) until a `fred_api_key` secret is present; degrades per release on a
    failed/shape-broken read rather than failing the whole macro call."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore | None = None,
        http_client: ResilientHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._secrets = secrets_store
        self._clock = clock if clock is not None else _utcnow
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="fred",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
            )
        )

    def fetch_events(
        self, *, symbol: str | None = None, window: str | None = None
    ) -> CalendarFetch:
        key = self._secrets.get("fred_api_key") if self._secrets is not None else None
        if not key:
            return CalendarFetch(events=[], notes=(_UNCONFIGURED_NOTE,))
        today = self._clock().date()
        events: list[MarketEvent] = []
        notes: list[str] = []
        for release in _RELEASES:
            params: dict[str, str | int] = {
                "release_id": int(release["release_id"]),
                "api_key": key,
                "file_type": "json",  # FRED defaults to XML (ADR-0107 risk) — pin JSON.
                "include_release_dates_with_no_data": "true",  # future scheduled dates
                "sort_order": "asc",
            }
            try:
                response = self._http.get(_RELEASE_DATES_URL, params=params, expect_json=True)
            except ResilientHttpError:
                notes.append(f"FRED {release['title']} unavailable (upstream error).")
                continue
            events.extend(_parse_release(response.json(), release, today))
        return CalendarFetch(events=events, notes=notes)


def _parse_release(payload: Any, release: dict[str, Any], today: date) -> list[MarketEvent]:
    """Map a FRED `release/dates` payload to the upcoming `MarketEvent`s for one
    release, defensively — a shape-broken payload or a malformed row is skipped, never
    a raise and never a fabricated date. Only dates on or after `today` are kept."""
    dates = payload.get("release_dates") if isinstance(payload, dict) else None
    if not isinstance(dates, Sequence) or isinstance(dates, (str, bytes)):
        return []
    events: list[MarketEvent] = []
    for row in dates:
        parsed = _parse_date(row.get("date")) if isinstance(row, dict) else None
        if parsed is None or parsed < today:
            continue
        events.append(
            MarketEvent(
                category="macro",
                title=str(release["title"]),
                symbol=None,
                scheduled_at=datetime(parsed.year, parsed.month, parsed.day, 12, 0, tzinfo=UTC),
                magnitude=None,
                source=_SOURCE,
                note=str(release["note"]),
            )
        )
    return events


def _parse_date(raw: Any) -> date | None:
    """Parse a FRED `YYYY-MM-DD` date string, or `None` for anything unparseable."""
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


__all__ = ["FredReleasesSource"]

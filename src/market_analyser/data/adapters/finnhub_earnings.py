"""Finnhub earnings-calendar adapter — Plan 0113 phase 2 (ADR-0107, ADR-0019, ADR-0038).

The event calendar's `earnings` provider: upcoming equity earnings dates (with EPS /
revenue estimates where the free tier serves them) from Finnhub's earnings calendar.

**Keyed, but inert without the key.** Like the FRED macro provider (ADR-0107) and the
LunarCrush social source (ADR-0103), an absent `finnhub_api_key` makes the adapter
**inert**: no request is issued and the result is an empty `CalendarFetch` with a
"not configured" note — never an exception, never fabricated dates. The same honest-
empty degrade covers every failure mode on the resilient path (ADR-0019): a rate-
limit (the free tier is 60 req/min), transport exhaustion, or a shape-broken payload.

**Free-tier field gating (ADR-0107 risk).** Some estimate fields are premium; a field
Finnhub does not serve on the free tier (a null `epsEstimate`/`revenueEstimate`) is
degraded to `None`/omitted rather than failing the row — the earnings *date* still
surfaces, and the event note discloses which estimates were unavailable.

**Auth is a header, not the path (secret hygiene).** The key travels as the
`X-Finnhub-Token` header — never embedded in the URL — so it cannot reach the
resilient client's path-only failure log (ADR-0038 rule 1; cf. `social_sentiment`).

**Dates are day-level with a session hint.** Finnhub gives the release *date* plus a
session code (`bmo` before open / `amc` after close / `dmh` during hours), not an
exact time; the event is anchored at 12:00 UTC of that date (noon keeps the calendar
day stable in every US timezone) and the note carries the session.

Conforms to `EventCalendarSource.fetch_events` (ADR-0031); package-internal per
ADR-0007 — reached through the `event_calendar` tool's registry, never imported
directly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.sources import EventCalendarSource
from market_analyser.data.types import CalendarFetch, MarketEvent
from market_analyser.persistence.secrets import SecretsStore

_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
_SOURCE = "finnhub"

# The earnings calendar changes slowly relative to a session; a short TTL absorbs
# repeat reads without staling the forward window.
_DEFAULT_TTL_SECONDS = 900.0

# The forward look-ahead horizons the tool's `window` selects among, in days.
_WINDOW_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90, "180d": 180, "1y": 365}
_DEFAULT_WINDOW_DAYS = 90

_SESSIONS = {
    "bmo": "before market open",
    "amc": "after market close",
    "dmh": "during market hours",
}

_UNCONFIGURED_NOTE = (
    "Finnhub unconfigured (finnhub_api_key) — earnings dates omitted until a key is "
    "set (the source is inert: zero requests)."
)


def _utcnow() -> datetime:
    """Wall-clock seam; the calendar is wall-clock-sensitive (no as_of)."""
    return datetime.now(tz=UTC)


class FinnhubEarningsSource(EventCalendarSource):
    """Fetches upcoming equity earnings from Finnhub's calendar. Inert (honest-empty,
    no request) until a `finnhub_api_key` secret is present; degrades unreadable
    estimate fields to null rather than failing the row (free-tier gating)."""

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
                source_name="finnhub",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
            )
        )

    def fetch_events(
        self, *, symbol: str | None = None, window: str | None = None
    ) -> CalendarFetch:
        key = self._secrets.get("finnhub_api_key") if self._secrets is not None else None
        if not key:
            return CalendarFetch(events=[], notes=(_UNCONFIGURED_NOTE,))
        today = self._clock().date()
        horizon = _WINDOW_DAYS.get(window or "", _DEFAULT_WINDOW_DAYS)
        params: dict[str, str | int | float] = {
            "from": today.isoformat(),
            "to": (today + timedelta(days=horizon)).isoformat(),
        }
        if symbol:
            params["symbol"] = symbol.strip().upper()
        try:
            response = self._http.get(
                _EARNINGS_URL,
                params=params,
                headers={"X-Finnhub-Token": key},  # header auth — never the URL path
                expect_json=True,
            )
        except ResilientHttpError:
            return CalendarFetch(
                events=[], notes=("Finnhub earnings unavailable (upstream error).",)
            )
        return CalendarFetch(events=_parse_calendar(response.json(), today))


def _parse_calendar(payload: Any, today: date) -> list[MarketEvent]:
    """Map a Finnhub `calendar/earnings` payload to upcoming `MarketEvent`s,
    defensively — a shape-broken payload or a malformed row is skipped, never a raise
    and never a fabricated date. Only rows on or after `today` are kept."""
    rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    events: list[MarketEvent] = []
    for row in rows:
        event = _event_from_row(row, today)
        if event is not None:
            events.append(event)
    return events


def _event_from_row(row: Any, today: date) -> MarketEvent | None:
    """One earnings row → a `MarketEvent`, or `None` if unusable. EPS estimate rides
    `magnitude`; the session, quarter/year, revenue estimate, and any free-tier gaps
    ride the `note` (the model carries one magnitude, so revenue is disclosed there)."""
    if not isinstance(row, dict):
        return None
    when = _parse_date(row.get("date"))
    symbol = row.get("symbol")
    if when is None or when < today or not isinstance(symbol, str) or not symbol:
        return None
    eps = _finite(row.get("epsEstimate"))
    revenue = _finite(row.get("revenueEstimate"))
    return MarketEvent(
        category="earnings",
        title=f"{symbol} earnings",
        symbol=symbol,
        scheduled_at=datetime(when.year, when.month, when.day, 12, 0, tzinfo=UTC),
        magnitude=eps,
        source=_SOURCE,
        note=_build_note(row, eps, revenue),
    )


def _build_note(row: dict[str, Any], eps: float | None, revenue: float | None) -> str:
    """Compose the honest coverage note: session + quarter/year, the revenue estimate
    when present, and an explicit disclosure of any estimate the free tier gated."""
    session = _SESSIONS.get(str(row.get("hour", "")), "session unspecified")
    quarter, year = row.get("quarter"), row.get("year")
    period = f"Q{quarter} {year}" if quarter and year else "period unknown"
    parts = [f"{period} earnings, {session} (day-level date; Finnhub gives no exact time)"]
    if revenue is not None:
        parts.append(f"revenue est {revenue:,.0f}")
    gated = [name for name, value in (("EPS", eps), ("revenue", revenue)) if value is None]
    if gated:
        parts.append(f"{'/'.join(gated)} estimate unavailable on the free tier")
    return "; ".join(parts) + "."


def _parse_date(raw: Any) -> date | None:
    """Parse a Finnhub `YYYY-MM-DD` date string, or `None` for anything unparseable."""
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _finite(raw: Any) -> float | None:
    """A finite numeric estimate, or `None` for null / non-numeric / non-finite — a
    gated free-tier field degrades to null, never a fabricated zero (ADR-0019)."""
    import math

    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


__all__ = ["FinnhubEarningsSource"]

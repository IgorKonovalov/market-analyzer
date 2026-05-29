"""Golden-path smoke driver (Plan 0016).

A runnable module — *not* pytest-collected (no `test_` prefix) — that attaches
to a live `pnpm dev:all` sidecar and drives one end-to-end golden path through
every shipped layer against **live** upstreams (Yahoo, TradingView):

     1. attach + /healthz identity      8. stocktwits_sentiment (live)
     2. get_ohlcv (live Yahoo)          9. search_symbols (live Yahoo)
     3. show_chart -> viewer           10. annotation roundtrip + highlight
     4. run_backtest + determinism     11. SSE liveness (chart.show/run.completed/
     5. screener_query (live TV)           chart.highlight observed end-to-end)
     6. news_for + sentiment_for_news  12. strategies list CLI
        (live RSS)                     13. cleanup (delete the rows it wrote)
     7. crypto_fear_greed (live Alternative.me)

It prints one `PASS`/`FAIL`/`UPSTREAM-DOWN` line per step and exits non-zero iff
any step is `FAIL`. A step that fails only because the upstream is unavailable
(typed `ResilientHttpError` from the sidecar, or a 5xx) is reported
`UPSTREAM-DOWN` and is non-fatal — the operator can tell "their problem" from
"our problem" at a glance.

All network I/O lives inside `main()` (and the step bodies it calls); importing
this module touches no network. REST routes are driven with stdlib `urllib`
(matching `api/__main__.py`'s `stop` path); MCP tools are driven with the `mcp`
package's Streamable-HTTP client — exported as `streamable_http_client` in the
pinned `mcp==1.27.1` (the plan's guessed `streamablehttp_client` spelling does
not exist in this version; no raw-JSON-RPC fallback was needed).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.api.lockfile import DEFAULT_LOCKFILE_NAME, read_lockfile
from market_analyser.api.mcp_secret import read_secret_record
from market_analyser.config import default_app_data_dir
from market_analyser.data._http import ResilientHttpError

# Mirrors `api/__main__.MCP_SECRET_FILENAME` without importing that
# uvicorn-heavy module just for a constant.
MCP_SECRET_FILENAME = "mcp-secret.json"

# Golden-path fixtures, named in one place for an easy swap (plan assumption 3).
# The window is fully in the past so the cached bars are stable: step 2 warms
# the exact backtest window into the cache, so step 4's paired runs read
# cache-only and the determinism sub-assert is meaningful (plan risk #4).
SYMBOL = "AAPL"
TIMEFRAME = "1d"
WINDOW_START = datetime(2026, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 3, 1, tzinfo=UTC)
ANNOTATION_TS = datetime(2026, 2, 2, tzinfo=UTC)
SMOKE_AGENT_ID = "smoke"

REST_TIMEOUT_S = 30.0
MCP_TIMEOUT_S = 60.0
SSE_LIVENESS_TIMEOUT_S = 20.0

# The visual half — the script drives the SSE-publishing tools so these land in
# the live viewer, but only a human can confirm they rendered. Kept in sync with
# the "Smoke check" section of docs/onboarding/claude-code-setup.md.
MANUAL_CHECKLIST = """\
Manual visual checklist - watch the live viewer (the script cannot assert these):
  [ ] 1. AAPL daily candles render in the viewer after step 3 (show_chart).
  [ ] 2. A bullish marker lands on the AAPL chart after step 8 (highlight_pattern).
  [ ] 3. BacktestView shows a non-empty equity curve + metrics after step 4.
  [ ] 4. The screener reply surfaces an "as of HH:MM" wall-clock (queried_at, step 5)."""


# --------------------------------------------------------------------------- #
# Pure, network-free helpers (unit-tested in test_golden_path_helpers.py)
# --------------------------------------------------------------------------- #


class Status(StrEnum):
    """The three outcomes a step can report."""

    PASS = "PASS"
    FAIL = "FAIL"
    UPSTREAM_DOWN = "UPSTREAM-DOWN"


@dataclass(frozen=True)
class StepResult:
    name: str
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class Connection:
    """Everything needed to drive the live sidecar, read from disk on attach."""

    port: int
    renderer_bearer: str
    mcp_bearer: str
    data_dir: Path

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class SidecarNotRunning(Exception):
    """No live sidecar discoverable from the data dir — the operator must start one."""


class UpstreamUnavailable(Exception):
    """A live upstream (Yahoo/TradingView) is down — distinct from an integration break."""


def read_connection(data_dir: Path) -> Connection:
    """Read the live sidecar's port + bearers from `<data_dir>/sidecar.lock`
    and `<data_dir>/mcp-secret.json`.

    Raises `SidecarNotRunning` (naming `pnpm dev:all`) when either file is
    absent — the hybrid flow needs the viewer up for the visual half.
    """
    lock_path = data_dir / DEFAULT_LOCKFILE_NAME
    record = read_lockfile(lock_path)
    if record is None:
        raise SidecarNotRunning(
            f"no sidecar lockfile at {lock_path}. Run `pnpm dev:all` first.",
        )
    secret_path = data_dir / MCP_SECRET_FILENAME
    if not secret_path.exists():
        raise SidecarNotRunning(
            f"no MCP secret at {secret_path}. Run `pnpm dev:all` first.",
        )
    mcp_bearer = read_secret_record(secret_path).secret
    return Connection(
        port=record.port,
        renderer_bearer=record.renderer_secret,
        mcp_bearer=mcp_bearer,
        data_dir=data_dir,
    )


def classify_error(exc: BaseException) -> Status:
    """Map an exception to a step status.

    An assertion mismatch is *our* integration breaking (`FAIL`). A typed
    `ResilientHttpError`/`UpstreamUnavailable`, or a 5xx HTTP response, is the
    upstream being unavailable (`UPSTREAM-DOWN`). Anything else defaults to
    `FAIL` — an unexpected error is our problem until proven otherwise.
    """
    if isinstance(exc, AssertionError):
        return Status.FAIL
    if isinstance(exc, (ResilientHttpError, UpstreamUnavailable)):
        return Status.UPSTREAM_DOWN
    if isinstance(exc, urllib.error.HTTPError) and exc.code >= 500:
        return Status.UPSTREAM_DOWN
    return Status.FAIL


def exit_code(results: Sequence[StepResult]) -> int:
    """Process exit code: 1 iff any step is `FAIL`. An `UPSTREAM-DOWN`-only run
    exits 0 (with the warning visible in the report)."""
    return 1 if any(r.status is Status.FAIL for r in results) else 0


def format_report(results: Sequence[StepResult]) -> str:
    """Render the one-line-per-step report plus a one-line tally."""
    lines = [
        f"[{r.status.value:>13}] {r.name}" + (f" - {r.detail}" if r.detail else "") for r in results
    ]
    n_pass = sum(1 for r in results if r.status is Status.PASS)
    n_fail = sum(1 for r in results if r.status is Status.FAIL)
    n_down = sum(1 for r in results if r.status is Status.UPSTREAM_DOWN)
    lines.append("")
    lines.append(f"{n_pass} PASS, {n_fail} FAIL, {n_down} UPSTREAM-DOWN")
    return "\n".join(lines)


def strip_run_provenance(result: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a BacktestResult dict without the documented run-provenance
    fields, so two runs of identical inputs compare equal (ADR-0018)."""
    return {k: v for k, v in result.items() if k not in {"run_id", "started_at", "finished_at"}}


# The upstream-failure `partial_reason` values get reported by get_ohlcv as data
# (Plan 0013), not as a raised exception, so a live Yahoo hiccup during the smoke
# run must be mapped back to the UPSTREAM-DOWN bucket explicitly.
_OHLCV_UPSTREAM_REASONS = frozenset({"rate_limited", "upstream_unavailable", "unknown_symbol"})


def unwrap_ohlcv_bars(payload: dict[str, Any]) -> list[Any]:
    """Unwrap a `get_ohlcv` MCP response (`GetOhlcvResponse`, Plan 0013 phase 2)
    into its `bars` list, translating a partial result into the right step outcome.

    The tool returns `{bars, partial_reason, message}` (no longer a bare list).
    `partial_reason` is `None` on a clean read; one of `rate_limited`/
    `upstream_unavailable`/`unknown_symbol` when only some gaps could be filled —
    an upstream failure surfaced as data rather than an exception; or
    `backfill_async_pending` only when `backfill_async=true` was requested. The
    smoke driver calls get_ohlcv synchronously (`backfill_async=false`), so:

    - a clean response (`partial_reason is None`) yields the bars;
    - an upstream `partial_reason` raises `UpstreamUnavailable`, so the classifier
      reports the step `UPSTREAM-DOWN` (their problem) rather than `FAIL`;
    - any other shape (missing `bars`, `backfill_async_pending`, an unknown
      reason) is our integration breaking -> `AssertionError` (`FAIL`).
    """
    bars = payload.get("bars")
    assert isinstance(bars, list), f"get_ohlcv response has no 'bars' list: {sorted(payload)}"
    reason = payload.get("partial_reason")
    if reason in _OHLCV_UPSTREAM_REASONS:
        raise UpstreamUnavailable(f"get_ohlcv partial ({reason}): {payload.get('message')}")
    assert reason is None, (
        f"unexpected get_ohlcv partial_reason {reason!r}: {payload.get('message')}"
    )
    return bars


# --------------------------------------------------------------------------- #
# Network plumbing (only ever called from main())
# --------------------------------------------------------------------------- #


def _rest(
    conn: Connection,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    bearer: str | None = None,
) -> tuple[int, Any]:
    """Issue a REST request via stdlib urllib. Lets `HTTPError` propagate so the
    classifier can distinguish a 5xx (UPSTREAM-DOWN) from a 4xx (FAIL)."""
    url = f"{conn.base_url}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(  # loopback-only, scheme is our own
        url,
        method=method,
        headers={"Authorization": f"Bearer {bearer or conn.renderer_bearer}"},
    )
    with urllib.request.urlopen(req, timeout=REST_TIMEOUT_S) as resp:
        body = resp.read()
        parsed = json.loads(body) if body else None
        return resp.status, parsed


@asynccontextmanager
async def mcp_session(conn: Connection) -> AsyncIterator[ClientSession]:
    """Open an initialized MCP client session against `/mcp` with the MCP bearer."""
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {conn.mcp_bearer}"},
            timeout=httpx.Timeout(MCP_TIMEOUT_S),
        ) as http_client,
        streamable_http_client(
            f"{conn.base_url}/mcp",
            http_client=http_client,
        ) as (read_stream, write_stream, _get_session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


def _content_text(content: Sequence[Any]) -> str:
    return " ".join(getattr(block, "text", "") for block in content).strip()


_UPSTREAM_HINTS = ("request failed after", "ResilientHttpError", "timed out", "502", "503", "504")


async def _call_tool(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool, raising `UpstreamUnavailable` when the error reads like an
    upstream failure and `AssertionError` otherwise. Returns the structured dict."""
    result = await session.call_tool(name, args)
    if result.isError:
        message = _content_text(result.content)
        if any(hint in message for hint in _UPSTREAM_HINTS):
            raise UpstreamUnavailable(f"{name}: {message}")
        raise AssertionError(f"{name} errored: {message}")
    assert result.structuredContent is not None, f"{name}: no structured content"
    return dict(result.structuredContent)


def _unwrap_list(payload: dict[str, Any]) -> list[Any]:
    """FastMCP wraps list-returning tools as `{'result': [...]}`; unwrap that."""
    rows = payload.get("result", payload)
    assert isinstance(rows, list), f"expected a list, got {type(rows).__name__}"
    return rows


class SseReader:
    """Background thread subscribing to `GET /events` and collecting envelope types.

    `connected` is set once the first SSE line arrives (the `retry:` preamble),
    which guarantees the server-side subscription is active — so the caller can
    wait for it before publishing the events it expects to observe.
    """

    def __init__(self, conn: Connection) -> None:
        self._url = f"{conn.base_url}/events?token={urllib.parse.quote(conn.renderer_bearer)}"
        self.types: set[str] = set()
        self.connected = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait_connected(self, timeout: float) -> bool:
        return self.connected.wait(timeout)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            req = urllib.request.Request(self._url)  # loopback only
            with urllib.request.urlopen(req, timeout=REST_TIMEOUT_S) as resp:
                for raw in resp:
                    self.connected.set()
                    if self._stop.is_set():
                        return
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        envelope = json.loads(line[len("data:") :].strip())
                    except json.JSONDecodeError:
                        continue
                    event_type = envelope.get("type")
                    if isinstance(event_type, str):
                        self.types.add(event_type)
        except Exception:  # a dead reader just yields no frames
            self.connected.set()


# --------------------------------------------------------------------------- #
# Golden-path steps (each takes its clients as args — no module-level state)
# --------------------------------------------------------------------------- #


def step_health(conn: Connection) -> str:
    status, body = _rest(conn, "GET", "/healthz")
    assert status == 200, f"/healthz returned {status}"
    assert body.get("ok") is True, f"/healthz ok != True: {body}"
    reported = body.get("data_dir")
    assert reported == str(conn.data_dir), (
        f"data_dir mismatch: /healthz={reported!r}, lockfile-dir={str(conn.data_dir)!r}"
    )
    return f"data_dir={reported}"


async def step_ohlcv(session: ClientSession) -> str:
    payload = await _call_tool(
        session,
        "get_ohlcv",
        {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
        },
    )
    bars = unwrap_ohlcv_bars(payload)
    assert len(bars) >= 1, "get_ohlcv returned zero bars"
    for bar in bars:
        for key in ("open", "high", "low", "close"):
            value = bar[key]
            assert isinstance(value, (int, float)) and math.isfinite(value) and value > 0, (
                f"bar {key}={value!r} is not a finite positive float"
            )
        assert bar["volume"] >= 0, f"bar volume {bar['volume']!r} < 0"
        event_ts = datetime.fromisoformat(bar["event_ts"])
        assert WINDOW_START <= event_ts <= WINDOW_END, f"bar event_ts {event_ts} outside window"
    return f"{len(bars)} bars in window"


async def step_show_chart(session: ClientSession) -> str:
    payload = await _call_tool(
        session,
        "show_chart",
        {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "range_start": WINDOW_START.isoformat(),
            "range_end": WINDOW_END.isoformat(),
        },
    )
    assert payload.get("event_published") is True, payload
    assert payload.get("type") == "chart.show", payload
    return "chart.show published"


async def step_backtest(session: ClientSession, conn: Connection) -> str:
    args = {
        "strategy_id": "rsi",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "range_start": WINDOW_START.isoformat(),
        "range_end": WINDOW_END.isoformat(),
        "params": {},
    }
    first = await _call_tool(session, "run_backtest", args)
    second = await _call_tool(session, "run_backtest", args)
    for label, reply in (("first", first), ("second", second)):
        summary = reply["summary"]
        for metric in ("sharpe", "max_drawdown"):
            assert math.isfinite(summary[metric]), f"{label} {metric} not finite: {summary[metric]}"

    _, full_first = _rest(conn, "GET", f"/backtests/{first['run_id']}")
    _, full_second = _rest(conn, "GET", f"/backtests/{second['run_id']}")
    assert isinstance(full_first.get("equity_curve"), list) and full_first["equity_curve"], (
        "equity_curve empty"
    )
    assert isinstance(full_first.get("trades"), list), "trades missing"
    assert strip_run_provenance(full_first) == strip_run_provenance(full_second), (
        "two runs of identical inputs differ after stripping run provenance"
    )
    return (
        f"trades={first['summary']['trade_count']}, "
        f"equity_pts={len(full_first['equity_curve'])}, deterministic"
    )


async def step_screener(session: ClientSession) -> str:
    # screener_query is the one tool that takes a single Pydantic-model param,
    # so its MCP arguments nest the fields under `params` (every other tool
    # takes flat top-level parameters).
    payload = await _call_tool(
        session,
        "screener_query",
        {
            "params": {
                "filters": {"RSI": {"lt": 35}},
                "market": "america",
                "exchange": "NASDAQ",
                "limit": 5,
            },
        },
    )
    rows = payload["rows"]
    assert isinstance(rows, list), f"rows is not a list: {type(rows).__name__}"
    assert 1 <= len(rows) <= 5, f"expected 1-5 screener rows, got {len(rows)}"
    for row in rows:
        assert row.get("symbol"), f"screener row missing symbol: {row}"
    assert payload.get("queried_at"), "screener reply missing queried_at"
    return f"{len(rows)} rows, queried_at={payload['queried_at']}"


async def step_news(session: ClientSession) -> str:
    # news_for and sentiment_for_news each take a single Pydantic-model param, so
    # their MCP arguments nest under `params` (like screener_query). Unfiltered
    # (symbol=None) so the assertion does not hinge on any one ticker being in the
    # headlines right now; the five feeds in a 24h window are reliably non-empty.
    news = await _call_tool(
        session,
        "news_for",
        {"params": {"symbol": None, "window": "24h", "limit": 5, "with_sentiment": True}},
    )
    items = news["items"]
    assert isinstance(items, list), f"items is not a list: {type(items).__name__}"
    assert items, "news_for returned no headlines across all feeds in 24h"
    assert len(items) <= 5, f"news_for ignored limit: {len(items)} > 5"
    for item in items:
        assert item.get("title") and item.get("url") and item.get("source"), (
            f"news item incomplete: {item}"
        )
        assert isinstance(item.get("compound_sentiment"), float), (
            f"with_sentiment item missing a float score: {item}"
        )
    assert news.get("queried_at"), "news_for reply missing queried_at"

    # sentiment_for_news is defined even with zero matching headlines (score 0.0,
    # all-zero breakdown), so a quiet ticker does not make this step FAIL.
    sentiment = await _call_tool(
        session,
        "sentiment_for_news",
        {"params": {"symbol": "BTC", "window": "24h"}},
    )
    assert sentiment.get("source") == "rss-vader", sentiment
    score = sentiment["score"]
    assert -1.0 <= score <= 1.0, f"sentiment score out of range: {score}"
    breakdown = sentiment["breakdown"]
    assert set(breakdown) == {"positive", "negative", "neutral"}, breakdown
    assert sentiment.get("queried_at"), "sentiment reply missing queried_at"
    return f"{len(items)} headlines; BTC score={score:.3f} {breakdown}"


async def step_fear_greed(session: ClientSession) -> str:
    # crypto_fear_greed takes a single empty Pydantic-model param, so its MCP
    # arguments nest under `params` (like screener_query / news_for) with no
    # fields. The index is market-wide and updates ~daily, so the only stable
    # assertions are the response shape plus the value/label invariants.
    payload = await _call_tool(session, "crypto_fear_greed", {"params": {}})
    assert set(payload) == {"value", "classification", "published_at", "queried_at", "source"}, (
        f"crypto_fear_greed shape unexpected: {sorted(payload)}"
    )
    value = payload["value"]
    assert isinstance(value, int) and 0 <= value <= 100, f"F&G value out of range: {value!r}"
    assert payload["classification"] in (
        "Extreme Fear",
        "Fear",
        "Neutral",
        "Greed",
        "Extreme Greed",
    ), f"unknown F&G classification: {payload['classification']!r}"
    assert payload.get("published_at"), "crypto_fear_greed missing published_at"
    assert payload.get("queried_at"), "crypto_fear_greed missing queried_at"
    assert payload["source"] == "alternative.me-fng", payload
    return f"value={value} ({payload['classification']})"


async def step_stocktwits(session: ClientSession) -> str:
    # stocktwits_sentiment takes a single Pydantic-model param, so its MCP
    # arguments nest under `params` (like news_for / crypto_fear_greed). AAPL is a
    # high-coverage large-cap, but the tool is defined even with zero matching
    # posts (score 0.0, all-zero breakdown), so a quiet window does not FAIL the
    # step — only the response shape + invariants are asserted. A rate-limit (403)
    # that exhausts retries surfaces as ResilientHttpError -> UPSTREAM-DOWN.
    payload = await _call_tool(
        session,
        "stocktwits_sentiment",
        {"params": {"symbol": "AAPL", "window": "24h"}},
    )
    assert payload.get("symbol") == "AAPL", payload
    assert payload.get("source") == "stocktwits", payload
    assert payload.get("window") == "24h", payload
    score = payload["score"]
    assert -1.0 <= score <= 1.0, f"stocktwits score out of range: {score}"
    breakdown = payload["breakdown"]
    assert set(breakdown) == {"positive", "negative", "neutral"}, breakdown
    assert all(isinstance(v, int) and v >= 0 for v in breakdown.values()), breakdown
    assert payload.get("queried_at"), "stocktwits reply missing queried_at"
    return f"AAPL score={score:.3f} {breakdown}"


async def step_search(session: ClientSession) -> str:
    # search_symbols resolves a loose, free-text name to fetchable Yahoo-native
    # symbols (Plan 0024 / ADR-0026) — the agent's recovery path for get_ohlcv's
    # unknown_symbol. Query a loose name ("bitcoin", not a ticker) and assert it
    # resolves to at least one BTC* symbol; every returned `symbol` is in the
    # OHLCV namespace by construction, so the response shape + a BTC hit are the
    # stable asserts (the exact result set drifts with Yahoo's relevance order). A
    # single Pydantic-model param, so the arguments nest under `params`.
    payload = await _call_tool(session, "search_symbols", {"params": {"query": "bitcoin"}})
    results = payload["results"]
    assert isinstance(results, list), f"results is not a list: {type(results).__name__}"
    assert results, "search_symbols('bitcoin') returned zero matches"
    for row in results:
        assert row.get("symbol"), f"search result missing symbol: {row}"
        assert {"name", "exchange", "quote_type"} <= set(row), (
            f"search result missing a SymbolInfo field: {sorted(row)}"
        )
    assert any("BTC" in row["symbol"].upper() for row in results), (
        f"no BTC* symbol in search results: {[r['symbol'] for r in results]}"
    )
    assert payload.get("queried_at"), "search_symbols reply missing queried_at"
    return f"{len(results)} matches; symbols={[r['symbol'] for r in results][:3]}"


async def step_annotations(session: ClientSession) -> str:
    written = await _call_tool(
        session,
        "write_annotation",
        {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "event_ts": ANNOTATION_TS.isoformat(),
            "kind": "bullish_marker",
            "label": "smoke",
            "agent_id": SMOKE_AGENT_ID,
        },
    )
    annotation_id = written.get("id")
    assert annotation_id, "write_annotation returned no id"

    list_payload = await _call_tool(
        session,
        "list_annotations",
        {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
        },
    )
    listed = _unwrap_list(list_payload)
    assert any(row["id"] == annotation_id for row in listed), (
        f"written id {annotation_id} not found in list_annotations"
    )

    highlight = await _call_tool(
        session,
        "highlight_pattern",
        {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "event_ts": ANNOTATION_TS.isoformat(),
            "kind": "bullish_marker",
            "label": "smoke",
            "agent_id": SMOKE_AGENT_ID,
        },
    )
    assert highlight.get("event_published") is True, highlight
    assert highlight.get("type") == "chart.highlight", highlight
    return f"wrote+listed id={annotation_id[:8]}..., highlight published"


def step_sse_liveness(reader: SseReader) -> str:
    expected = {"chart.show", "run.completed", "chart.highlight"}
    deadline = time.monotonic() + SSE_LIVENESS_TIMEOUT_S
    while time.monotonic() < deadline and not expected <= reader.types:
        time.sleep(0.1)
    missing = expected - reader.types
    assert not missing, f"SSE did not deliver {sorted(missing)} (saw {sorted(reader.types)})"
    return f"observed {sorted(expected)}"


def step_cli() -> str:
    executable = shutil.which("market-analyser")
    cmd = (
        [executable, "strategies", "list", "--json"]
        if executable
        else [sys.executable, "-m", "market_analyser.cli", "strategies", "list", "--json"]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, f"CLI exited {proc.returncode}: {proc.stderr.strip()}"
    rows = json.loads(proc.stdout)
    assert isinstance(rows, list), "strategies list --json did not emit a JSON array"
    ids = [row["id"] for row in rows]
    assert len(ids) >= 6, f"expected >= 6 strategies, got {len(ids)}: {ids}"
    assert ids == sorted(ids), f"strategy ids not sorted: {ids}"
    assert len(ids) == len(set(ids)), f"duplicate strategy ids: {ids}"
    return f"{len(ids)} strategies, sorted + unique"


def step_cleanup(conn: Connection) -> str:
    window = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "start": WINDOW_START.isoformat(),
        "end": WINDOW_END.isoformat(),
    }
    _, listed = _rest(conn, "GET", "/annotations", params=window)
    smoke_rows = [row for row in listed if row.get("agent_id") == SMOKE_AGENT_ID]
    for row in smoke_rows:
        status, _ = _rest(conn, "DELETE", f"/annotations/{row['id']}")
        assert status == 204, f"DELETE /annotations/{row['id']} returned {status}"

    _, after = _rest(conn, "GET", "/annotations", params=window)
    residue = [row for row in after if row.get("agent_id") == SMOKE_AGENT_ID]
    assert not residue, f"cleanup left {len(residue)} smoke annotation(s) behind"
    return f"deleted {len(smoke_rows)}, no residue"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


@dataclass
class _Results:
    items: list[StepResult] = field(default_factory=list)

    def run_sync(self, name: str, fn: Callable[[], str]) -> None:
        self.items.append(_capture(name, fn))

    async def run_async(self, name: str, coro: Awaitable[str]) -> None:
        self.items.append(await _capture_async(name, coro))


def _capture(name: str, fn: Callable[[], str]) -> StepResult:
    try:
        return StepResult(name=name, status=Status.PASS, detail=fn())
    except Exception as exc:  # classifier decides FAIL vs UPSTREAM-DOWN
        return StepResult(name=name, status=classify_error(exc), detail=_describe(exc))


async def _capture_async(name: str, coro: Awaitable[str]) -> StepResult:
    try:
        return StepResult(name=name, status=Status.PASS, detail=await coro)
    except Exception as exc:
        return StepResult(name=name, status=classify_error(exc), detail=_describe(exc))


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


async def _amain() -> int:
    data_dir = default_app_data_dir()
    try:
        conn = read_connection(data_dir)
    except SidecarNotRunning as exc:
        print(str(exc))
        return 1

    results = _Results()
    results.run_sync("1. attach + health", lambda: step_health(conn))

    try:
        async with mcp_session(conn) as session:
            await results.run_async("2. ohlcv (live Yahoo)", step_ohlcv(session))
            reader = SseReader(conn)
            reader.start()
            reader.wait_connected(timeout=5.0)
            try:
                await results.run_async("3. show_chart -> viewer", step_show_chart(session))
                await results.run_async(
                    "4. run_backtest + determinism", step_backtest(session, conn)
                )
                await results.run_async("5. screener (live TradingView)", step_screener(session))
                await results.run_async("6. news + sentiment (live RSS)", step_news(session))
                await results.run_async(
                    "7. crypto F&G (live Alternative.me)", step_fear_greed(session)
                )
                await results.run_async("8. stocktwits sentiment (live)", step_stocktwits(session))
                await results.run_async("9. search_symbols (live Yahoo)", step_search(session))
                await results.run_async(
                    "10. annotation roundtrip + highlight", step_annotations(session)
                )
                results.run_sync("11. SSE liveness", lambda: step_sse_liveness(reader))
            finally:
                reader.stop()
    except Exception as exc:  # a connect/teardown failure is one FAIL, not a crash
        results.items.append(StepResult("MCP session", classify_error(exc), _describe(exc)))

    results.run_sync("12. strategies list CLI", step_cli)
    results.run_sync("13. cleanup", lambda: step_cleanup(conn))

    print(format_report(results.items))
    print("\n" + MANUAL_CHECKLIST)
    return exit_code(results.items)


def main() -> int:
    # Windows consoles default to cp1252; force UTF-8 (replace on failure) so the
    # report can never die on a stray non-ASCII byte in an upstream error string.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())

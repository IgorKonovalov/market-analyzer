"""Plan 0016 phase 1 done-when: offline coverage of the smoke driver's pure helpers.

No network — every test here runs under the normal `uv run pytest`. Covers:
- the lockfile/secret parser returns the right `(port, renderer_bearer,
  mcp_bearer, data_dir)` from a fixture lockfile + secret file, and raises a
  clear `pnpm dev:all` error when the lockfile is absent;
- the report formatter renders `PASS`/`FAIL`/`UPSTREAM-DOWN` lines;
- `exit_code` is 1 iff any step is `FAIL` (an `UPSTREAM-DOWN`-only run exits 0);
- the error classifier maps `ResilientHttpError`/5xx → `UPSTREAM-DOWN` and an
  assertion mismatch → `FAIL`.
"""

from __future__ import annotations

import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_analyser.api.lockfile import LockfileRecord, write_lockfile
from market_analyser.api.mcp_secret import rotate_secret
from market_analyser.data._http import ResilientHttpError
from tests.smoke.golden_path import (
    SidecarNotRunning,
    Status,
    StepResult,
    UpstreamUnavailable,
    classify_error,
    exit_code,
    format_report,
    read_connection,
    strip_run_provenance,
    unwrap_ohlcv_bars,
)


def _write_fixture(data_dir: Path) -> tuple[LockfileRecord, str]:
    record = LockfileRecord(
        pid=4321,
        port=51234,
        renderer_secret="r" * 64,
        started_at=datetime(2026, 5, 24, tzinfo=UTC),
        process_create_time=123.0,
        sidecar_version="0.1.0",
    )
    write_lockfile(data_dir / "sidecar.lock", record)
    secret = rotate_secret(data_dir / "mcp-secret.json").secret
    return record, secret


def test_read_connection_parses_lockfile_and_secret(tmp_path: Path) -> None:
    record, secret = _write_fixture(tmp_path)
    conn = read_connection(tmp_path)
    assert conn.port == record.port
    assert conn.renderer_bearer == record.renderer_secret
    assert conn.mcp_bearer == secret
    assert conn.data_dir == tmp_path
    assert conn.base_url == f"http://127.0.0.1:{record.port}"


def test_read_connection_missing_lockfile_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SidecarNotRunning) as excinfo:
        read_connection(tmp_path)
    assert "pnpm dev:all" in str(excinfo.value)


def test_read_connection_missing_secret_raises_clear_error(tmp_path: Path) -> None:
    record = LockfileRecord(
        pid=1,
        port=5,
        renderer_secret="r" * 64,
        started_at=datetime(2026, 5, 24, tzinfo=UTC),
        process_create_time=1.0,
        sidecar_version="0.1.0",
    )
    write_lockfile(tmp_path / "sidecar.lock", record)  # lockfile present, secret absent
    with pytest.raises(SidecarNotRunning) as excinfo:
        read_connection(tmp_path)
    assert "pnpm dev:all" in str(excinfo.value)


def test_exit_code_one_iff_any_fail() -> None:
    assert exit_code([StepResult("a", Status.PASS), StepResult("b", Status.PASS)]) == 0
    # UPSTREAM-DOWN alone is non-fatal — the operator's problem, not ours.
    assert exit_code([StepResult("a", Status.PASS), StepResult("b", Status.UPSTREAM_DOWN)]) == 0
    assert exit_code([StepResult("a", Status.FAIL)]) == 1
    assert exit_code([StepResult("a", Status.UPSTREAM_DOWN), StepResult("b", Status.FAIL)]) == 1


def test_format_report_renders_each_status() -> None:
    report = format_report(
        [
            StepResult("step one", Status.PASS, "5 bars"),
            StepResult("step two", Status.FAIL, "assertion boom"),
            StepResult("step three", Status.UPSTREAM_DOWN, "yahoo 503"),
        ],
    )
    assert "PASS" in report
    assert "FAIL" in report
    assert "UPSTREAM-DOWN" in report
    assert "step one" in report
    assert "assertion boom" in report
    assert "1 PASS, 1 FAIL, 1 UPSTREAM-DOWN" in report


def test_classify_error_upstream_vs_assertion() -> None:
    assert classify_error(AssertionError("mismatch")) is Status.FAIL
    rhe = ResilientHttpError(
        source_name="yahoo",
        last_response=None,
        last_exception=None,
        attempts=3,
    )
    assert classify_error(rhe) is Status.UPSTREAM_DOWN
    assert classify_error(UpstreamUnavailable("tradingview down")) is Status.UPSTREAM_DOWN
    http_5xx = urllib.error.HTTPError("http://x", 503, "Service Unavailable", hdrs=None, fp=None)  # type: ignore[arg-type]
    assert classify_error(http_5xx) is Status.UPSTREAM_DOWN
    # A 4xx is our problem (bad request shape), not the upstream being down.
    http_4xx = urllib.error.HTTPError("http://x", 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]
    assert classify_error(http_4xx) is Status.FAIL
    assert classify_error(ValueError("unexpected")) is Status.FAIL


def test_unwrap_ohlcv_bars_clean_response_returns_bars() -> None:
    bars = [{"open": 1.0}, {"open": 2.0}]
    assert unwrap_ohlcv_bars({"bars": bars, "partial_reason": None, "message": None}) is bars
    # an empty-but-clean response is still a clean unwrap (the zero-bars FAIL is
    # step_ohlcv's call, not this helper's).
    assert unwrap_ohlcv_bars({"bars": [], "partial_reason": None, "message": None}) == []


@pytest.mark.parametrize("reason", ["rate_limited", "upstream_unavailable", "unknown_symbol"])
def test_unwrap_ohlcv_bars_upstream_reason_is_upstream_down(reason: str) -> None:
    # An upstream partial_reason arrives as data, not an exception; the helper
    # re-raises it as UpstreamUnavailable so classify_error buckets it UPSTREAM-DOWN.
    with pytest.raises(UpstreamUnavailable) as excinfo:
        unwrap_ohlcv_bars({"bars": [], "partial_reason": reason, "message": "yahoo 429"})
    assert reason in str(excinfo.value)
    assert classify_error(excinfo.value) is Status.UPSTREAM_DOWN


def test_unwrap_ohlcv_bars_async_pending_is_fail() -> None:
    # backfill_async_pending only appears when backfill_async=true; the smoke
    # driver never requests that, so seeing it here is our bug -> FAIL.
    with pytest.raises(AssertionError):
        unwrap_ohlcv_bars({"bars": [], "partial_reason": "backfill_async_pending", "message": "x"})


def test_unwrap_ohlcv_bars_malformed_shape_is_fail() -> None:
    with pytest.raises(AssertionError):
        unwrap_ohlcv_bars({"partial_reason": None})  # missing 'bars'
    with pytest.raises(AssertionError):
        unwrap_ohlcv_bars({"bars": [], "partial_reason": "wat"})  # unknown reason


def test_strip_run_provenance_drops_only_provenance_fields() -> None:
    payload = {
        "run_id": "abc",
        "started_at": "t0",
        "finished_at": "t1",
        "metrics": {"sharpe": 1.0},
        "equity_curve": [1, 2, 3],
    }
    stripped = strip_run_provenance(payload)
    assert stripped == {"metrics": {"sharpe": 1.0}, "equity_curve": [1, 2, 3]}

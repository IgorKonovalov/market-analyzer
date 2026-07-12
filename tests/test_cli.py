"""Smoke test for the `market-analyser strategies list` CLI (Plan 0002 followup).

Plan 0002 phase 5's done-when ("six rows, identical across runs") was verified by
hand at close; Plan 0050 phase 6 adds rsi_stop, Plan 0054 adds
chart_pattern_breakout, and Plan 0075 adds ichimoku, so the roster is now nine. This
locks it in: it pins the strategy count (a stray extra module or a discovery
regression fails here), the sorted-by-id ordering the CLI
promises in its docstring, and the run-twice byte-equality both output modes
guarantee (`discover()` sorts by `META.id`; pydantic emits schemas in declaration
order). No network, no disk — pure in-process.
"""

from __future__ import annotations

import json

import pytest

from market_analyser import cli
from market_analyser.contracts.strategy import discover


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    """Invoke the CLI and return captured stdout; `main()` exits 0 on success."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(list(argv))
    assert excinfo.value.code == 0
    return capsys.readouterr().out


def test_strategies_list_json_has_nine_sorted_ids(capsys: pytest.CaptureFixture[str]) -> None:
    rows = json.loads(_run(capsys, "strategies", "list", "--json"))
    ids = [row["id"] for row in rows]

    # Nine is the roster: the Plan 0002 six (rsi + bollinger/macd/ema_cross/
    # supertrend/donchian), rsi_stop (Plan 0050 phase 6),
    # chart_pattern_breakout (Plan 0054), and ichimoku (Plan 0075). A stray
    # tenth module or a missing one trips this.
    assert len(ids) == 9
    assert ids == sorted(ids)
    # The CLI must surface exactly what discovery finds, in the same order.
    assert ids == list(discover().keys())


def test_strategies_list_json_is_byte_identical_across_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = _run(capsys, "strategies", "list", "--json")
    second = _run(capsys, "strategies", "list", "--json")
    assert first == second


def test_strategies_list_text_is_byte_identical_across_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = _run(capsys, "strategies", "list")
    second = _run(capsys, "strategies", "list")
    assert first == second
    # One header line per strategy (` - ` separates id from name); nine rows.
    header_lines = [line for line in first.splitlines() if " - " in line]
    assert len(header_lines) == 9

"""Plan 0070 phase 3 done-when: the CLI writes and gates the reference.

`test_committed_reference_is_fresh` is the local guard the plan asks for: a dev
who edits a tool / route / event and forgets to regenerate gets a red *local*
test, not only red CI. The remaining tests pin idempotency (two renders are
byte-identical) and both directions of the `--check` gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from market_analyser.apiref.__main__ import (
    check_reference,
    main,
    render_reference,
    write_reference,
)


@pytest.fixture(scope="module")
def reference_files() -> dict[str, str]:
    return render_reference()


def test_render_reference_has_all_four_full_detail_files(
    reference_files: dict[str, str],
) -> None:
    assert set(reference_files) == {"README.md", "mcp-tools.md", "rest-api.md", "events.md"}
    tools_md = reference_files["mcp-tools.md"]
    # Full detail: the forecast entry carries its description, params, and return shape.
    assert "## `forecast`" in tools_md
    assert "| `symbol` | string | yes |" in tools_md
    assert "| `horizons` |" in tools_md
    assert "**Returns:** `MultiHorizonForecastResult`" in tools_md
    assert "src/market_analyser/api/mcp_tools/forecast.py" in tools_md


def test_committed_reference_is_fresh(reference_files: dict[str, str]) -> None:
    # Renders against the real committed docs/reference/ tree; 0 means fresh.
    assert check_reference(reference_files) == 0


def test_main_check_on_committed_tree_returns_zero() -> None:
    assert main(["--check"]) == 0


def test_render_is_idempotent(reference_files: dict[str, str], tmp_path: Path) -> None:
    write_reference(reference_files, tmp_path)
    # A second independent render is byte-identical and passes the check.
    second = render_reference()
    assert reference_files == second
    assert check_reference(second, tmp_path) == 0


def test_check_detects_one_character_drift(reference_files: dict[str, str], tmp_path: Path) -> None:
    write_reference(reference_files, tmp_path)
    assert check_reference(reference_files, tmp_path) == 0
    # A single appended byte to any generated file reddens the check.
    target = tmp_path / "events.md"
    target.write_bytes(target.read_bytes() + b"x")
    assert check_reference(reference_files, tmp_path) == 1

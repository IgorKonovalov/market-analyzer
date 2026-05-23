"""Pin the contract `scripts/dev/_lib/resolve-data-dir.mjs` depends on.

The Node helper spawns `uv run python -c "<oneliner>"` and trims the captured
stdout to obtain the canonical data dir. If the Python side ever starts
emitting a deprecation warning, an extra blank line, or trailing whitespace,
the Node side would silently consume the wrong path — `wait-on` would then sit
on a path that never receives the lockfile, and the dev-loop would hang with
no clear failure. This test asserts the exact stdout shape the wrapper relies
on so any regression there fails loudly under `uv run pytest`.
"""

from __future__ import annotations

import subprocess
import sys

from market_analyser.config import default_app_data_dir

ONELINER = "from market_analyser.config import default_app_data_dir; print(default_app_data_dir())"


def test_oneliner_prints_canonical_data_dir_with_single_trailing_newline() -> None:
    expected = str(default_app_data_dir())
    result = subprocess.run(
        [sys.executable, "-c", ONELINER],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stderr == ""
    assert result.stdout == f"{expected}\n"

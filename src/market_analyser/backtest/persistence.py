"""Persist + read `BacktestResult` artifacts — Plan 0008 phase 3.

`persist(result, runs_dir, repository)` writes three files under
`runs/<run_id>/` and inserts the SQLite summary row in one atomic-ish unit:

1.  Write `spec.json`, `result.json`, `equity_curve.csv` into a hidden
    temporary directory beside `runs/` (same filesystem so the final
    rename is atomic).
2.  Rename the temp dir to `runs/<run_id>/`.
3.  Insert the SQLite summary row.
4.  If the SQLite insert raises, recursively delete `runs/<run_id>/`
    before re-raising — there is no on-disk artifact for a row that
    never got indexed.

A duplicate `run_id` is the principal failure mode: SQLite's PK
constraint catches it on insert, and the cleanup keeps the filesystem
consistent with the index.

The reader (`read_result`) re-merges the three files back into a
`BacktestResult` byte-equivalent to what `persist()` consumed. The disk
artifact is the source of truth — the SQLite row is just an index.
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from market_analyser.backtest.result import (
    BacktestMetrics,
    BacktestResult,
    BacktestRunSummary,
    EquityPoint,
)
from market_analyser.backtest.types import Trade
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)

SPEC_FILENAME: Final[str] = "spec.json"
RESULT_FILENAME: Final[str] = "result.json"
EQUITY_CURVE_FILENAME: Final[str] = "equity_curve.csv"

# The exact set of keys allowed in `spec.json`. Asserted by phase-3 done-when
# §165 — adding a field means updating the schema, the writer, the reader, and
# this set together so divergence is caught at test time.
SPEC_KEYS: Final[frozenset[str]] = frozenset(
    {
        "strategy_id",
        "strategy_version",
        "symbol",
        "timeframe",
        "range_start",
        "range_end",
        "bars_hash",
        "params",
        "costs",
        "initial_capital",
        "sizing",
    },
)


def _build_spec(result: BacktestResult) -> dict[str, Any]:
    """Return the re-runnable spec dict — exactly the keys in `SPEC_KEYS`."""
    return {
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "range_start": result.range_start.isoformat(),
        "range_end": result.range_end.isoformat(),
        "bars_hash": result.bars_hash,
        "params": result.params,
        "costs": result.costs,
        "initial_capital": result.initial_capital,
        "sizing": result.sizing,
    }


def _build_result_json(result: BacktestResult) -> dict[str, Any]:
    """Return the result-side JSON — everything except the equity curve.

    The equity curve goes to its own CSV (one row per bar; large data) so
    `result.json` stays small and grep-friendly.
    """
    dumped = result.model_dump(mode="json")
    dumped.pop("equity_curve", None)
    return dumped


def _write_equity_curve_csv(path: Path, equity_curve: list[EquityPoint]) -> None:
    """Write the equity curve as a two-column CSV: `ts,equity`.

    `ts` serializes to ISO-8601 with the UTC offset; `equity` to Python's
    default float repr. CSV uses `\\n` line terminators so the file is
    byte-identical across platforms.
    """
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["ts", "equity"])
        for point in equity_curve:
            writer.writerow([point.ts.isoformat(), repr(point.equity)])


def _summary_from_result(result: BacktestResult) -> BacktestRunSummary:
    return BacktestRunSummary(
        run_id=result.run_id,
        strategy_id=result.strategy_id,
        strategy_version=result.strategy_version,
        symbol=result.symbol,
        timeframe=result.timeframe,
        range_start=result.range_start,
        range_end=result.range_end,
        total_return=result.metrics.total_return,
        sharpe=result.metrics.sharpe,
        max_drawdown=result.metrics.max_drawdown,
        win_rate=result.metrics.win_rate,
        trade_count=result.metrics.trade_count,
        finished_at=result.finished_at,
        artifact_path=result.run_id,
        engine_version=result.engine_version,
    )


def persist(
    result: BacktestResult,
    runs_dir: Path,
    repository: BacktestRunsRepository,
) -> Path:
    """Persist a `BacktestResult` to disk + SQLite, atomically.

    Returns the final on-disk directory (`runs_dir / run_id`). Raises if
    the directory already exists or if the SQLite insert fails — in
    either case `runs/<run_id>/` is left in a consistent state (either
    absent or matching the pre-call contents).
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    final_dir = runs_dir / result.run_id
    if final_dir.exists():
        raise FileExistsError(
            f"run_id {result.run_id!r} already has an artifact at {final_dir}",
        )

    # Stage all three files in a temp directory on the same filesystem, then
    # promote with one rename. If any write fails, `shutil.rmtree` in the
    # except clause leaves the runs directory untouched.
    temp_dir_path = Path(
        tempfile.mkdtemp(prefix=f".tmp-{result.run_id}-", dir=runs_dir),
    )
    try:
        (temp_dir_path / SPEC_FILENAME).write_text(
            json.dumps(_build_spec(result), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (temp_dir_path / RESULT_FILENAME).write_text(
            json.dumps(_build_result_json(result), indent=2) + "\n",
            encoding="utf-8",
        )
        _write_equity_curve_csv(
            temp_dir_path / EQUITY_CURVE_FILENAME,
            result.equity_curve,
        )
        temp_dir_path.rename(final_dir)
    except Exception:
        shutil.rmtree(temp_dir_path, ignore_errors=True)
        raise

    # Index insert. On failure, drop the artifact directory so the on-disk
    # state matches the SQLite state (no orphaned artifact).
    try:
        repository.insert(_summary_from_result(result))
    except Exception:
        shutil.rmtree(final_dir, ignore_errors=True)
        raise

    return final_dir


def read_result(artifact_dir: Path) -> BacktestResult:
    """Re-merge `spec.json`, `result.json`, and `equity_curve.csv` into a
    `BacktestResult`.

    Raises `FileNotFoundError` if any of the three files is missing —
    callers above (the GET route) translate that to a 404. The reader is
    deliberately strict: a partial artifact is a bug, not a fallback path.
    """
    spec_path = artifact_dir / SPEC_FILENAME
    result_path = artifact_dir / RESULT_FILENAME
    equity_curve_path = artifact_dir / EQUITY_CURVE_FILENAME
    for path in (spec_path, result_path, equity_curve_path):
        if not path.is_file():
            raise FileNotFoundError(f"missing artifact file: {path}")

    result_dict = json.loads(result_path.read_text(encoding="utf-8"))
    equity_curve = _read_equity_curve_csv(equity_curve_path)
    result_dict["equity_curve"] = [point.model_dump(mode="json") for point in equity_curve]
    return BacktestResult.model_validate(result_dict)


def _read_equity_curve_csv(path: Path) -> list[EquityPoint]:
    points: list[EquityPoint] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != ["ts", "equity"]:
            raise ValueError(
                f"equity_curve.csv at {path} has unexpected header {header!r} "
                f"(expected ['ts', 'equity'])",
            )
        for row in reader:
            if len(row) != 2:
                raise ValueError(
                    f"equity_curve.csv at {path} has malformed row {row!r}",
                )
            points.append(EquityPoint(ts=datetime.fromisoformat(row[0]), equity=float(row[1])))
    return points


__all__ = [
    "EQUITY_CURVE_FILENAME",
    "RESULT_FILENAME",
    "SPEC_FILENAME",
    "SPEC_KEYS",
    "BacktestMetrics",
    "Trade",
    "persist",
    "read_result",
]

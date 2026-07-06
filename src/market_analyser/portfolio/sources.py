"""Manual positions-file source (Plan 0041 phase 2; ADR-0042).

The third holdings leg: a **gitignored, user-maintained** positions file for
equities/other — the ADR-0042 Alternative-C answer to TradFi holdings without
a broker adapter. It lives beside the `defi-analyst`'s `positions.yaml` in the
gitignored `positions/` directory, but is **JSON** (`positions/portfolio.json`):
the sidecar parses it in-process, the repo carries no YAML parser, and
hand-editable JSON is the established house surface (`config.json`, ADR-0006).

File shape (schema-validated, unknown keys refused so a typo fails loudly):

    {
      "as_of": "2026-07-01T00:00:00Z",
      "positions": [
        {"symbol": "AAPL", "quantity": 100, "avg_cost": 185.5},
        {"symbol": "GLD", "quantity": 20,
         "as_of": "2026-06-15T00:00:00Z"}
      ]
    }

- The top-level `as_of` is **required**: the file is only as current as the
  user keeps it (ADR-0042's named risk), so its freshness stamp is explicit,
  user-maintained state — never inferred from the filesystem or a wall clock.
  A per-entry `as_of` may override it (a row updated more recently than the
  rest).
- `avg_cost` is optional: an omitted cost basis is honestly-unknown (`None`
  on the `Holding`), never zero (ADR-0036 loud-failure posture).
- A malformed entry raises `ManualPositionsError` naming the bad row (index
  and symbol when present); a **missing file is an empty source, not an
  error** — the user simply has no manual leg yet.

Holdings come out unpriced (`usd_value`/`pricing_source` `None`): valuation
belongs to the aggregator (phase 3), which names its pricing reference per
holding rather than implying a single oracle.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from market_analyser.portfolio.models import Holding

MANUAL_POSITIONS_FILENAME = "portfolio.json"


class ManualPositionsError(ValueError):
    """The manual positions file exists but could not be parsed — malformed
    JSON, a bad header, or an invalid row (named by index and, when present,
    symbol). Distinct from a *missing* file, which is an empty source."""


class ManualPositionEntry(BaseModel):
    """One row of the positions file. `extra="forbid"` so a typo'd key fails
    the row loudly instead of being silently dropped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    quantity: float  # signed: a negative row is a deliberate short/liability
    avg_cost: float | None = None  # per-unit average cost; omitted = unknown, never 0
    as_of: datetime | None = None  # optional per-row override of the file's stamp

    @field_validator("quantity")
    @classmethod
    def _quantity_must_be_finite_and_nonzero(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("quantity must be finite (no NaN/Inf)")
        if v == 0:
            raise ValueError("quantity must be nonzero — remove the row instead")
        return v

    @field_validator("avg_cost")
    @classmethod
    def _avg_cost_must_be_finite_and_non_negative(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not math.isfinite(v):
            raise ValueError("avg_cost must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("avg_cost must be non-negative")
        return v

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return v.astimezone(UTC)


class ManualPositionsFile(BaseModel):
    """The whole-file shape: a required user-maintained freshness stamp plus
    the rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: datetime
    positions: list[ManualPositionEntry]

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return v.astimezone(UTC)


class ManualPositionsSource:
    """Parses `positions/portfolio.json` into validated manual `Holding`s."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_holdings(self) -> list[Holding]:
        """Return the file's rows as `venue="manual"` holdings, file order
        preserved. A missing file returns `[]`; a malformed file raises
        `ManualPositionsError` naming the problem (and the bad row)."""
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ManualPositionsError(
                f"{self._path.name}: not readable as JSON ({err})",
            ) from err
        try:
            parsed = ManualPositionsFile.model_validate(raw)
        except ValidationError as err:
            raise ManualPositionsError(_describe_validation_error(self._path, raw, err)) from err
        return [
            Holding(
                symbol=entry.symbol,
                venue="manual",
                quantity=entry.quantity,
                avg_cost=entry.avg_cost,
                as_of=entry.as_of if entry.as_of is not None else parsed.as_of,
                kind="manual",
            )
            for entry in parsed.positions
        ]


def _describe_validation_error(path: Path, raw: Any, err: ValidationError) -> str:
    """One clear line for the first validation failure, naming the bad row by
    index (and symbol, when the row carries one) — the done-when's 'clear
    validation error naming the bad row'."""
    first = err.errors()[0]
    loc = first["loc"]
    if len(loc) >= 2 and loc[0] == "positions" and isinstance(loc[1], int):
        row = loc[1]
        symbol = _row_symbol(raw, row)
        where = f"row {row}" + (f" ({symbol})" if symbol else "")
        field = ".".join(str(part) for part in loc[2:]) or "entry"
        return f"{path.name}: {where}: {field}: {first['msg']}"
    field = ".".join(str(part) for part in loc) or "file"
    return f"{path.name}: {field}: {first['msg']}"


def _row_symbol(raw: Any, row: int) -> str | None:
    if not isinstance(raw, dict):
        return None
    positions = raw.get("positions")
    if not isinstance(positions, list) or row >= len(positions):
        return None
    entry = positions[row]
    if isinstance(entry, dict) and isinstance(entry.get("symbol"), str) and entry["symbol"]:
        symbol: str = entry["symbol"]
        return symbol
    return None


__all__ = [
    "MANUAL_POSITIONS_FILENAME",
    "ManualPositionEntry",
    "ManualPositionsError",
    "ManualPositionsFile",
    "ManualPositionsSource",
]

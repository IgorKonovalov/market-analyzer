"""Canonical bar serialization + SHA256.

Per [ADR-0018](../../../docs/architecture/adrs/0018-backtest-result-schema.md):
`bars_hash` makes silent data drift loud. Two backtests with the same spec
but different cached bars will have different `bars_hash` values, and the
divergence is visible on inspection.

Canonical form: for each `Bar` in input order, write
``f'{event_ts.isoformat()}|{open!r}|{high!r}|{low!r}|{close!r}|{volume!r}'``
joined by ``\\n``, encode UTF-8, SHA256. `repr(float)` is deterministic
under CPython 3.10+ (PEP 3101); the repo is CPython-only (uv pins the
interpreter), so this is sound. `datetime.isoformat()` is deterministic
for tz-aware UTC datetimes, and `Bar` boundary-validates `event_ts` into
UTC at construction.

The empty bar list hashes to SHA256 of the empty UTF-8 buffer — a stable
non-zero string callers can rely on, not a special-case.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from market_analyser.data.types import Bar


def bars_hash(bars: Sequence[Bar]) -> str:
    """Return the SHA256 of the canonical serialization of `bars`."""

    lines = [
        f"{bar.event_ts.isoformat()}|{bar.open!r}|{bar.high!r}|{bar.low!r}|{bar.close!r}|{bar.volume!r}"
        for bar in bars
    ]
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["bars_hash"]

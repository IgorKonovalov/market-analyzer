"""In-memory, per-symbol mirror of the renderer's user drawings (Plan 0104, ADR-0099).

The read half of the drawing read-back loop. The renderer OWNS the user drawing
set (`localStorage['ma.userDrawings']`, ADR-0091) and PUTs its full per-symbol
set here on every mutation and on chart load; the `get_chart_drawings` MCP tool
reads this shadow so the agent can finally *see* what the user drew ("what do you
think about this resistance?").

Ownership never moves (ADR-0099): this is a READ-ONLY shadow — ephemeral (cleared
on sidecar restart, no persistence, no migration) and non-authoritative. There is
no write path from here back to `ma.userDrawings`. `synced_at` is `None` until the
first sync for a symbol, so "no viewer running / never synced" stays honestly
distinct from "synced, but the user has no drawings".

Placed in a neutral top-level core (the ADR-0065 `ui_events` precedent) rather
than under `api/`: it holds domain value-types (`DrawingSpec` from `events/`) and
is shared in-memory state the api layer both writes (the `PUT /user_drawings`
route) and reads (the MCP tool), so keeping it out of the transport layer means a
future consumer need not depend up into `api/`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from market_analyser.events.drawing_types import DrawingSpec


class UserDrawingsSnapshot(BaseModel):
    """One symbol's mirrored user drawing set plus its freshness.

    `synced_at` is `None` exactly when nothing has synced for `symbol` since the
    sidecar booted — the honest never-synced signal the agent must read before
    trusting an empty `drawings` list (ADR-0099 staleness mitigation)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    drawings: list[DrawingSpec]
    synced_at: datetime | None


class UserDrawingsMirror:
    """Per-symbol shadow of the renderer's user drawings — declarative replace in,
    snapshot out. In-memory and ephemeral; the renderer stays the source of truth."""

    def __init__(self) -> None:
        self._by_symbol: dict[str, UserDrawingsSnapshot] = {}

    def replace(
        self,
        symbol: str,
        drawings: Sequence[DrawingSpec],
        *,
        synced_at: datetime,
    ) -> UserDrawingsSnapshot:
        """Declaratively replace `symbol`'s mirrored set (ADR-0099) — the whole
        set each sync, not a delta. `synced_at` is stamped by the caller (the PUT
        route) so this store stays clock-free and testable."""
        snapshot = UserDrawingsSnapshot(
            symbol=symbol,
            drawings=list(drawings),
            synced_at=synced_at,
        )
        self._by_symbol[symbol] = snapshot
        return snapshot

    def snapshot(self, symbol: str) -> UserDrawingsSnapshot:
        """`symbol`'s mirrored set + `synced_at`, or an empty never-synced
        snapshot (`synced_at=None`, no drawings) when nothing has synced for it."""
        existing = self._by_symbol.get(symbol)
        if existing is not None:
            return existing
        return UserDrawingsSnapshot(symbol=symbol, drawings=[], synced_at=None)


__all__ = ["UserDrawingsMirror", "UserDrawingsSnapshot"]

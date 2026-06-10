"""Support/resistance primitives: confirmed swing pivots + clustered levels
(Plan 0051, ADR-0023).

`swing_pivots` is the public, reusable extraction of the swing-pivot logic that
previously lived module-private inside `snapshot.py::_support_resistance`. It is
the shared foundation classical-pattern detection (Plan 0052) consumes, so its
signature and semantics are deliberately small and stable:

    swing_pivots(bars, left=3, right=3) -> list[Pivot]

A bar `j` is a `high` pivot when its high strictly exceeds the highs of the
`left` bars before it AND the `right` bars after it; a `low` pivot is the mirror
on lows. Only *confirmed* pivots are returned: bar `j` needs all `right` bars of
right-context inside the input series, so a pivot forming at bar `j` is first
knowable at bar `j + right` and `swing_pivots(bars[: k + 1])` returns exactly
the full-series pivots with `bar_index <= k - right`. Appending future bars
never changes or removes an already-confirmed pivot — the anti-lookahead
property pinned by the truncation-invariance test in
`tests/analysis/test_levels.py`.

`support_resistance_levels` (phase 3) builds on the pivots: nearby pivots of one
role cluster into a zone, and each zone is strength-ranked by a documented blend
of touch count and the volume-by-price mass inside the zone's band (the phase-2
`volume_profile` primitive) — a zone that also absorbed heavy traded volume is a
stronger level than an equally-touched thin one.

Pure, trailing, deterministic, no pandas/numpy (ADR-0023): `swing_pivots` output
order is by `bar_index` ascending, with a same-bar `high` pivot before the `low`
one; `support_resistance_levels` output order is strength descending with
deterministic tie-breaks (price ascending, then role).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from market_analyser.analysis.types import Level, Pivot
from market_analyser.analysis.volume_profile import volume_profile
from market_analyser.data.types import Bar

# Default pivot wings: a 3-left/3-right window matches the 3-bar centred window
# the snapshot's private helper has always used (SR_PIVOT_WINDOW).
DEFAULT_PIVOT_LEFT = 3
DEFAULT_PIVOT_RIGHT = 3

# --- Level clustering + strength tunables (Plan 0051 phase 3) ---------------- #
# Pivots within this fraction of the cluster's anchor price (its lowest pivot)
# merge into one zone; the same fraction sets the half-width of the band the
# volume-by-price profile is read over for the zone's volume_at_level.
CLUSTER_TOLERANCE_PCT = 0.005  # 0.5% of price
# Strength formula (documented, deterministic):
#   strength = TOUCH_WEIGHT * (touches / max_touches)
#            + VOLUME_WEIGHT * (volume_at_level / max_volume_at_level)
# with both maxima taken over the full candidate-level set (before the per-role
# cap), and the volume term 0 when no level has any volume mass.
TOUCH_STRENGTH_WEIGHT = 0.5
VOLUME_STRENGTH_WEIGHT = 0.5
DEFAULT_MAX_LEVELS = 5  # keep at most this many strongest levels per role


def swing_pivots(
    bars: Sequence[Bar],
    left: int = DEFAULT_PIVOT_LEFT,
    right: int = DEFAULT_PIVOT_RIGHT,
) -> list[Pivot]:
    """Confirmed swing pivots over `bars`, ordered by `bar_index` ascending.

    A `high` pivot at `j` has `bars[j].high` strictly above every high in the
    `left` bars before and the `right` bars after it; a `low` pivot mirrors on
    lows. A bar can be both (a `high` pivot is emitted before the same bar's
    `low` pivot). Only pivots with a full `right`-bar window inside the series
    are confirmed — no future bar beyond the series end is read.
    """

    if left < 1:
        raise ValueError(f"left must be >= 1, got {left}")
    if right < 1:
        raise ValueError(f"right must be >= 1, got {right}")
    n = len(bars)
    pivots: list[Pivot] = []
    for j in range(left, n - right):
        neighbours = [*bars[j - left : j], *bars[j + 1 : j + right + 1]]
        if bars[j].high > max(b.high for b in neighbours):  # strict local max
            pivots.append(Pivot(bar_index=j, ts=bars[j].event_ts, price=bars[j].high, kind="high"))
        if bars[j].low < min(b.low for b in neighbours):  # strict local min
            pivots.append(Pivot(bar_index=j, ts=bars[j].event_ts, price=bars[j].low, kind="low"))
    return pivots


def _cluster_pivots(
    pivots: Sequence[Pivot],
    cluster_tolerance_pct: float,
) -> list[list[Pivot]]:
    """Greedy single-pass price clustering of one role's pivots.

    Pivots are visited in ascending price order (ties broken by `bar_index` for
    determinism); a pivot joins the current cluster while its price is within
    `cluster_tolerance_pct` of the cluster's anchor (its lowest pivot price),
    otherwise it starts a new cluster."""

    ordered = sorted(pivots, key=lambda p: (p.price, p.bar_index))
    clusters: list[list[Pivot]] = []
    for pivot in ordered:
        if clusters and pivot.price <= clusters[-1][0].price * (1.0 + cluster_tolerance_pct):
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])
    return clusters


def support_resistance_levels(
    bars: Sequence[Bar],
    left: int = DEFAULT_PIVOT_LEFT,
    right: int = DEFAULT_PIVOT_RIGHT,
    cluster_tolerance_pct: float = CLUSTER_TOLERANCE_PCT,
    max_levels: int = DEFAULT_MAX_LEVELS,
) -> list[Level]:
    """Clustered, strength-ranked support/resistance zones over `bars`.

    Pipeline: confirmed `swing_pivots` (low pivots -> support candidates, high
    pivots -> resistance candidates), per-role greedy price clustering within
    `cluster_tolerance_pct` of each cluster's anchor (its lowest pivot), then
    strength ranking by the documented touch+volume blend (module constants).
    The volume term reads the phase-2 trailing volume-by-price profile over the
    band `cluster price * cluster_tolerance_pct`, so the whole computation sees
    only `bars[0..=last]` — trailing, no lookahead (ADR-0023).

    At most `max_levels` strongest levels per role survive; the returned list is
    ordered strength descending (ties: price ascending, then role). Empty input
    or a series with no confirmed pivots returns `[]`.
    """

    if cluster_tolerance_pct <= 0.0:
        raise ValueError(f"cluster_tolerance_pct must be > 0, got {cluster_tolerance_pct}")
    if max_levels < 1:
        raise ValueError(f"max_levels must be >= 1, got {max_levels}")

    pivots = swing_pivots(bars, left=left, right=right)
    if not pivots:
        return []
    profile = volume_profile(bars)

    # Candidate zones for BOTH roles first: the strength normalisation maxima
    # are taken over the full candidate set, before the per-role cap.
    candidates: list[tuple[Literal["support", "resistance"], list[Pivot]]] = []
    support_pivots = [p for p in pivots if p.kind == "low"]
    resistance_pivots = [p for p in pivots if p.kind == "high"]
    candidates.extend(
        ("support", c) for c in _cluster_pivots(support_pivots, cluster_tolerance_pct)
    )
    candidates.extend(
        ("resistance", c) for c in _cluster_pivots(resistance_pivots, cluster_tolerance_pct)
    )

    zones: list[tuple[Literal["support", "resistance"], float, int, float, datetime, datetime]] = []
    for role, cluster in candidates:
        price = sum(p.price for p in cluster) / len(cluster)
        volume_at_level = profile.volume_at_price(price, price * cluster_tolerance_pct)
        zones.append(
            (
                role,
                price,
                len(cluster),
                volume_at_level,
                min(p.ts for p in cluster),
                max(p.ts for p in cluster),
            )
        )

    max_touches = max(touches for _, _, touches, _, _, _ in zones)
    max_volume = max(volume for _, _, _, volume, _, _ in zones)
    levels = [
        Level(
            price=price,
            role=role,
            touches=touches,
            volume_at_level=volume,
            strength=(
                TOUCH_STRENGTH_WEIGHT * (touches / max_touches)
                + VOLUME_STRENGTH_WEIGHT * (volume / max_volume if max_volume > 0.0 else 0.0)
            ),
            first_ts=first_ts,
            last_ts=last_ts,
        )
        for role, price, touches, volume, first_ts, last_ts in zones
    ]

    kept: list[Level] = []
    for role in ("support", "resistance"):
        role_levels = sorted(
            (level for level in levels if level.role == role),
            key=lambda level: (-level.strength, level.price),
        )
        kept.extend(role_levels[:max_levels])
    kept.sort(key=lambda level: (-level.strength, level.price, level.role))
    return kept


__all__ = [
    "CLUSTER_TOLERANCE_PCT",
    "DEFAULT_MAX_LEVELS",
    "DEFAULT_PIVOT_LEFT",
    "DEFAULT_PIVOT_RIGHT",
    "TOUCH_STRENGTH_WEIGHT",
    "VOLUME_STRENGTH_WEIGHT",
    "support_resistance_levels",
    "swing_pivots",
]

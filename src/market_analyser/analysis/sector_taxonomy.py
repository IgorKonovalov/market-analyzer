"""Versioned crypto sector taxonomy (Plan 0102 phase 1, ADR-0097).

Crypto has no canonical sector index — unlike equities' SPDR sector ETFs there is no
single fetchable price per "sector". So we *define* the sectors ourselves as versioned
config data (this module) and synthesize sector momentum from constituent OHLCV we
already fetch (`analysis/sectors.py`). The taxonomy is a deliberately maintained,
opinionated artifact: it ages as the market moves, and each revision is a **config
edit here** — never a change to the analysis logic that consumes it (ADR-0097).

The shipped taxonomy is the pinned initial set (`CRYPTO_SECTOR_TAXONOMY`), built and
validated at import time through `load_taxonomy` so a malformed basket can never reach
a caller. Constituents are USD-native symbols (`<TICKER>-USD`) priced through the
existing Coinbase / Binance / Yahoo sources (ADR-0076 / ADR-0052 / ADR-0069) — no new
data source. Membership may overlap (a token can be both "AI" and "DePIN"); overlap is
allowed and intentional, since momentum is computed per basket (ADR-0097).

A sector needs at least `MIN_PRICED_TO_RANK` priced constituents to be ranked rather
than reported incomplete — the skip-and-flag floor that stops a single illiquid or
delisted constituent from silently defining a sector's read.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator

# The skip-and-flag floor (ADR-0097): a sector with fewer than this many *priced*
# constituents is reported `complete=False` rather than ranked, so a thin or stale
# basket cannot masquerade as a confident sector read.
MIN_PRICED_TO_RANK: Final = 2


class Sector(BaseModel):
    """One crypto sector and its representative constituent basket (ADR-0097).

    `constituents` are USD-native symbols (`<TICKER>-USD`), unique within the basket
    and non-empty. Equal-weighted — every constituent is one voter (cap-weighting is a
    future refinement, ADR-0097 alt B), so the basket is chosen to be representative,
    not exhaustive. A token may appear in more than one sector's basket across the
    taxonomy; that cross-membership is intentional and allowed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    constituents: tuple[str, ...]

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sector name must be non-empty")
        return v

    @field_validator("constituents")
    @classmethod
    def _basket_well_formed(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("sector basket must be non-empty")
        for symbol in v:
            if not symbol.strip():
                raise ValueError("constituent symbol must be non-empty")
        if len(set(v)) != len(v):
            raise ValueError(f"sector basket has duplicate constituents: {v}")
        return v


class SectorTaxonomy(BaseModel):
    """A versioned set of crypto sectors — the single source of truth a rotation read
    ranks (ADR-0097). `version` dates the artifact so a revision is auditable; `sectors`
    is the ordered, non-empty sector set with unique names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    sectors: tuple[Sector, ...]

    @field_validator("version")
    @classmethod
    def _version_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("taxonomy version must be non-empty")
        return v

    @field_validator("sectors")
    @classmethod
    def _sectors_well_formed(cls, v: tuple[Sector, ...]) -> tuple[Sector, ...]:
        if not v:
            raise ValueError("taxonomy must define at least one sector")
        names = [s.name for s in v]
        if len(set(names)) != len(names):
            raise ValueError(f"taxonomy has duplicate sector names: {names}")
        return v


def load_taxonomy(version: str, sectors: tuple[tuple[str, tuple[str, ...]], ...]) -> SectorTaxonomy:
    """Build a validated `SectorTaxonomy` from raw `(name, constituents)` pairs.

    The one construction seam (used for the shipped taxonomy and by tests): every
    invariant — non-empty version, at least one sector, unique sector names, a
    non-empty duplicate-free basket per sector — is enforced by the pydantic models,
    so a malformed input raises `ValueError`/`ValidationError` at build time rather
    than surfacing a broken read to a caller."""

    return SectorTaxonomy(
        version=version,
        sectors=tuple(
            Sector(name=name, constituents=constituents) for name, constituents in sectors
        ),
    )


# --------------------------------------------------------------------------- #
# The shipped taxonomy — the pinned v1 set (ADR-0097; Plan 0102 phase 1).       #
#                                                                               #
# A maintained artifact: revise the sector list / constituents here as the      #
# market moves (today's "AI" sector barely existed two cycles ago). Constituents #
# are liquid, representative, USD-native names; a delisted/illiquid one is        #
# skip-and-flagged at read time, so a stale entry degrades gracefully.           #
# --------------------------------------------------------------------------- #

_SHIPPED_VERSION: Final = "2026-07-16"

_SHIPPED_SECTORS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("Layer-1", ("BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "ADA-USD", "SUI-USD")),
    ("Layer-2", ("ARB-USD", "OP-USD", "MATIC-USD", "IMX-USD", "STRK-USD")),
    ("DeFi", ("UNI-USD", "AAVE-USD", "LDO-USD", "MKR-USD", "CRV-USD")),
    ("Memecoins", ("DOGE-USD", "SHIB-USD", "PEPE-USD", "WIF-USD", "BONK-USD")),
    ("AI", ("RENDER-USD", "FET-USD", "TAO-USD", "GRT-USD")),
    ("DePIN", ("HNT-USD", "IOTX-USD", "FIL-USD", "RENDER-USD")),
)

# Built + validated at import: an ill-formed edit fails fast on load, never at a call.
CRYPTO_SECTOR_TAXONOMY: Final = load_taxonomy(_SHIPPED_VERSION, _SHIPPED_SECTORS)


__all__ = [
    "CRYPTO_SECTOR_TAXONOMY",
    "MIN_PRICED_TO_RANK",
    "Sector",
    "SectorTaxonomy",
    "load_taxonomy",
]

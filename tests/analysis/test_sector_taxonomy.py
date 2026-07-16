"""Done-when for Plan 0102 phase 1: the versioned crypto sector taxonomy + loader (ADR-0097).

Pins that the loader parses the shipped taxonomy into typed sectors, rejects a
malformed / empty basket (and the other structural invariants), and fixes the
≥N-priced floor that decides whether a sector is ranked or reported incomplete.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from market_analyser.analysis.sector_taxonomy import (
    CRYPTO_SECTOR_TAXONOMY,
    MIN_PRICED_TO_RANK,
    Sector,
    SectorTaxonomy,
    load_taxonomy,
)


def test_shipped_taxonomy_parses_into_typed_sectors() -> None:
    tax = CRYPTO_SECTOR_TAXONOMY
    assert isinstance(tax, SectorTaxonomy)
    assert tax.version  # dated, non-empty artifact
    assert len(tax.sectors) >= 6  # the pinned v1 set (ADR-0097: ~6-8 sectors)
    for sector in tax.sectors:
        assert isinstance(sector, Sector)
        assert sector.constituents  # non-empty basket
        # USD-native symbols (ADR-0076): every constituent is a `<TICKER>-USD` pair.
        for symbol in sector.constituents:
            assert symbol.endswith("-USD"), symbol
    # Sector names are unique across the taxonomy.
    names = [s.name for s in tax.sectors]
    assert len(set(names)) == len(names)


def test_overlapping_membership_is_allowed() -> None:
    """A token may appear in more than one sector's basket (ADR-0097) — RENDER-USD
    sits in both AI and DePIN in the shipped set."""

    membership = {s.name: set(s.constituents) for s in CRYPTO_SECTOR_TAXONOMY.sectors}
    assert "RENDER-USD" in membership["AI"]
    assert "RENDER-USD" in membership["DePIN"]


def test_empty_basket_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Sector(name="Empty", constituents=())
    with pytest.raises(ValidationError):
        load_taxonomy("v", (("Empty", ()),))


def test_blank_constituent_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Sector(name="Bad", constituents=("BTC-USD", "  "))


def test_duplicate_constituent_within_a_basket_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Sector(name="Dup", constituents=("BTC-USD", "BTC-USD"))


def test_blank_sector_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Sector(name="   ", constituents=("BTC-USD",))


def test_empty_taxonomy_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SectorTaxonomy(version="v", sectors=())
    with pytest.raises(ValidationError):
        load_taxonomy("v", ())


def test_blank_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SectorTaxonomy(
            version="  ",
            sectors=(Sector(name="L1", constituents=("BTC-USD",)),),
        )


def test_duplicate_sector_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        load_taxonomy(
            "v",
            (
                ("L1", ("BTC-USD",)),
                ("L1", ("ETH-USD",)),
            ),
        )


def test_loader_round_trips_a_wellformed_taxonomy() -> None:
    tax = load_taxonomy(
        "test-2026",
        (
            ("Alpha", ("A-USD", "B-USD")),
            ("Beta", ("C-USD",)),
        ),
    )
    assert tax.version == "test-2026"
    assert [s.name for s in tax.sectors] == ["Alpha", "Beta"]
    assert tax.sectors[0].constituents == ("A-USD", "B-USD")


def test_min_priced_floor_is_pinned() -> None:
    """The ≥N-priced-to-rank floor is exactly 2: a sector needs at least two priced
    constituents to be ranked, otherwise it is reported incomplete (ADR-0097)."""

    assert MIN_PRICED_TO_RANK == 2

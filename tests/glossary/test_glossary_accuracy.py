"""Plan 0065 phase 3 done-when: cross-language glossary accuracy.

Reads the renderer's shared ``glossary.json`` by repo-relative path (no TS /
renderer dependency) and structurally ties it to the computing Python code, so
the tooltip copy cannot silently drift from what it explains:

  - every ``indicator``-category key equals, bidirectionally, the union of the
    frozen ``FEATURE_NAMES`` / ``FEATURE_NAMES_V2`` / ``FEATURE_NAMES_V2_DEEP``
    tuples -- adding a feature, deleting a feature's entry, or inventing a
    phantom indicator key each fail here until the glossary catches up;
  - the ``overlay``-category vocabulary (chart-legend copy: ema / sma /
    supertrend) is deliberately DISJOINT from ``indicator`` and excluded from the
    feature-name pin -- it describes chart overlays, not model features;
  - each formula-bearing term (one carrying a ``formulaAnchor``) references the
    canonical constant it explains, imported here from its owning module --
    conviction to ``SHARPE_FULL_CREDIT`` (``advisor/fusion.py``), edge_strength
    to ``EDGE_MARGIN_THRESHOLD`` (``api/mcp_tools/forecast.py``) -- so renaming a
    constant (the import breaks) or re-pointing an anchor (the symbol is no
    longer named in the prose) fails the test.

The glossary is a renderer build-time asset (ADR-0046: never on the wire); this
test is the one place the two languages meet, by reading the same file.
"""

from __future__ import annotations

import json
from pathlib import Path

from market_analyser.advisor.fusion import SHARPE_FULL_CREDIT
from market_analyser.api.mcp_tools.forecast import EDGE_MARGIN_THRESHOLD
from market_analyser.forecast.features import (
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_NAMES_V2_DEEP,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GLOSSARY_PATH = _REPO_ROOT / "desktop" / "renderer" / "glossary" / "glossary.json"


def _load_glossary() -> dict[str, dict[str, str]]:
    with _GLOSSARY_PATH.open(encoding="utf-8") as handle:
        data: dict[str, dict[str, str]] = json.load(handle)
    return data


GLOSSARY = _load_glossary()

# Each glossary ``formulaAnchor`` -> the canonical constant it explains, by symbol
# name. The constants are imported above, so a removed/renamed export breaks this
# module at collection time; the symbol name is what the term's ``howComputed``
# must reference, so a re-pointed anchor (or a formula that stops naming its
# constant) fails the linkage assertion below.
_ANCHOR_TO_CONSTANT: dict[str, str] = {
    "conviction_mapping": "SHARPE_FULL_CREDIT",
    "edge_margin_threshold": "EDGE_MARGIN_THRESHOLD",
}


def _feature_name_union() -> set[str]:
    return set(FEATURE_NAMES) | set(FEATURE_NAMES_V2) | set(FEATURE_NAMES_V2_DEEP)


def _keys_in_category(category: str) -> set[str]:
    return {key for key, record in GLOSSARY.items() if record.get("category") == category}


def test_glossary_parses_and_every_record_carries_both_hats() -> None:
    """A smoke guard: a malformed record fails here, not cryptically downstream."""
    assert GLOSSARY, "glossary.json is empty"
    for key, record in GLOSSARY.items():
        for field in ("term", "category", "howComputed", "whatItMeans"):
            assert record.get(field), f"{key}: missing or empty {field}"


def test_indicator_keys_equal_the_frozen_feature_name_union() -> None:
    """Bidirectional: the ``indicator`` glossary keys ARE the FEATURE_NAMES union
    -- no feature without an entry, no indicator key that is not a feature."""
    feature_union = _feature_name_union()
    indicator_keys = _keys_in_category("indicator")
    missing = feature_union - indicator_keys
    extra = indicator_keys - feature_union
    assert not missing, f"features with no glossary entry: {sorted(missing)}"
    assert not extra, f"indicator glossary keys that are not FEATURE_NAMES: {sorted(extra)}"


def test_overlay_vocabulary_is_disjoint_from_indicator_and_not_a_feature() -> None:
    """The chart-legend ``overlay`` vocabulary is distinct from ``indicator`` and
    never demanded in FEATURE_NAMES -- the exclusion the indicator pin relies on."""
    overlay_keys = _keys_in_category("overlay")
    indicator_keys = _keys_in_category("indicator")
    assert overlay_keys, "expected a non-empty overlay-category vocabulary"
    assert overlay_keys.isdisjoint(indicator_keys), (
        f"overlay/indicator keys overlap: {sorted(overlay_keys & indicator_keys)}"
    )
    assert overlay_keys.isdisjoint(_feature_name_union()), (
        f"overlay keys must not be FEATURE_NAMES: {sorted(overlay_keys & _feature_name_union())}"
    )


def test_canonical_constants_are_exported_and_numeric() -> None:
    """The formula anchors point at real, exported constants; a removed/renamed
    export breaks the import at the top of this module and errors this test."""
    assert isinstance(SHARPE_FULL_CREDIT, (int, float))
    assert isinstance(EDGE_MARGIN_THRESHOLD, (int, float))


def test_formula_anchored_terms_reference_their_canonical_constant() -> None:
    """Each ``formulaAnchor`` maps to a known canonical constant, and the term's
    ``howComputed`` names that constant's symbol -- so re-pointing an anchor to a
    different constant, or a formula that stops naming it, fails here."""
    used_anchors: set[str] = set()
    for key, record in GLOSSARY.items():
        anchor = record.get("formulaAnchor")
        if anchor is None:
            continue
        assert anchor in _ANCHOR_TO_CONSTANT, f"{key}: unknown formulaAnchor {anchor!r}"
        symbol = _ANCHOR_TO_CONSTANT[anchor]
        assert symbol in record["howComputed"], (
            f"{key}: howComputed does not reference its canonical constant {symbol}"
        )
        used_anchors.add(anchor)

    # Bidirectional: every registered anchor is actually used by a glossary term,
    # so a stale registry entry cannot hide a dropped anchor.
    assert used_anchors == set(_ANCHOR_TO_CONSTANT), (
        f"anchor registry and glossary anchors disagree: "
        f"registry={sorted(_ANCHOR_TO_CONSTANT)}, glossary={sorted(used_anchors)}"
    )

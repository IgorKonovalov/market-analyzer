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
from typing import Any

from market_analyser.advisor.fusion import SHARPE_FULL_CREDIT
from market_analyser.analysis.chart_patterns import CHART_PATTERNS
from market_analyser.api.mcp_tools.forecast import EDGE_MARGIN_THRESHOLD
from market_analyser.forecast.features import (
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_NAMES_V2_DEEP,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GLOSSARY_PATH = _REPO_ROOT / "desktop" / "renderer" / "glossary" / "glossary.json"


# Plan 0069 phase 3 made the three prose fields (term / howComputed /
# whatItMeans) locale-keyed `{ "en": ..., "ru"?: ... }`; category / formulaAnchor
# stay flat strings. So a record's values are heterogeneous (str | dict).
def _load_glossary() -> dict[str, dict[str, Any]]:
    with _GLOSSARY_PATH.open(encoding="utf-8") as handle:
        data: dict[str, dict[str, Any]] = json.load(handle)
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
        assert record.get("category"), f"{key}: missing or empty category"
        for field in ("term", "howComputed", "whatItMeans"):
            prose = record.get(field)
            assert isinstance(prose, dict), f"{key}: {field} must be a localized object"
            assert prose.get("en"), f"{key}: missing or empty {field}.en"


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


def test_chart_pattern_keys_equal_the_detector_pattern_names() -> None:
    """Bidirectional (Plan 0105 phase 1): the ``chart_pattern`` glossary keys ARE
    the detector's canonical ``CHART_PATTERNS`` tuple -- adding a detector
    pattern, deleting its entry, or inventing a phantom pattern key each fail
    here until the glossary catches up."""
    detector_names = set(CHART_PATTERNS)
    chart_pattern_keys = _keys_in_category("chart_pattern")
    missing = detector_names - chart_pattern_keys
    extra = chart_pattern_keys - detector_names
    assert not missing, f"detector patterns with no glossary entry: {sorted(missing)}"
    assert not extra, f"chart_pattern glossary keys the detector does not emit: {sorted(extra)}"


# The Plan 0105 phase-1 legend keys: overlay/summary terms the chart legend emits
# that phase 2 wires via `glossaryKey`. Pinned so a rename or deletion on either
# side surfaces here rather than as a silently-inert legend row.
_LEGEND_SUMMARY_KEYS = {"ichimoku", "obv", "rsi", "structure"}


def test_legend_summary_keys_are_present_overlay_terms() -> None:
    """Each Plan 0105 legend key exists and stays in the ``overlay`` vocabulary
    (so the indicator/feature-name pin above keeps excluding them)."""
    overlay_keys = _keys_in_category("overlay")
    missing = _LEGEND_SUMMARY_KEYS - set(GLOSSARY)
    assert not missing, f"legend keys with no glossary entry: {sorted(missing)}"
    misfiled = _LEGEND_SUMMARY_KEYS - overlay_keys
    assert not misfiled, f"legend keys not in the overlay category: {sorted(misfiled)}"


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
        # The canonical constant is named in the authored English prose (`ru` is a
        # translation and need not carry the English symbol name).
        assert symbol in record["howComputed"]["en"], (
            f"{key}: howComputed does not reference its canonical constant {symbol}"
        )
        used_anchors.add(anchor)

    # Bidirectional: every registered anchor is actually used by a glossary term,
    # so a stale registry entry cannot hide a dropped anchor.
    assert used_anchors == set(_ANCHOR_TO_CONSTANT), (
        f"anchor registry and glossary anchors disagree: "
        f"registry={sorted(_ANCHOR_TO_CONSTANT)}, glossary={sorted(used_anchors)}"
    )

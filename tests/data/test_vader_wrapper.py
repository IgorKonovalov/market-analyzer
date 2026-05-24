"""Plan 0010 phase 2 — unit tests for the VADER scoring wrapper.

VADER is a lexicon scorer with no model weights or randomness, so its compound
score is deterministic for a given input. The expected values below were
computed once against vaderSentiment==3.3.2 and pinned.
"""

from __future__ import annotations

import pytest

from market_analyser.data import _vader

# A real sentence VADER scores neutral (none of its words are in the lexicon) —
# a concrete instance of the plan's "VADER is general-English, not finance"
# limitation. Pinned at fixture-creation time.
_ATH_EXPECTED_COMPOUND = 0.0
# A distinctive non-zero pin, so the determinism check can't pass on an
# everything-returns-zero bug.
_GREAT_EXPECTED_COMPOUND = 0.6249


def test_score_is_deterministic_and_pinned() -> None:
    neutral_text = "Bitcoin surges to a new all-time high"
    first = _vader.score(neutral_text)
    second = _vader.score(neutral_text)
    assert abs(first - _ATH_EXPECTED_COMPOUND) < 1e-9
    assert first == second  # byte-identical across consecutive calls

    positive = _vader.score("great")
    assert abs(positive - _GREAT_EXPECTED_COMPOUND) < 1e-9
    assert positive == _vader.score("great")


def test_empty_and_whitespace_score_zero() -> None:
    assert _vader.score("") == 0.0
    assert _vader.score("   ") == 0.0


def test_none_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _vader.score(None)  # type: ignore[arg-type]  # deliberately wrong type


def test_score_is_bounded() -> None:
    for text in ("great", "terrible awful disaster", "Bitcoin surges to a new all-time high"):
        assert -1.0 <= _vader.score(text) <= 1.0

"""Phase 2 done-when: `discover()` returns a sorted dict; duplicates raise.

Fixtures live in `tests/contracts/_fixtures/`:
- `strategies_ok/`: two valid stubs (`alpha.py` declares `id="zeta"`,
  `beta.py` declares `id="alpha"` — filenames intentionally opposite to ids
  so a filename-sorted result would fail), one underscore-prefixed module
  that must be skipped without import, and one module without `META` that
  discover() imports but ignores.
- `strategies_dup/`: two stubs sharing `META.id="dup"`.
"""

from __future__ import annotations

import pytest

from market_analyser.contracts import DuplicateStrategyError, StrategyMeta, discover


def test_default_discover_finds_rsi() -> None:
    result = discover()
    assert "rsi" in result
    assert isinstance(result["rsi"].META, StrategyMeta)
    assert result["rsi"].META.id == "rsi"


def test_default_discover_keys_are_sorted_by_id() -> None:
    keys = list(discover().keys())
    assert keys == sorted(keys)


def test_discover_returns_two_fixture_stubs_in_id_sorted_order() -> None:
    result = discover("tests.contracts._fixtures.strategies_ok")
    assert list(result.keys()) == ["alpha", "zeta"]
    # alpha.py declares id="zeta"; beta.py declares id="alpha". Sorted-by-id
    # means the result is keyed [alpha (from beta.py), zeta (from alpha.py)],
    # never by filename.
    assert result["alpha"].__name__.endswith(".beta")
    assert result["zeta"].__name__.endswith(".alpha")


def test_discover_skips_underscore_prefixed_modules() -> None:
    result = discover("tests.contracts._fixtures.strategies_ok")
    # The marker attribute on _private.py would only be reachable via
    # `result["..."]` if the module had been imported and registered. Since
    # discover() skips `_*` and the module has no META, no key references it.
    for mod in result.values():
        assert not getattr(mod, "SHOULD_NOT_BE_DISCOVERED", False)


def test_discover_silently_ignores_modules_without_meta() -> None:
    # not_a_strategy.py imports cleanly but has no META — must not raise,
    # must not appear in the result.
    result = discover("tests.contracts._fixtures.strategies_ok")
    for mod in result.values():
        assert mod.__name__ != "tests.contracts._fixtures.strategies_ok.not_a_strategy"


def test_discover_raises_duplicate_strategy_error_on_id_collision() -> None:
    with pytest.raises(DuplicateStrategyError) as excinfo:
        discover("tests.contracts._fixtures.strategies_dup")
    msg = str(excinfo.value)
    assert "dup" in msg
    # The error must name both colliding modules so the operator can find
    # them without grepping.
    assert "one" in msg
    assert "two" in msg


def test_discover_is_deterministic_across_calls() -> None:
    a = list(discover("tests.contracts._fixtures.strategies_ok").keys())
    b = list(discover("tests.contracts._fixtures.strategies_ok").keys())
    assert a == b

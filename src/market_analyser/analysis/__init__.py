"""Technical-analysis surface (Plan 0018, ADR-0023).

The canonical home for pure, trailing, deterministic technical-analysis math:
indicators (`indicators.py`), candlestick patterns (`patterns.py`), and a composed
condition snapshot (`snapshot.py`). Every function here is pure — same bars in,
same output out, no I/O, no module-level mutable state, no wall-clock, no RNG —
and trailing: `result[i]` is computed only from `bars[0..=i]`, with `None` for
leading bars where the value is mathematically undefined. The anti-lookahead
invariant is enforced at this layer so callers inherit it for free.
"""

from __future__ import annotations

from market_analyser.analysis.patterns import detect_patterns, resolve_span
from market_analyser.analysis.types import PatternHit

__all__ = ["PatternHit", "detect_patterns", "resolve_span"]

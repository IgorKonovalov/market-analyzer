"""Advisor layer (Plan 0038, ADR-0029): the app may recommend, not act.

The one labeled layer where analyst outputs are fused into a directional
trade recommendation. Downstream consumer of `analysis/`, `backtest/`, and
the forecast surface — imports their outputs, never their internals. Holds
no key, places no order, moves no money (ADR-0025 is a different, untaken
decision).
"""

from market_analyser.advisor.fusion import fuse
from market_analyser.advisor.models import BasisValue, Recommendation, RecommendationBasis

__all__ = ["BasisValue", "Recommendation", "RecommendationBasis", "fuse"]

"""Shared internals used by more than one MCP tool / route (Plan 0072 phase 3).

The designated home for helpers a tool or route legitimately shares with a
sibling, so consumers import from here instead of reaching across into another
tool module's `_`-private names (finding (f) of the 2026-07-09 audit).

Kept as focused submodules — `backtest_timeframe` (a trivial `Literal`) and
`chart_patterns_response` (the detection body, which pulls the analysis layer) —
so importing one does not transitively load the other's dependencies.
"""

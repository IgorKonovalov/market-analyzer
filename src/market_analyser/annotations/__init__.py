"""Agent-written annotations on the chart (Plan 0006).

Annotations are app-private state — markers an external MCP client wrote against
a (symbol, timeframe, event_ts) tuple. The data they describe is not a
data-source (no provider), so the Pydantic types live here rather than under
`data/types.py`.
"""

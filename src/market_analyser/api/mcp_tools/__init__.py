"""Per-tool MCP-server modules registered from `api.mcp_app.create_mcp_components`.

The first tool to land here is `run_backtest` (Plan 0008 phase 4); the
forward-looking `forecast` tool (Plan 0036 phase 4) is the newest. Older
tools (`get_ohlcv`, `write_annotation`, `list_annotations`, `show_chart`,
`update_chart`, `highlight_pattern`) still live inline inside `mcp_app.py` —
they will migrate as they grow their own per-tool tests or evolve enough
shape to need it. There is no protocol change between inline and per-file
tools; `FastMCP.tool` just needs a callable with the user-facing signature.
"""

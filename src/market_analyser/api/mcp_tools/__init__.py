"""Per-tool MCP-server modules registered from `api.mcp_app.create_mcp_components`.

One module per tool, each exposing a `register_<tool>(server, *, deps)` the
assembly hub calls (Plan 0017). The newest arrivals are the forward-looking
`forecast` tool (Plan 0036 phase 4) and the advisor layer's `recommend` tool
(Plan 0038 phase 2, ADR-0029) — the one tool allowed to emit a labeled
advisory trade recommendation rather than a pure condition report. There is
no protocol difference between tools; `FastMCP.tool` just needs a callable
with the user-facing signature.
"""

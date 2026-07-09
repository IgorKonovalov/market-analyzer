"""Plan 0070 phase 2 done-when: the markdown renderer is exact and deterministic.

The exact-block tests pin the table layout (type / required / default, including a
required positional rendered `—` and an optional rendered `` `None` ``) and the
no-params / return-fallback shapes. The cross-run test asserts byte-identical
output from two independent wiring passes — the property the `--check` CI gate
stands on.
"""

from __future__ import annotations

from pathlib import Path

from market_analyser.apiref.introspect import (
    ParamDoc,
    ToolDoc,
    introspect_events,
    introspect_routes,
    introspect_tools,
)
from market_analyser.apiref.render import (
    render_events_doc,
    render_index,
    render_routes_doc,
    render_tool,
    render_tools_doc,
)
from market_analyser.apiref.wiring import build_wired_app, build_wired_mcp_server

_FULL_SRC = "src/market_analyser/api/mcp_tools/sample_tool.py"
_PING_SRC = "src/market_analyser/api/mcp_tools/ping.py"

_FULL_TOOL = ToolDoc(
    name="sample_tool",
    summary="Do a sample thing.",
    description="Do a sample thing.\n\nA second paragraph with more detail.",
    params=(
        ParamDoc(name="symbol", type_str="string", required=True, default=None),
        ParamDoc(name="limit", type_str="integer", required=False, default="None"),
    ),
    return_shape="SampleResult",
    return_fields=(ParamDoc(name="ok", type_str="boolean", required=True, default=None),),
    source_module="market_analyser.api.mcp_tools.sample_tool",
    source_path=_FULL_SRC,
)

# Built by adjacent-string concatenation (not a triple-quoted block) only so the
# long Source line stays under the line-length limit; the value is still an exact
# literal render.
_EXPECTED_FULL = (
    "## `sample_tool`\n"
    "\n"
    "Do a sample thing.\n"
    "\n"
    "A second paragraph with more detail.\n"
    "\n"
    "**Parameters**\n"
    "\n"
    "| Name | Type | Required | Default |\n"
    "| --- | --- | --- | --- |\n"
    "| `symbol` | string | yes | — |\n"
    "| `limit` | integer | no | `None` |\n"
    "\n"
    "**Returns:** `SampleResult`\n"
    "\n"
    "| Field | Type |\n"
    "| --- | --- |\n"
    "| `ok` | boolean |\n"
    "\n"
    f"**Source:** [`{_FULL_SRC}`](../../{_FULL_SRC})\n"
)

_NO_PARAMS_TOOL = ToolDoc(
    name="ping",
    summary="Ping.",
    description="Ping.",
    params=(),
    return_shape="dict[str, str]",
    return_fields=(),
    source_module="market_analyser.api.mcp_tools.ping",
    source_path=_PING_SRC,
)

_EXPECTED_NO_PARAMS = (
    "## `ping`\n"
    "\n"
    "Ping.\n"
    "\n"
    "**Parameters**\n"
    "\n"
    "No parameters.\n"
    "\n"
    "**Returns:** `dict[str, str]`\n"
    "\n"
    f"**Source:** [`{_PING_SRC}`](../../{_PING_SRC})\n"
)


def test_render_tool_exact_block() -> None:
    assert render_tool(_FULL_TOOL) == _EXPECTED_FULL


def test_render_tool_no_params_and_return_fallback() -> None:
    rendered = render_tool(_NO_PARAMS_TOOL)
    assert rendered == _EXPECTED_NO_PARAMS
    # No-params tools state it explicitly rather than emitting an empty table.
    assert "No parameters." in rendered
    # The return fallback (no output-schema fields) renders the shape line only,
    # never an empty field table.
    assert "**Returns:** `dict[str, str]`" in rendered
    assert "| Field | Type |" not in rendered


def test_render_tool_with_output_schema_renders_field_table() -> None:
    rendered = render_tool(_FULL_TOOL)
    assert "| Field | Type |" in rendered
    assert "| `ok` | boolean |" in rendered


def test_render_tool_is_stable_across_calls() -> None:
    assert render_tool(_FULL_TOOL) == render_tool(_FULL_TOOL)


def test_full_reference_renders_byte_identically_across_runs(tmp_path: Path) -> None:
    def render_all(run_dir: Path) -> tuple[str, str, str, str]:
        server = build_wired_mcp_server(run_dir)
        app = build_wired_app(run_dir)
        tools = introspect_tools(server)
        routes = introspect_routes(app)
        events = introspect_events()
        return (
            render_tools_doc(tools),
            render_routes_doc(routes),
            render_events_doc(events),
            render_index(tools, routes, events),
        )

    # Two independent wiring + introspection + render passes must be byte-identical.
    assert render_all(tmp_path / "run_a") == render_all(tmp_path / "run_b")

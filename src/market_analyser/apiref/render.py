"""Deterministic markdown rendering of the introspection records (Plan 0070
phase 2, ADR-0064).

Pure functions: records in, markdown out. No wall-clock, no host paths, no
set-ordering — the same records render byte-identically on every run and every
machine, which is exactly what the `--check` CI gate depends on. Every generated
file opens with a "generated - do not edit" banner naming the regenerate command.

Source links are file-level (module path, no line number) so an unrelated edit
that shifts line numbers never churns the reference.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from market_analyser.apiref.introspect import EventDoc, ParamDoc, RouteDoc, ToolDoc

_REGEN_COMMAND = "uv run python -m market_analyser.apiref"
_REGEN_ALIAS = "pnpm gen:api-docs"

# docs/reference/ sits two directories below the repo root; a `src/...` link
# therefore climbs two levels.
_SRC_LINK_PREFIX = "../../"


def _banner() -> str:
    return (
        "<!-- GENERATED FILE - DO NOT EDIT BY HAND.\n"
        f"     Regenerate: {_REGEN_COMMAND}  (or: {_REGEN_ALIAS})\n"
        "     Rendered from the live sidecar; see Plan 0070 / ADR-0064. -->"
    )


def _cell(text: str) -> str:
    """Escape a value for a markdown table cell (pipes, newlines)."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _anchor(heading_text: str) -> str:
    """GitHub-style heading anchor for an in-page link."""
    slug = heading_text.strip().lower().replace("`", "")
    slug = re.sub(r"[^a-z0-9 \-]", "", slug)
    return slug.replace(" ", "-")


def _source_link(source_path: str) -> str:
    return f"**Source:** [`{source_path}`]({_SRC_LINK_PREFIX}{source_path})"


def _param_rows(params: Iterable[ParamDoc]) -> list[str]:
    rows = [
        "| Name | Type | Required | Default |",
        "| --- | --- | --- | --- |",
    ]
    for param in params:
        required = "yes" if param.required else "no"
        default = f"`{param.default}`" if param.default is not None else "—"
        rows.append(f"| `{_cell(param.name)}` | {_cell(param.type_str)} | {required} | {default} |")
    return rows


def _field_rows(fields: Iterable[ParamDoc]) -> list[str]:
    rows = [
        "| Field | Type |",
        "| --- | --- |",
    ]
    for field in fields:
        rows.append(f"| `{_cell(field.name)}` | {_cell(field.type_str)} |")
    return rows


def render_tool(tool: ToolDoc) -> str:
    """Render one MCP tool entry."""
    lines = [f"## `{tool.name}`", ""]
    if tool.description.strip():
        lines += [tool.description.strip(), ""]
    lines += ["**Parameters**", ""]
    lines += _param_rows(tool.params) if tool.params else ["No parameters."]
    lines += ["", f"**Returns:** `{tool.return_shape}`", ""]
    if tool.return_fields:
        lines += _field_rows(tool.return_fields)
        lines += [""]
    lines.append(_source_link(tool.source_path))
    return "\n".join(lines).rstrip() + "\n"


def render_route(route: RouteDoc) -> str:
    """Render one REST route entry."""
    heading = f"{route.method} {route.path}"
    lines = [f"## `{heading}`", ""]
    body = route.description.strip() or route.summary.strip()
    if body:
        lines += [body, ""]
    lines += [f"**Auth:** {route.auth}", ""]
    lines += ["**Parameters**", ""]
    lines += _param_rows(route.params) if route.params else ["No parameters."]
    lines += [""]
    if route.request_schema is not None:
        lines.append(f"**Request body:** `{route.request_schema}`")
    if route.response_schema is not None:
        lines.append(f"**Response:** `{route.response_schema}`")
    return "\n".join(lines).rstrip() + "\n"


def render_event(event: EventDoc) -> str:
    """Render one SSE event entry."""
    lines = [f"## `{event.kind}`", "", f"**Version:** {event.version}", ""]
    if event.description.strip():
        lines += [event.description.strip(), ""]
    lines += ["**Payload fields**", ""]
    lines += _param_rows(event.payload_fields) if event.payload_fields else ["No payload fields."]
    lines += ["", _source_link(event.source_path)]
    return "\n".join(lines).rstrip() + "\n"


def _toc(rows: Iterable[tuple[str, str]], name_header: str) -> list[str]:
    """A two-column table of contents: linked name + one-line summary."""
    lines = [f"| {name_header} | Summary |", "| --- | --- |"]
    for heading, summary in rows:
        lines.append(f"| [`{heading}`](#{_anchor(heading)}) | {_cell(summary)} |")
    return lines


def _document(title: str, intro: str, toc_lines: list[str], entries: list[str]) -> str:
    parts = [_banner(), "", f"# {title}", "", intro, ""]
    parts += toc_lines
    parts += ["", "---", ""]
    parts.append("\n".join(entries).rstrip())
    return "\n".join(parts).rstrip() + "\n"


def render_tools_doc(tools: tuple[ToolDoc, ...]) -> str:
    intro = (
        f"The {len(tools)} agent-callable MCP tools mounted at `/mcp`, from the live "
        "FastMCP registry."
    )
    toc = _toc([(tool.name, tool.summary) for tool in tools], "Tool")
    entries = [render_tool(tool) for tool in tools]
    return _document("MCP tools", intro, toc, entries)


def render_routes_doc(routes: tuple[RouteDoc, ...]) -> str:
    intro = (
        f"The {len(routes)} renderer-facing REST operations, from the FastAPI OpenAPI "
        "document. Every route is renderer-bearer gated by the central middleware except "
        "the auth-exempt `/healthz` liveness probe. Route handlers live under "
        f"[`src/market_analyser/api/routes/`]({_SRC_LINK_PREFIX}src/market_analyser/api/routes)."
    )
    toc = _toc(
        [(f"{route.method} {route.path}", route.summary) for route in routes],
        "Route",
    )
    entries = [render_route(route) for route in routes]
    return _document("REST API", intro, toc, entries)


def render_events_doc(events: tuple[EventDoc, ...]) -> str:
    intro = (
        f"The {len(events)} SSE envelope kinds published on `/events`, from the event "
        "type registry. Each kind carries a versioned, validated payload."
    )
    toc = _toc([(event.kind, event.summary) for event in events], "Event")
    entries = [render_event(event) for event in events]
    return _document("SSE events", intro, toc, entries)


def render_index(
    tools: tuple[ToolDoc, ...],
    routes: tuple[RouteDoc, ...],
    events: tuple[EventDoc, ...],
) -> str:
    lines = [
        _banner(),
        "",
        "# Sidecar API reference",
        "",
        "Full-detail reference for the sidecar's three surfaces, generated by "
        "introspecting the live, fully-wired app so it cannot drift from behaviour "
        "(Plan 0070, ADR-0064).",
        "",
        f"Do not edit by hand. Regenerate with `{_REGEN_COMMAND}` (or `{_REGEN_ALIAS}`); "
        "CI fails on any drift.",
        "",
        f"- [MCP tools](mcp-tools.md) — {len(tools)} agent-callable tools at `/mcp`",
        f"- [REST API](rest-api.md) — {len(routes)} renderer routes",
        f"- [SSE events](events.md) — {len(events)} event kinds on `/events`",
    ]
    return "\n".join(lines).rstrip() + "\n"

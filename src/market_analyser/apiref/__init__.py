"""Sidecar API reference generator (Plan 0070, ADR-0064).

Boots the fully-wired sidecar in memory, introspects its three surfaces — the
MCP tool registry, the FastAPI OpenAPI document, and the SSE event vocabulary —
and renders deterministic markdown under ``docs/reference/``. A ``--check`` mode
re-renders in memory and fails on any drift, gating freshness in CI exactly like
``gen-types:check``.

The reference is rendered from the same registry / OpenAPI / envelope objects the
runtime uses, so it cannot drift from behaviour without turning CI red — no
schemas are re-derived by hand.

Modules:

- ``wiring`` — the single fully-wired construction of the ``FastMCP`` server + the
  ``FastAPI`` app, shared with ``tests/api/test_mcp_tools.py`` so the
  conditional-registration wrinkle is solved once.
- ``introspect`` — pure functions returning ordered, structured ``ToolDoc`` /
  ``RouteDoc`` / ``EventDoc`` records from the live objects.
- ``render`` — deterministic markdown rendering of those records (Plan 0070 phase 2).
- ``__main__`` — the ``write`` / ``--check`` CLI (Plan 0070 phase 3).
"""

from __future__ import annotations

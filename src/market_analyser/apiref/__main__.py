"""CLI for the sidecar API reference generator (Plan 0070 phase 3, ADR-0064).

    python -m market_analyser.apiref            # write mode
    python -m market_analyser.apiref --check    # drift gate (exit 1 on drift)

Write mode renders the four reference files under ``docs/reference/`` from the
live, fully-wired sidecar. Check mode renders the same content in memory, diffs
it against the committed files, prints a unified diff of any drift, and exits 1 —
the ``gen-types:check``-style gate that keeps the reference honest in CI.

Output is written as UTF-8 with LF newlines (``docs/reference/*.md`` is pinned to
``eol=lf`` in ``.gitattributes``), so a write on any platform round-trips through
git without a spurious CRLF diff.
"""

from __future__ import annotations

import argparse
import difflib
import logging
import sys
import tempfile
from pathlib import Path

from market_analyser.apiref.introspect import (
    introspect_events,
    introspect_routes,
    introspect_tools,
)
from market_analyser.apiref.render import (
    render_events_doc,
    render_index,
    render_routes_doc,
    render_tools_doc,
)
from market_analyser.apiref.wiring import build_wired_app, build_wired_mcp_server

# apiref/__main__.py -> apiref -> market_analyser -> src -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE_DIR = _REPO_ROOT / "docs" / "reference"


def render_reference() -> dict[str, str]:
    """Render the four reference files (filename -> markdown) from the live app."""
    # The wiring applies Alembic migrations to its in-memory DB; silence that
    # INFO chatter so a `--check` failure's diff is the only thing on stderr.
    logging.getLogger("alembic").setLevel(logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="apiref-") as tmp:
        run_dir = Path(tmp)
        server = build_wired_mcp_server(run_dir)
        app = build_wired_app(run_dir)
        tools = introspect_tools(server)
        routes = introspect_routes(app)
        events = introspect_events()
    return {
        "README.md": render_index(tools, routes, events),
        "mcp-tools.md": render_tools_doc(tools),
        "rest-api.md": render_routes_doc(routes),
        "events.md": render_events_doc(events),
    }


def write_reference(files: dict[str, str], reference_dir: Path = _REFERENCE_DIR) -> None:
    reference_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (reference_dir / name).write_bytes(content.encode("utf-8"))


def check_reference(files: dict[str, str], reference_dir: Path = _REFERENCE_DIR) -> int:
    """Return 0 when every committed file matches the rendered content, else 1
    (printing a unified diff per drifted file to stderr)."""
    drifted = False
    for name, content in files.items():
        path = reference_dir / name
        current = path.read_bytes().decode("utf-8") if path.exists() else ""
        if current != content:
            drifted = True
            reason = "missing" if not path.exists() else "stale"
            print(f"DRIFT ({reason}): docs/reference/{name}", file=sys.stderr)
            diff = difflib.unified_diff(
                current.splitlines(),
                content.splitlines(),
                fromfile=f"committed/{name}",
                tofile=f"generated/{name}",
                lineterm="",
            )
            for line in diff:
                print(line, file=sys.stderr)
    if drifted:
        print(
            "\napiref --check: the committed reference is stale. "
            "Regenerate with: uv run python -m market_analyser.apiref",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m market_analyser.apiref",
        description="Generate or verify the sidecar API reference under docs/reference/.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed reference matches the live surfaces; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    files = render_reference()
    if args.check:
        return check_reference(files)
    write_reference(files)
    for name in files:
        print(f"wrote docs/reference/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

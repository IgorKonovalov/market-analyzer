"""Command-line entry point for market-analyser.

The sidecar uvicorn process is launched via ``python -m market_analyser.api``;
this ``market-analyser`` script (registered through ``[project.scripts]`` in
``pyproject.toml``) is the user-facing CLI for inspecting the project.

Subcommands:

``strategies list [--json]``
    Discover every strategy module under ``market_analyser.strategies`` and
    print ``id`` / ``name`` / ``version`` / ``timeframes`` plus the JSON
    schema of its ``Params`` model. Output is deterministic across runs
    (``discover()`` returns a dict sorted by ``META.id`` and pydantic emits
    ``model_json_schema()`` in declaration order).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from market_analyser.contracts.strategy import BaseParams, StrategyMeta, discover


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market-analyser")
    subparsers = parser.add_subparsers(dest="command", required=True)

    strategies = subparsers.add_parser(
        "strategies",
        help="Inspect installed strategy modules.",
    )
    strategies_sub = strategies.add_subparsers(dest="strategies_command", required=True)

    list_parser = strategies_sub.add_parser(
        "list",
        help="List discovered strategies with their parameter schemas.",
    )
    list_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit a JSON array instead of human-readable text.",
    )

    return parser


def _collect_strategies() -> list[dict[str, Any]]:
    """Build the structured payload that both output modes render."""

    modules = discover()
    rows: list[dict[str, Any]] = []
    for mod in modules.values():
        # `discover()` validated both attributes; annotations are for readers.
        meta: StrategyMeta = mod.META
        params_cls: type[BaseParams] = mod.Params
        rows.append(
            {
                "id": meta.id,
                "name": meta.name,
                "version": meta.version,
                "timeframes": list(meta.timeframes),
                "params_schema": params_cls.model_json_schema(),
            }
        )
    return rows


def _render_text(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, row in enumerate(rows):
        if i > 0:
            lines.append("")
        timeframes = ", ".join(row["timeframes"])
        lines.append(f"{row['id']} - {row['name']} v{row['version']} [{timeframes}]")
        schema = json.dumps(row["params_schema"], indent=2)
        lines.extend(f"  {line}" for line in schema.splitlines())
    return "\n".join(lines) + "\n"


def _strategies_list(*, as_json: bool) -> int:
    rows = _collect_strategies()
    if as_json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
    else:
        sys.stdout.write(_render_text(rows))
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "strategies" and args.strategies_command == "list":
        raise SystemExit(_strategies_list(as_json=args.as_json))

    # argparse with `required=True` on every subparser will have already
    # raised SystemExit before reaching here; this is unreachable in practice.
    raise SystemExit(2)


if __name__ == "__main__":
    main()

"""Layering invariant: domain packages must not import the `api` layer.

Plan 0072 phase 1 done-when (ADR-0065): the renderer→agent feedback *buffer*
(`UIEventBuffer` / `UIEventEnvelope`) moved out of `api/ui_events/` into the
neutral top-level `market_analyser.ui_events` core so that domain background
loops — the watch scheduler today, DeFi jobs and backfill tomorrow — can produce
agent-pollable events without depending **up** into the FastAPI transport layer.

This is the same acyclic-graph invariant [ADR-0032] already holds for the SSE
event bus. The static check *is* the acceptance gate (the plan's Risks section
names it so): a fresh `alerts/`/`defi/`/`data/`/`analysis/` import of
`market_analyser.api.*` regresses the layering and must fail here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import market_analyser

# Domain packages that must never import the api (transport/composition) layer.
# They may only depend *downward* on neutral cores (`events/`, `ui_events/`,
# `contracts/`, `persistence/`, ...).
DOMAIN_PACKAGES = ("alerts", "defi", "data", "analysis")

FORBIDDEN_PREFIX = "market_analyser.api"


def _package_root() -> Path:
    package_file = market_analyser.__file__
    assert package_file is not None
    return Path(package_file).parent


def _imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        # Only absolute imports carry a module we can classify; relative
        # imports (`node.level > 0`) stay within the domain package.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            names.append(node.module)
    return names


def test_domain_packages_do_not_import_the_api_layer() -> None:
    root = _package_root()
    checked_files = 0
    for package in DOMAIN_PACKAGES:
        package_dir = root / package
        assert package_dir.is_dir(), f"expected domain package {package!r} at {package_dir}"
        for source in sorted(package_dir.rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            checked_files += 1
            for name in _imported_modules(tree):
                assert not (name == FORBIDDEN_PREFIX or name.startswith(FORBIDDEN_PREFIX + ".")), (
                    f"{source.relative_to(root)} imports {name!r} — domain code "
                    f"must not depend up into the api layer (ADR-0065/ADR-0032). "
                    "Move the shared primitive into a neutral top-level core."
                )
    assert checked_files > 0  # the lint actually walked source files

"""Living behavioral-spec freshness gate (Plan 0112 phase 2, ADR-0106).

`docs/architecture/specs/` holds one hand-authored behavioral contract per core
subsystem, reconciled at each plan's close ceremony. This package is the
`dev`-owned *structural* gate that keeps them honest — a sibling to
`market_analyser.apiref`'s ``--check``: it verifies every spec has the required
sections and a resolvable ``Reconciled-through:`` plan reference, but leaves
behavioral accuracy to the architect's judgment at reconcile time.

Modules:

- ``check`` — pure functions returning the list of structural problems.
- ``__main__`` — the ``--check`` CLI that prints problems to stderr and exits 1.
"""

from __future__ import annotations

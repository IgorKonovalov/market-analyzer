"""CLI for the living-spec freshness gate (Plan 0112 phase 2, ADR-0106).

    python -m market_analyser.specs            # check mode (the only mode)
    python -m market_analyser.specs --check    # explicit; identical behavior

Structural gate sibling to ``apiref --check``. Prints one line per problem to
stderr and exits 1 when any spec under ``docs/architecture/specs/`` is
structurally stale or dangling; exits 0 when every spec (excluding
``_template.md`` / ``README.md``) carries its required sections and a resolvable
``Reconciled-through: Plan NNNN`` reference.

``--check`` is accepted for symmetry with ``gen:api-docs:check`` and reads the
same as no argument — there is no "write" mode, because specs are hand-authored.
"""

from __future__ import annotations

import argparse
import sys

from market_analyser.specs.check import check_specs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m market_analyser.specs",
        description="Verify docs/architecture/specs/ are structurally fresh; exit 1 on problems.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the structural freshness check (the default and only mode).",
    )
    parser.parse_args(argv)

    problems = check_specs()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            f"\nspecs --check: {len(problems)} structural problem(s) in "
            "docs/architecture/specs/. Each spec (except _template.md / README.md) "
            "needs an '## Invariants' section, a '## Scenarios' section, and a "
            "'Reconciled-through: Plan NNNN' line pointing at a plan under "
            "plans/ or plans/done/.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

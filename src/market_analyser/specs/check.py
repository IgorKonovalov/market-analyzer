"""Structural freshness rules for the living behavioral specs (Plan 0112 phase 2).

Sibling to `apiref --check`: a *structural* gate, not a behavioral judge. A spec
under ``docs/architecture/specs/`` (excluding ``_template.md`` and ``README.md``)
is structurally fresh when it carries an ``## Invariants`` section, a
``## Scenarios`` section, and a ``Reconciled-through: Plan NNNN`` line whose plan
resolves to a file under ``plans/`` or ``plans/done/``. Behavioral accuracy stays
the architect's judgment at reconcile time (ADR-0106); this module only defends
the claim that a spec which lost its ``Reconciled-through:`` line, or points at a
non-existent plan, turns CI red rather than passing silently.
"""

from __future__ import annotations

import re
from pathlib import Path

# specs/check.py -> specs -> market_analyser -> src -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPECS_DIR = _REPO_ROOT / "docs" / "architecture" / "specs"
_PLANS_DIR = _REPO_ROOT / "docs" / "architecture" / "plans"

# `_template.md` is the shape to copy (its `Plan NNNN` is a placeholder) and
# `README.md` is the index; neither is a subsystem contract, so both are exempt.
EXCLUDED_FILENAMES = frozenset({"_template.md", "README.md"})

_INVARIANTS_RE = re.compile(r"^##\s+Invariants\s*$", re.MULTILINE)
_SCENARIOS_RE = re.compile(r"^##\s+Scenarios\s*$", re.MULTILINE)
# Matches the reconciled line body regardless of surrounding markdown bold, e.g.
# `> **Reconciled-through:** Plan 0112`. Plans are zero-padded 4-digit (NNNN).
_RECONCILED_PLAN_RE = re.compile(r"Reconciled-through:\**\s*Plan\s+(\d{4})")
_RECONCILED_PRESENT_RE = re.compile(r"Reconciled-through:")


def _plan_resolves(plan_number: str, plans_dir: Path) -> bool:
    """True if a plan ``NNNN-*.md`` exists under ``plans/`` or ``plans/done/``."""

    return any(any(base.glob(f"{plan_number}-*.md")) for base in (plans_dir, plans_dir / "done"))


def check_specs(specs_dir: Path = _SPECS_DIR, plans_dir: Path = _PLANS_DIR) -> list[str]:
    """Return a sorted list of human-readable structural problems.

    An empty list means every spec is structurally fresh. A missing ``specs_dir``
    yields no problems — the layer is opt-in, so an absent directory is "nothing
    to gate," not a failure.
    """

    if not specs_dir.is_dir():
        return []

    problems: list[str] = []
    for path in sorted(specs_dir.glob("*.md")):
        if path.name in EXCLUDED_FILENAMES:
            continue
        text = path.read_text(encoding="utf-8")
        rel = f"docs/architecture/specs/{path.name}"

        if not _INVARIANTS_RE.search(text):
            problems.append(f"{rel}: missing required section '## Invariants'")
        if not _SCENARIOS_RE.search(text):
            problems.append(f"{rel}: missing required section '## Scenarios'")

        plan_match = _RECONCILED_PLAN_RE.search(text)
        if plan_match is None:
            if _RECONCILED_PRESENT_RE.search(text):
                problems.append(
                    f"{rel}: 'Reconciled-through:' line is malformed "
                    "(expected 'Reconciled-through: Plan NNNN')"
                )
            else:
                problems.append(f"{rel}: missing 'Reconciled-through:' line")
        elif not _plan_resolves(plan_match.group(1), plans_dir):
            problems.append(
                f"{rel}: Reconciled-through references Plan {plan_match.group(1)}, "
                "which resolves to neither plans/ nor plans/done/"
            )

    return problems


__all__ = ["EXCLUDED_FILENAMES", "check_specs"]

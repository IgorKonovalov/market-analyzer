"""Plan 0112 phase 2 done-when: the living-spec freshness gate.

The gate is structural, not behavioral. These tests pin both directions:

1. The four committed specs pass (`check_specs()` / `main(["--check"])` -> 0) —
   the phase-1 pilot and the phase-3 backfill are structurally fresh.
2. Every failure mode reddens the gate: a missing `## Invariants` / `## Scenarios`
   section, a missing or malformed `Reconciled-through:` line, and a
   `Reconciled-through:` pointing at a plan that resolves to neither `plans/` nor
   `plans/done/`.
3. `_template.md` and `README.md` are excluded (a template's placeholder
   `Plan NNNN` must not fail the gate), and a plan under `plans/done/` resolves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from market_analyser.specs.__main__ import main
from market_analyser.specs.check import check_specs

_VALID_SPEC = """# Spec — Example subsystem

> **Subsystem:** an example, for tests only.
> **Source:** src/market_analyser/example/
> **Reconciled-through:** Plan 0001
> **Governing ADRs:** 0001-example

## Invariants
- The subsystem MUST do the thing.

## Scenarios
- WHEN a condition holds THEN an observable behavior follows.

## Known gaps / honest nulls
- Deliberately guarantees nothing about the other thing.
"""


@pytest.fixture
def spec_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A minimal (specs_dir, plans_dir) pair with one valid spec + its plan."""

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    plans_dir = tmp_path / "plans"
    (plans_dir / "done").mkdir(parents=True)

    (specs_dir / "example.md").write_text(_VALID_SPEC, encoding="utf-8")
    (plans_dir / "0001-example.md").write_text("# 0001 - example\n", encoding="utf-8")
    return specs_dir, plans_dir


def test_valid_spec_passes(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    assert check_specs(specs_dir, plans_dir) == []


def test_missing_invariants_section_fails(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    spec = specs_dir / "example.md"
    spec.write_text(_VALID_SPEC.replace("## Invariants", "## Guarantees"), encoding="utf-8")
    problems = check_specs(specs_dir, plans_dir)
    assert any("missing required section '## Invariants'" in p for p in problems)


def test_missing_scenarios_section_fails(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    spec = specs_dir / "example.md"
    spec.write_text(_VALID_SPEC.replace("## Scenarios", "## Examples"), encoding="utf-8")
    problems = check_specs(specs_dir, plans_dir)
    assert any("missing required section '## Scenarios'" in p for p in problems)


def test_missing_reconciled_line_fails(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    spec = specs_dir / "example.md"
    spec.write_text(
        _VALID_SPEC.replace("> **Reconciled-through:** Plan 0001\n", ""),
        encoding="utf-8",
    )
    problems = check_specs(specs_dir, plans_dir)
    assert any("missing 'Reconciled-through:' line" in p for p in problems)


def test_malformed_reconciled_line_fails(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    spec = specs_dir / "example.md"
    # Present but no `Plan NNNN` token -> malformed, not merely missing.
    spec.write_text(
        _VALID_SPEC.replace("> **Reconciled-through:** Plan 0001", "> **Reconciled-through:** TBD"),
        encoding="utf-8",
    )
    problems = check_specs(specs_dir, plans_dir)
    assert any("'Reconciled-through:' line is malformed" in p for p in problems)


def test_dangling_plan_reference_fails(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    spec = specs_dir / "example.md"
    # Plan 9999 exists in neither plans/ nor plans/done/.
    spec.write_text(_VALID_SPEC.replace("Plan 0001", "Plan 9999"), encoding="utf-8")
    problems = check_specs(specs_dir, plans_dir)
    assert any("references Plan 9999" in p for p in problems)


def test_plan_reference_resolves_in_done(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    # Move the plan into done/ and point the spec at it: still resolves.
    (plans_dir / "0001-example.md").unlink()
    (plans_dir / "done" / "0002-archived.md").write_text("# 0002\n", encoding="utf-8")
    (specs_dir / "example.md").write_text(
        _VALID_SPEC.replace("Plan 0001", "Plan 0002"), encoding="utf-8"
    )
    assert check_specs(specs_dir, plans_dir) == []


def test_template_and_readme_are_excluded(spec_tree: tuple[Path, Path]) -> None:
    specs_dir, plans_dir = spec_tree
    # A template with a placeholder `Plan NNNN` and a README missing every
    # section must not fail the gate.
    (specs_dir / "_template.md").write_text(
        "# Spec — <name>\n> **Reconciled-through:** Plan NNNN\n", encoding="utf-8"
    )
    (specs_dir / "README.md").write_text("# Index\nNo sections here.\n", encoding="utf-8")
    assert check_specs(specs_dir, plans_dir) == []


def test_absent_specs_dir_is_not_a_failure(tmp_path: Path) -> None:
    assert check_specs(tmp_path / "nope", tmp_path / "plans") == []


def test_committed_specs_tree_is_fresh() -> None:
    # The real done-when: the four committed specs pass with the default dirs.
    assert check_specs() == []


def test_main_check_on_committed_tree_returns_zero() -> None:
    assert main(["--check"]) == 0
    assert main([]) == 0


def test_main_returns_one_and_prints_when_problems(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "market_analyser.specs.__main__.check_specs",
        lambda: ["docs/architecture/specs/x.md: missing required section '## Invariants'"],
    )
    assert main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "missing required section '## Invariants'" in err
    assert "specs --check: 1 structural problem(s)" in err

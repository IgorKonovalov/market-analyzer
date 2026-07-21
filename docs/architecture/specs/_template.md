# Spec — <subsystem name>

> **Subsystem:** <one line — what this subsystem is responsible for>
> **Source:** src/market_analyser/<paths>  (and desktop/<paths> if renderer)
> **Reconciled-through:** Plan NNNN  (last plan whose close reconciled this file)
> **Governing ADRs:** NNNN-foo, NNNN-bar

<!--
This is the template for a living behavioral spec (Plan 0112, ADR-0106). Copy it
to `docs/architecture/specs/<subsystem>.md`, fill every section, and delete these
comments. The freshness gate (`specs --check`, Plan 0112 phase 2) requires every
spec except this file and README.md to carry an `## Invariants` section, a
`## Scenarios` section, and a `Reconciled-through:` line pointing at a plan that
resolves in `plans/` or `plans/done/`. Keep the three headings and the
`Reconciled-through: Plan NNNN` line exact so the gate stays green.

A spec states what the running system does *today*, by behavior — not what a plan
is about to build (that expires to `done/`) and not the mechanical param/payload
surface (that is generated apiref, `docs/reference/`, ADR-0064). Write invariants
as MUST-statements a maintainer can hold the code to; write scenarios as
observable WHEN/THEN behavior. Cross-link each claim to its governing ADR and,
where useful, the source file that enforces it.
-->

## Invariants

- The subsystem MUST <behavioral guarantee that holds for every input>.  (ADR-NNNN, `src/...`)
- The subsystem MUST <another guarantee>.  (ADR-NNNN)

## Scenarios

- WHEN <condition or input> THEN <observable, checkable behavior>.
- WHEN <edge / failure condition> THEN <how the subsystem responds>.

## Known gaps / honest nulls

- <what this subsystem deliberately does NOT guarantee, and why — the honest
  boundary of the contract, so a reader does not assume a guarantee that isn't
  there>.

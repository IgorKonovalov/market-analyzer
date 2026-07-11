"""One-shot back-fill of the pre-existing `runs/advice` artifacts into the
advice ledger (Plan 0080 phase 1, ADR-0075).

The ledger indexes every `recommend` call, but recommendations were already being
persisted as `runs/advice/<stamp>-<symbol>/explanation.json` artifacts before the
ledger existed (ADR-0058). Those carry everything a ledger row needs — the fused
verdict (direction/entry/stop/targets/conviction), the as-of bar, and the
horizon (on the forecast leg). This ingests them once, at startup, so the track
record has history from day one rather than starting empty.

Idempotent by construction: `record` is first-write-wins on the call identity, so
re-running the back-fill (every boot) inserts nothing already present and never
touches an outcome the scorer has since written. A malformed or unreadable
artifact is skipped, not fatal — one bad file cannot block the boot.
"""

from __future__ import annotations

from pathlib import Path

from market_analyser.api.mcp_tools.recommend import (
    RecommendationExplanationArtifact,
    ledger_entry_from_recommendation,
)
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerRepository


def backfill_advice_ledger(repository: AdviceLedgerRepository, runs_dir: Path) -> int:
    """Ingest every `runs_dir/advice/*/explanation.json` into the ledger,
    append-only. Returns the count of newly-inserted rows (rows already present
    are skipped). A missing `advice/` directory is a no-op."""

    advice_dir = runs_dir / "advice"
    if not advice_dir.is_dir():
        return 0

    inserted = 0
    for artifact_path in sorted(advice_dir.glob("*/explanation.json")):
        try:
            artifact = RecommendationExplanationArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            # A malformed/unreadable artifact predates or violates the schema —
            # skip it rather than abort the whole ingestion (and the boot).
            continue
        entry = ledger_entry_from_recommendation(
            artifact.recommendation,
            strategy_id=artifact.strategy_id,
            # The forecast leg carries the horizon the call was made at — the same
            # value the tool's `horizon_bars` param produced, so the back-filled
            # call_id matches a live re-run of the same call exactly.
            horizon_bars=artifact.inputs.forecast.horizon_bars,
            forecast=artifact.inputs.forecast,
            artifact_path=artifact_path.relative_to(runs_dir).as_posix(),
            created_at=artifact.started_at,
        )
        if repository.record(entry):
            inserted += 1
    return inserted


__all__ = ["backfill_advice_ledger"]

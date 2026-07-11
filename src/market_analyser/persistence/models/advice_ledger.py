"""`advice_ledger` ORM model — Plan 0080 phase 1 (ADR-0075).

The append-only, queryable index over the advisor's own `recommend` calls: one
row per call (directional *and* flat), written at production time beside the
existing `runs/advice` explanation artifact (the ADR-0018 disk-artifact +
SQLite-index pattern). A scheduled scorer (phase 3) later fills the nullable
outcome columns once the call's horizon has matured; the recorded call itself is
never mutated or deleted — cherry-picking a loser out of the record is made
structurally impossible.

`call_id` is a synthetic, deterministic identity string
(`symbol|timeframe|strategy_id|as_of_bar_ts|horizon_bars`): re-running the same
recommendation at the same bar resolves to the same `call_id`, so first-write-
wins keeps one row per call and never overwrites a scored outcome. The identity
components are also stored as their own columns so the aggregation layer (phase
4) can filter/group by symbol, horizon, and maturity without parsing the id.

`Base` lives in `_base.py` to keep the pre-package import contract intact while
opening per-domain-file growth (the `backtest_runs` precedent).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class AdviceLedgerRow(Base):
    """One recorded advisory recommendation. PK is the synthetic `call_id`.

    The call half (`symbol` … `created_at`) is written once, at recommend time,
    and is immutable. The outcome half (`outcome_class` … `scored_at`) is null
    until the phase-3 scorer matures and scores the call path-dependently; a
    flat call has no direction to score and keeps its outcome columns null
    forever (it stays in the ledger so the "how often did the advisor commit"
    denominator is honest).
    """

    __tablename__ = "advice_ledger"

    call_id: Mapped[str] = mapped_column(String, primary_key=True)
    # The call identity, also stored discretely for filter/group-by.
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    timeframe: Mapped[str] = mapped_column(String, nullable=False)
    strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    as_of_bar_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    # The recorded ticket. A flat call carries no levels (direction == "flat");
    # entry_low/entry_high/stop are null and targets_json is "[]".
    direction: Mapped[str] = mapped_column(String, nullable=False)
    entry_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    targets_json: Mapped[str] = mapped_column(String, nullable=False)
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    # The forecast probability attached to the call's direction (prob_up for a
    # long, prob_down for a short), the input to the calibration read — null for
    # a flat call or a demoted no-edge forecast (ADR-0071).
    forecast_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Relative to the sidecar's runs_dir; null when no runs_dir was wired (the
    # call is still recorded — the ledger does not depend on the artifact).
    artifact_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Outcome — filled by the phase-3 scorer once the horizon matures. Null means
    # "not yet scored" (still pending, or a flat call that never scores).
    outcome_class: Mapped[str | None] = mapped_column(String, nullable=True)
    realized_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    directional_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_advice_ledger_symbol", "symbol"),
        # The scorer scans for unscored rows (`outcome_class IS NULL`); the
        # aggregation layer lists recent scored ones.
        Index("ix_advice_ledger_outcome_class", "outcome_class"),
        Index("ix_advice_ledger_created_at", "created_at"),
    )

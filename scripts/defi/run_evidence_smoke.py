#!/usr/bin/env python
"""Plan 0079 Phase 4 — the BA-7 live evidence run (human-owned).

Drives the PRODUCTION code paths directly — the same OnchainPoolPriceAdapter
and scan_discrepancies the MCP tool uses — against a set of VALIDATED pools on
a live chain, and records the net-of-cost finding to runs/defi/. It does NOT
touch the running sidecar (no restart, no entrypoint edit); it just imports and
calls the shipped code. What it skips vs the MCP tool is only the pagination /
error-taxonomy wrapper — not the evidence, which is the ArbObservation list.

Answers the BA-7 question: do cross-pool discrepancies ever survive net-of-cost
at RPC observability, and (with --samples) for how long? A null result — nets go
negative, nothing clears cost — is a LEGITIMATE, valuable outcome that stops an
expensive arb-execution build. RPC-observed persistence is an UPPER BOUND on
capturability, never a capture guarantee.

Prereqs:
  1. Validate your pool set with validate_pools.py first (every pool_id must
     read a sane price). Only validated pools go in VALIDATED_POOLS below.
  2. An RPC endpoint URL for the chain (Base = cheap gas). Passed via env so it
     is never written to the artifact or the repo:
         $env:BASE_RPC_URL = "https://<your-base-rpc>"     # PowerShell
  3. Set est_gas_cost to a realistic round-trip gas figure in QUOTE-token units
     (e.g. USDC for a *-/USDC pair). Conservative > optimistic — an optimistic
     cost model fabricates opportunities.

Run (from the repo root, so runs/defi/ resolves and the package imports):
    $env:BASE_RPC_URL = "https://<your-base-rpc>"
    uv run python scripts/defi/run_evidence_smoke.py

Read-only: the adapter issues only eth_call. No key, no signing, no funds.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from market_analyser.data.adapters.onchain_pools import (
    OnchainPoolPriceAdapter,
    PoolConfig,
)
from market_analyser.data.errors import UpstreamDataError
from market_analyser.defi.discrepancy import DiscrepancyParams, scan_discrepancies

# === EDIT THIS BLOCK ========================================================

# The pool set — ONLY addresses that passed validate_pools.py. You need >= 2
# pools sharing the same `pair` for the screener to compare them. fee_bps is the
# venue's swap fee in basis points (confirm per DEX — it is a cost the screener
# subtracts, not read on-chain). base/quote orient the price as quote-per-base.
VALIDATED_POOLS: tuple[PoolConfig, ...] = (
    # PoolConfig(
    #     pool_id="0x...",            # from getPool/getPair, validated
    #     dex="aerodrome",
    #     chain="base",
    #     pair="WETH/USDC",
    #     base_token="0x4200000000000000000000000000000000000006",   # WETH
    #     quote_token="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
    #     fee_bps=30,
    # ),
    # PoolConfig(pool_id="0x...", dex="baseswap", chain="base", pair="WETH/USDC",
    #            base_token="0x4200...0006", quote_token="0x8335...2913", fee_bps=30),
)

PAIRS: tuple[str, ...] = ("WETH/USDC",)  # must match the pairs above
TRADE_SIZE = 0.5  # base-token size to price the spread for
EST_GAS_COST = 3.0  # round-trip gas in QUOTE units — be conservative
MIN_NET_SPREAD = 0.0  # threshold a net spread must clear to be "capturable"

# Persistence sampling: 1 = a single point-in-time read. To gauge how long a
# discrepancy lasts, take several samples spaced apart (the plan's phase-4
# "over a session"). Each sample is one full re-read of every pool.
SAMPLES = 1
SAMPLE_INTERVAL_SECONDS = 30.0

# ============================================================================


class _EnvRpcSecrets:
    """Minimal SecretsStore stand-in: the adapter only calls `.get(key)`. Returns
    the env RPC URL for the chain's reserved key so a one-off evidence run needs
    no secrets.json. The URL is never logged or written to the artifact."""

    _ENV: ClassVar[dict[str, str]] = {"base_rpc_url": "BASE_RPC_URL", "eth_rpc_url": "ETH_RPC_URL"}

    def get(self, key: str) -> str | None:
        return os.environ.get(self._ENV.get(key, ""), None)


def _one_sample(adapter: OnchainPoolPriceAdapter, params: DiscrepancyParams) -> dict:
    quotes = []
    for pair in PAIRS:
        quotes.extend(adapter.fetch_pool_quotes(pair, trade_size=TRADE_SIZE))
    observations = scan_discrepancies(quotes, params=params)
    return {
        "sampled_at": datetime.now(tz=UTC).isoformat(),
        "n_quotes": len(quotes),
        "quotes": [q.model_dump(mode="json") for q in quotes],
        "observations": [o.model_dump(mode="json") for o in observations],
    }


def main() -> int:
    if not VALIDATED_POOLS:
        print("VALIDATED_POOLS is empty — paste your validated set first.", file=sys.stderr)
        return 2
    if not any(_EnvRpcSecrets().get(k) for k in ("base_rpc_url", "eth_rpc_url")):
        print("Set BASE_RPC_URL (or ETH_RPC_URL) in the environment first.", file=sys.stderr)
        return 2

    adapter = OnchainPoolPriceAdapter(
        secrets_store=_EnvRpcSecrets(),  # duck-typed; adapter only calls .get()
        pools=VALIDATED_POOLS,
    )
    params = DiscrepancyParams(est_gas_cost=EST_GAS_COST, min_net_spread=MIN_NET_SPREAD)

    samples: list[dict] = []
    for i in range(max(1, SAMPLES)):
        if i:
            time.sleep(SAMPLE_INTERVAL_SECONDS)
        try:
            s = _one_sample(adapter, params)
        except UpstreamDataError as err:  # typed RPC failure (config/rate/outage)
            print(f"sample {i}: RPC failure — {type(err).__name__}: {err}", file=sys.stderr)
            continue
        samples.append(s)
        caps = [o for o in s["observations"] if o["capturable_at_threshold"]]
        best = max((o["net_spread"] for o in s["observations"]), default=None)
        print(
            f"sample {i}: {s['n_quotes']} quotes, {len(s['observations'])} obs, "
            f"{len(caps)} capturable, best net_spread={best}"
        )

    # --- write the runs/defi artifact (the BA-7 evidence record) ------------
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = Path("runs/defi")
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"{stamp}-plan-0079-cross-pool-evidence.json"
    artifact.write_text(
        json.dumps(
            {
                "plan": "0079",
                "purpose": "ADR-0072 BA-7 cross-pool arb-viability evidence",
                "chain": VALIDATED_POOLS[0].chain,
                "pairs": list(PAIRS),
                "trade_size": TRADE_SIZE,
                "est_gas_cost": EST_GAS_COST,
                "min_net_spread": MIN_NET_SPREAD,
                "pools": [p.model_dump(mode="json") for p in VALIDATED_POOLS],
                "capturability_caveat": (
                    "RPC-observed persistence is an UPPER BOUND on capturability, "
                    "not a capture guarantee; excludes MEV/searcher competition, "
                    "block-inclusion and inventory risk."
                ),
                "samples": samples,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {artifact}")
    ever = any(o["capturable_at_threshold"] for s in samples for o in s["observations"])
    print(
        "VERDICT: at least one net-of-cost-capturable observation appeared."
        if ever
        else "VERDICT: no discrepancy cleared net-of-cost — the honest-null prior holds "
        "(a legitimate no-go for an arb-execution build)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

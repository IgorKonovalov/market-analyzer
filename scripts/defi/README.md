# Plan 0079 — cross-pool discrepancy scanner: Phase 4 operator tooling

Phase 4 of [Plan 0079](../../docs/architecture/plans/done/0079-cross-pool-discrepancy-scanner.md)
is the **BA-7 live evidence run** ([ADR-0072](../../docs/architecture/adrs/0072-bounded-autonomy-and-prediction-market-execution.md)):
point the shipped read-only scanner at real DEX pools on a live chain and record
whether cross-pool price discrepancies ever survive **net of cost** (gas +
slippage + fees), and for how long. It is `human`-owned because it needs a live
RPC endpoint and human-verified pool addresses.

**A null result is a legitimate, valuable success.** If discrepancies vanish
net-of-cost, that stops an expensive, adversarial arb-execution build — exactly
what this evidence layer exists to decide before any execution is written.
RPC-observed persistence is an **upper bound** on capturability, never a capture
guarantee (a colocated searcher sees and executes faster than an RPC poller).

## Scripts

| Script | Purpose |
|---|---|
| [`validate_pools.py`](validate_pools.py) | Reads each candidate pool from *your* RPC and prints its reserves + marginal price, so a wrong/wrong-type address fails on screen instead of muddying the evidence. Fabrication-proof: it trusts no address, it reads the chain. |
| [`run_evidence_smoke.py`](run_evidence_smoke.py) | Drives the **production** `OnchainPoolPriceAdapter` + `scan_discrepancies` directly against a validated pool set, prints per-sample results, and writes the `runs/defi/` evidence artifact with a plain-English verdict. `SAMPLES>1` gauges persistence over a session. |

Both are read-only (the adapter issues only `eth_call` — no key, no signing, no
funds). The RPC URL is passed via env (`BASE_RPC_URL` / `ETH_RPC_URL`), so it is
never written to an artifact or committed. `runs/defi/` is gitignored — the
evidence artifact stays local.

## The v1 adapter's hard scope constraint

The v1 adapter computes `price = reserve_quote / reserve_base` — it **assumes
constant-product `x·y=k`**. That decides which pools are valid:

| Pool type | Valid? | Why |
|---|---|---|
| Aerodrome **volatile** (vAMM), Uniswap-v2 forks (Sushi/BaseSwap/Alien Base/SwapBased) | ✅ | Constant-product; expose `getReserves()` / `token0()` |
| Aerodrome **stable** (sAMM) | ❌ **silent trap** | `getReserves()` succeeds (won't revert), but the stable-curve marginal price ≠ `reserve_quote/reserve_base` → wrong price |
| Uniswap-v3 / Aerodrome **Slipstream** (concentrated liquidity) | ❌ | No `getReserves()`; a v3 Quoter source is a documented followup |

**Liquidity reality:** most WETH/USDC depth on Base is concentrated-liquidity
(Uni-v3 / Aerodrome Slipstream), which is out of scope. Finding a *second*
constant-product pool for the same pair may mean a smaller venue, and thin
second-venue liquidity is itself a Phase-4 finding — it caps real capturability.

## Verified addresses (Base mainnet)

All from official docs or BaseScan verified labels. **Cross-check each on
BaseScan before use.**

### Constant-product (v2-style) factories — derive pools from these

| DEX | v2 Factory | Pool getter | Source |
|---|---|---|---|
| Aerodrome | `0x420DD381b31aEf6683db6B902084cB0FFECe40Da` | `getPool(a, b, false)` | BaseScan verified label |
| SushiSwap V2 | `0x71524B4f93c58fcbF659783284E38825f0622859` | `getPair(a, b)` | Sushi official docs (cpAMM) |
| BaseSwap | `0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB` | `getPair(a, b)` | BaseScan verified label |
| Alien Base (v2) | `0x3e84d913803b02a4a7f027165e8ca42c14c0fde7` | `getPair(a, b)` | Alien Base docs |
| SwapBased | `0x04C9f118d21e8B767D2e50C946f0cC9F6C367300` | `getPair(a, b)` | BaseScan verified label |

- Aerodrome's `getPool(a, b, false)` — `false` selects the **volatile** pool; a
  stable pool would misprice (see scope table).
- Use each DEX's **v2** factory, never its v3 (e.g. Alien Base v3
  `0x0Fd83557b2be93617c9C1C1B6fd549401C74558C` is concentrated liquidity — out
  of scope).
- **`fee_bps` is a screener cost input, not read on-chain — confirm it per
  venue.** These forks each set their own swap fee; do not assume 30 bps.

### Tokens

| Token | Address | Note |
|---|---|---|
| WETH | `0x4200000000000000000000000000000000000006` | Base OP-stack canonical predeploy |
| USDC (native) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | Circle-issued; BaseScan verified + Circle docs |
| USDbC (bridged) | `0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA` | **Different token** — some v2 pools pair against this, not native USDC |

## Workflow

1. **Derive pool addresses (no guessing).** On a factory's BaseScan "Read
   Contract" tab (BaseScan builds calldata from the verified ABI, so the
   selector is correct by construction), call `getPair(WETH, USDC)` /
   `getPool(WETH, USDC, false)`. `0x000…0` means no such v2 pool exists.
2. **Validate.** Paste the returned pool(s) into `validate_pools.py`, set
   `BASE_RPC_URL`, run it. Only pools that print a sane "quote per base" price go
   forward.
3. **Run the evidence smoke.** Paste the validated set into
   `run_evidence_smoke.py`'s `VALIDATED_POOLS`, set a conservative `EST_GAS_COST`
   (quote-token units) and `TRADE_SIZE`, set `BASE_RPC_URL`, and run from the
   repo root. It writes `runs/defi/<ts>-plan-0079-cross-pool-evidence.json` and
   prints the verdict. Use `SAMPLES>1` to measure persistence over a session.

### Alternative: the agent/MCP path

To make the running sidecar's `scan_pool_discrepancies` tool return real data
(instead of the empty default), pass an explicit adapter with pools in
`api/__main__.py`'s `create_app(...)` call and set the `base_rpc_url` secret in
`secrets.json`, then restart:

```python
from market_analyser.data.adapters.onchain_pools import OnchainPoolPriceAdapter, PoolConfig
# ...
app = create_app(
    ...,
    pool_price_sources={"onchain": OnchainPoolPriceAdapter(
        secrets_store=secrets_store, pools=[PoolConfig(...), PoolConfig(...)])},
)
```

This is a temporary edit — **do not commit hardcoded pool addresses**, and the
RPC URL belongs in `secrets.json`, never in a committed file. The standalone
`run_evidence_smoke.py` is the cleaner Phase-4 vehicle (no sidecar edit, same
production code paths).

## Sources

- Aerodrome PoolFactory — https://basescan.org/address/0x420dd381b31aef6683db6b902084cb0ffece40da
- SushiSwap cpAMM contracts — https://docs.sushi.com/contracts/cpamm
- BaseSwap Factory — https://basescan.org/address/0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB
- Alien Base contracts — https://docs.alienbase.xyz/reference/contracts.md
- SwapBased Uniswap V2 Factory — https://basescan.org/address/0x04C9f118d21e8B767D2e50C946f0cC9F6C367300
- Native USDC on Base — https://basescan.org/token/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
- Circle USDC contract addresses — https://developers.circle.com/stablecoins/usdc-contract-addresses

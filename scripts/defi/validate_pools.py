#!/usr/bin/env python
"""Validate candidate cross-pool discrepancy-scanner pools against a live RPC.

Fabrication-proof: it does NOT trust any address you give it — it reads each
one from the chain and shows you the truth (reserves, token0 orientation, the
decimals-adjusted marginal price). If an address is wrong it reverts / mismatches
here, on your screen, instead of muddying the Phase-4 evidence smoke.

Usage (PowerShell, from the repo root):
    $env:BASE_RPC_URL = "https://<your-base-rpc>"
    uv run python scripts/defi/validate_pools.py

Then paste the CANDIDATES below (pool_id + base/quote token + expected pair).
Only pools that print a sane price belong in DEFAULT_POOLS.

Read-only: the only JSON-RPC method issued is eth_call (same as the adapter).

--- How to get a pool_id without guessing (fabrication-proof) ---------------
Derive it from the factory on-chain, via BaseScan "Read Contract" (BaseScan
builds the calldata from the verified ABI, so the function selector is correct
by construction — no memory, no code):

  Aerodrome PoolFactory  0x420DD381b31aEf6683db6B902084cB0FFECe40Da
     Read Contract -> getPool(tokenA=WETH, tokenB=USDC, stable=false)
     (stable=false => the VOLATILE constant-product pool; false is required —
      a stable pool would misprice under this adapter's x*y=k formula)

  SushiSwap V2 Factory   0x71524B4f93c58fcbF659783284E38825f0622859
     Read Contract -> getPair(WETH, USDC)
     (returns 0x000...000 if no such v2 pool exists — see the liquidity caveat)

  Other Base constant-product (Uniswap-v2-style) factories, all getPair(a,b):
     BaseSwap    0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB   (BaseScan verified)
     Alien Base  0x3e84d913803b02a4a7f027165e8ca42c14c0fde7   (v2 factory, per docs)
     SwapBased   0x04C9f118d21e8B767D2e50C946f0cC9F6C367300   (BaseScan verified)
     NOTE these forks each set their OWN swap fee — confirm fee_bps per venue
     (do NOT assume 30 bps); the fee is a screener input, not read on-chain.
     NOTE use each DEX's v2 factory, NOT its v3 factory (e.g. Alien Base v3
     0x0Fd83557b2be93617c9C1C1B6fd549401C74558C is concentrated-liquidity =
     out of scope).

Verified addresses (official docs / BaseScan verified labels — cross-check each
on Basescan before use):
  WETH   0x4200000000000000000000000000000000000006  (Base OP-stack predeploy)
  USDC   0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913  (NATIVE Circle USDC)
  USDbC  0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA  (BRIDGED — a DIFFERENT
         token; if a pool pairs against this, it is a different pair)

Liquidity caveat: most WETH/USDC depth on Base is concentrated-liquidity
(Uniswap-v3 / Aerodrome Slipstream) — OUT of scope for this v1 constant-product
adapter. Finding a SECOND viable constant-product WETH/USDC pool (beyond
Aerodrome vAMM) may mean BaseSwap / Alien Base / SwapBased / PancakeSwap-v2,
whichever actually holds reserves. Thin second-venue liquidity is itself a
Phase-4 finding (it caps real capturability).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

# --- fill these in from DEX docs / Basescan, then run -------------------------
# base_token / quote_token orient the price as quote-per-base (e.g. USDC per WETH).
CANDIDATES = [
    {
        "label": "aerodrome vAMM WETH/USDC",
        "pool_id": "0x<VERIFY>",
        "base_token": "0x4200000000000000000000000000000000000006",  # WETH
        "quote_token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
    },
    # add a 2nd constant-product WETH/USDC venue here (sushi/pancake-v2/baseswap)
]

_SEL_GET_RESERVES = "0x0902f1ac"  # getReserves()
_SEL_TOKEN0 = "0x0dfe1681"  # token0()
_SEL_DECIMALS = "0x313ce567"  # decimals()
_WORD = 32


def eth_call(rpc: str, to: str, data: str) -> bytes:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }
    req = urllib.request.Request(
        rpc, data=json.dumps(payload).encode(), headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    if "error" in body:
        raise RuntimeError(f"JSON-RPC error: {body['error']}")
    result = body.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RuntimeError(f"bad result: {result!r}")
    return bytes.fromhex(result[2:])


def word_uint(data: bytes, i: int) -> int:
    return int.from_bytes(data[i * _WORD : (i + 1) * _WORD], "big")


def word_addr(data: bytes) -> str:
    return "0x" + data[12:_WORD].hex()


def main() -> int:
    rpc = os.environ.get("BASE_RPC_URL")
    if not rpc:
        print("set BASE_RPC_URL first", file=sys.stderr)
        return 2
    ok = 0
    for c in CANDIDATES:
        label, pool = c["label"], c["pool_id"]
        base, quote = c["base_token"].lower(), c["quote_token"].lower()
        print(f"\n=== {label}  {pool}")
        if "<VERIFY>" in pool:
            print("  SKIP — pool_id placeholder not filled in")
            continue
        try:
            reserves = eth_call(rpc, pool, _SEL_GET_RESERVES)
            r0, r1 = word_uint(reserves, 0), word_uint(reserves, 1)
            token0 = word_addr(eth_call(rpc, pool, _SEL_TOKEN0))
            if token0 == base:
                rb_raw, rq_raw = r0, r1
            elif token0 == quote:
                rb_raw, rq_raw = r1, r0
            else:
                print(
                    f"  FAIL — token0 {token0} is neither base nor quote "
                    f"(is this the right pool / the right pair?)"
                )
                continue
            bd = word_uint(eth_call(rpc, base, _SEL_DECIMALS), 0)
            qd = word_uint(eth_call(rpc, quote, _SEL_DECIMALS), 0)
            rb = rb_raw / 10**bd
            rq = rq_raw / 10**qd
            if rb <= 0 or rq <= 0:
                print(f"  FAIL — non-positive reserve (base={rb}, quote={rq})")
                continue
            price = rq / rb
            print(f"  OK   token0={token0}")
            print(f"       reserves: base={rb:,.4f}  quote={rq:,.2f}")
            print(f"       marginal price = {price:,.2f} quote per base")
            print(
                "       -> eyeball this against the real WETH/USDC spot; "
                "if it's absurd the address is wrong"
            )
            ok += 1
        except Exception as err:
            print(f"  FAIL — {type(err).__name__}: {err}")
    print(
        f"\n{ok}/{len(CANDIDATES)} candidates validated. "
        f"Only OK ones with a sane price go into DEFAULT_POOLS."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

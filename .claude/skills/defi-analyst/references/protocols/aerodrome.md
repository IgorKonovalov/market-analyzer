# Aerodrome (Base)

The dominant DEX on Base, forked from Velodrome (which itself is descended from Solidly). Two distinct pool types and a ve-token reward layer sitting on top.

## Pool types

### vAMM and sAMM (the "classic" pools)

- **vAMM** — volatile-pair AMM, same constant-product math as Uniswap v2 (`x * y = k`).
- **sAMM** — stable-pair AMM, uses `x³y + xy³ = k` (concentrated liquidity around the peg, suited for stable-stable pairs like USDC/USDbC).

Fees are 0.05% for sAMM and 0.30% for vAMM by default but can be adjusted per pool. All fees go to LPs *plus* the gauge's voters (see below).

### Slipstream (v3-style concentrated liquidity)

Aerodrome's concentrated-liquidity pools, mechanically identical to Uniswap v3. Use the v3 mental model: pick a tick range, earn fees in-range, get IL outside. Fee tiers + tick spacings analogous to Uniswap v3 — verify the exact set on Aerodrome's docs since the tier list is shorter.

## The ve-token reward layer

This is what distinguishes Aerodrome from Uniswap.

1. AERO tokens are issued every epoch (one week).
2. **veAERO** is AERO locked for up to 4 years; longer lock = more vote weight. veAERO holders vote each epoch on **which pools** get the next epoch's emissions.
3. Pool LPs stake their LP tokens in the pool's **gauge** to earn AERO emissions proportional to votes.
4. Voters earn the pool's trading fees as a **bribe** plus any direct bribes posted by protocols wanting more votes.

The net effect: an LP's APR = trading-fee APR + AERO-reward APR, where the reward APR depends on the gauge's vote share that epoch. Vote shares change weekly.

## What to fetch for a position

- **For a classic LP**: `Pair.balanceOf(wallet)` for LP token balance, plus `Gauge.earned(wallet)` for accrued AERO rewards. Gauge address is `voter.gauges(pair_address)`.
- **For a Slipstream LP**: same as Uniswap v3 NFT position, plus the gauge layer for AERO accrual.
- **AERO emissions to the gauge**: query `voter.weights(pool)` and the global total to compute the gauge's share of the next epoch's emissions.
- **Bribes**: the bribe contract per gauge holds the rewards for voters; not directly relevant to LPs (those are for veAERO holders).

## What changes week-to-week

- **Reward APR.** A pool's AERO emissions are entirely a function of votes. A pool with 5% of votes this epoch may get 1% next epoch if a competitor offers bigger bribes. **Don't project last week's reward APR forward without checking current vote weights.**
- **Vote shares.** If you're holding veAERO and voting yourself, your bribe income depends on the pool you voted for — same volatility.

## Risks specific to Aerodrome

- **Reward APR is non-stationary** — see above. Always show fee APR and reward APR separately in any analysis.
- **AERO price is reflexive** — high emissions can dilute AERO price, which lowers the dollar value of the rewards, which lowers LP yield. The cycle is well-understood and has bitten Solidly-family forks before.
- **Gauge voting can be gamed** — protocols sometimes bribe heavily to attract LP capital, then stop bribing once the TVL is sticky. Look at the bribe history of the gauge before assuming next-epoch incentives will look like last-epoch's.
- **Smart-contract risk** is inherited from the Velodrome/Solidly fork lineage; multiple Solidly forks have had audits and time but also discovered issues. Treat as moderate-mature, not blue-chip-mature.

## What to flag in a Mode 2 / Mode 3

- AERO rewards being valued at issuance price rather than current market — recompute.
- A pool whose APR is >80% reward-driven: the position is effectively a directional bet on AERO.
- Gauge with declining vote share over the last 4 weeks — the reward APR is likely about to drop.
- Slipstream position out-of-range (same flag as Uniswap v3).

# Uniswap v3

Concentrated liquidity AMM. The LP picks a price range; the position only earns fees when the current price is inside that range, but earns much more than a v2 LP would for the same capital.

## Mechanics in one paragraph

A v3 position is liquidity deployed between two ticks (`tick_lower`, `tick_upper`). Tick spacing depends on the fee tier (e.g. 0.05% pools have tick spacing 10, 0.30% pools 60). When the current tick is inside the range, the position holds a mix of both assets (composition shifts with price) and earns trading fees. When the price crosses outside the range, the position becomes entirely one asset (the cheaper one) and earns nothing until price returns.

## Fee tiers

| Fee tier | Typical use                       | Tick spacing |
| -------- | --------------------------------- | ------------ |
| 0.01%    | Stable-stable (USDC/USDT)         | 1            |
| 0.05%    | Stable-volatile (USDC/ETH)        | 10           |
| 0.30%    | Volatile-volatile (ETH/WBTC)      | 60           |
| 1.00%    | Exotic / low-volume pairs         | 200          |

The fee tier with the most liquidity is *not* always the best for an LP — sometimes a thinner tier earns more per dollar because there's less competition. Look at fee revenue per dollar of TVL by tier, not raw TVL.

## What to fetch for a position

A v3 position is an ERC-721 NFT minted by `NonfungiblePositionManager` (one per chain). Inputs you need:

- `tokenId` (from the user's wallet).
- `positionManager.positions(tokenId)` → `(nonce, operator, token0, token1, fee, tickLower, tickUpper, liquidity, feeGrowthInside0LastX128, feeGrowthInside1LastX128, tokensOwed0, tokensOwed1)`.
- `pool.slot0()` → current tick + price. (Pool address derivable from `(token0, token1, fee)`.)
- Fee math: `tokensOwed0` and `tokensOwed1` are the fees already accumulated *to the position struct's last update*. To get up-to-the-second fees, you need to recompute from `feeGrowthInside0/1` vs. the pool's current global fee growth — annoying enough that the Uniswap interface and most SDKs do it for you. The subgraph also exposes `collectedFeesToken0/1`.

## In-range vs out-of-range

```
in_range = pool.tick_lower <= pool.current_tick < pool.tick_upper
```

The single most informative number about a v3 LP's performance is **% of position lifetime in-range**. Out-of-range > 30% is usually a sign the user picked the range too narrow or didn't rebalance.

## Impermanent loss math (concentrated liquidity)

IL in v3 is bounded by the range — the worst case is the position becomes 100% of the cheaper asset at one end of the range. But for a narrow range, IL accrues much faster than the equivalent v2 LP at the same price move.

Closed form for a position in `[P_a, P_b]` with current price `P`:

- Position value at entry (price `P_0`) vs current (price `P`) — the framework is the standard CLP math; spelled out in Uniswap's v3 whitepaper §6 if you need it.
- For sanity-checking: at `±10%` move on a `±20%` range, expect IL roughly 2-3x the v2 equivalent.

## Range strategy archetypes

- **Wide range** (e.g. ±50% around current). Lower fee density, lower IL risk, lower turnover. Good for set-and-forget.
- **Narrow range** (e.g. ±5% around current). High fee density, high IL, frequent out-of-range events unless rebalanced. Suitable for tight-range pairs (stable-stable, LST-ETH).
- **One-sided** (range entirely above or below current price). Effectively a limit order. Earns 0 fees until price hits the range.

## Slipstream / v3-style on other chains

Aerodrome, Velodrome, PancakeSwap v3, etc. are all Uniswap v3 forks (often with their own fee-tier set and gauge layers). The mechanics are identical; the differences live in (1) fee tier choices, (2) reward emissions on top of fees, (3) governance / gauge votes that direct rewards. See `aerodrome.md` for the Aerodrome-specific layer.

## What to flag in a Mode 2 / Mode 3

- Position out-of-range for > 30% of its lifetime.
- Realized IL > realized fees for > 60 days (structural loss to HODL).
- Position is the entire pool's TVL on its current tick (concentration: a single trade can move the price across your whole range).
- Narrow-range position with no rebalance discipline visible from the user's history.

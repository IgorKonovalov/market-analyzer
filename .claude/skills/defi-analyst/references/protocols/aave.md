# Aave v3

Lending market. The user supplies collateral, optionally borrows against it, and the protocol liquidates the position if the **health factor** drops to 1.

## Health factor

```
HF = Σ(collateral_i * liquidation_threshold_i) / Σ(borrow_i)
```

All values in the protocol's price units (USD-denominated via the Aave oracle).

- HF > 1 → safe.
- HF = 1 → liquidatable.
- HF < 1 → liquidator can repay part of the debt and seize collateral at a discount (the **liquidation bonus**, usually 5-10%).

Authoritative source: `Pool.getUserAccountData(user)` on the Aave Pool contract. Subgraph data lags by 1-30 blocks — use RPC for any decision where seconds matter.

## LTV vs Liquidation Threshold

- **LTV** (loan-to-value) — the maximum you can *borrow* against the asset.
- **LT** (liquidation threshold) — the level at which you get *liquidated*.

LT is always higher than LTV (e.g. ETH might be 75% LTV / 78% LT). The gap is the user's safety buffer; max-borrow at the LTV ceiling leaves you 3 percentage points of price drop before HF = 1.

## E-mode (efficiency mode)

For correlated assets (e.g. ETH ↔ wstETH ↔ weETH), Aave allows e-mode with higher LTV (often 90%+) and tighter LT. The catch: you can only borrow assets *within the e-mode category*. Useful for leveraged staking; dangerous if the correlation breaks (e.g. an LST de-pegs).

## Isolation mode

Some assets can only be used as collateral in isolation (you can't combine them with other collateral). Designed for newly listed / lower-quality assets. Caps the user's borrow against that asset.

## What to fetch for a position

1. **Health factor & current debt:** `Pool.getUserAccountData(wallet)` → `(totalCollateralBase, totalDebtBase, availableBorrowsBase, currentLiquidationThreshold, ltv, healthFactor)`.
2. **Per-asset balances:** the user holds aTokens for each supplied asset and variable/stable debt tokens for each borrowed asset. Read balances via `ERC20.balanceOf`.
3. **Per-asset rates:** `Pool.getReserveData(asset)` returns the current supply APY and borrow APY.

## Distance to liquidation

For a single-collateral, single-borrow position, the collateral price at which HF = 1:

```
P_liquidation = (current_debt_usd / liquidation_threshold) / collateral_amount_native
```

Expressed as a percentage move: `(P_liquidation / current_price) - 1`. Anything inside one weekly standard deviation of the collateral's return distribution is risky.

For multi-asset positions, the math is more involved (need to know which asset moves and how) — bail to a Monte Carlo or quote it per-asset.

## Risks specific to Aave

- **Liquidation cascades** — if a major collateral (ETH, BTC) drops fast, many positions liquidate at once, gas prices spike, oracle updates lag, and HFs can pass through 1 without the protocol catching them. The 2022 Mango incident and others have shown this isn't theoretical.
- **Oracle dependence** — Aave uses Chainlink (and Pyth on some chains). A stale or attacked feed is a systemic failure mode. Aave v3 has circuit breakers; verify they're enabled on the chain in question.
- **Frozen / paused reserves** — governance can freeze a reserve. Frozen reserves can still be borrowed against / repaid, but no new supply or borrow. Paused = nothing works. Check `Pool.getConfiguration(asset)`.
- **GHO** (Aave's stablecoin) — interest rate is set by governance, not a market. Treat like any other stable for peg purposes, but be aware the rate can change on a vote.

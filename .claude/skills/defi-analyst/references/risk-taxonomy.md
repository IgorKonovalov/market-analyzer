# Risk taxonomy

Five categories. A Mode 3 audit walks every category for the pool or position in scope, states the finding, cites the evidence, and gives the threshold used to flag (or "no threshold defined — raw number" if undefined).

The categories aren't independent — a stable pool with a depeg event also has liquidation cascade risk for any lender holding it as collateral. Mention the linkage when it matters; don't double-count.

## 1. Smart contract risk

What can the protocol's code do to you that the design didn't intend?

**Inputs:**
- Protocol age — when was the contract deployed? (DefiLlama protocol page → "Inception".)
- Audit history — how many audits, by whom, when was the latest, did the audit cover the version currently deployed?
- Exploit history — has *this* protocol or a fork of it been exploited? Was money returned?
- TVL trend — sudden TVL drops are sometimes preludes to a known issue being exited by insiders.
- Upgradeability — is the contract upgradeable? By whom (multisig threshold)? Time-locked?

**Thresholds we flag:**
- Protocol age < 6 months (without exception).
- No audit in the last 12 months on the *currently deployed* version.
- Any past exploit on this exact contract that hasn't been followed by a complete redeploy + new audit.
- Upgradeable contract with a 2-of-N multisig and no time lock — too easy to rug.

## 2. Oracle / peg risk

Does the position's value depend on something staying pegged or correctly priced?

**Inputs:**
- For stablecoins: historical peg deviation (largest drawdown from $1.00 in the last 365 days), recovery time, mechanism (overcollateralized like DAI/GHO, fiat-backed like USDC, algorithmic like the dead ones).
- For LSTs / LRTs (stETH, weETH, ezETH): historical discount to underlying, redemption mechanism, queue length.
- For wrapped assets (cbBTC, WBTC): custodian, attestation history, withdrawal liveness.
- For lending positions: which oracle does the protocol use? Chainlink, Pyth, or a TWAP from a DEX? When did it last fail?

**Thresholds we flag:**
- Stablecoin peg deviation > 2% in the last 90 days (e.g. USDC's March 2023 depeg would have triggered this).
- LST discount > 3% currently or in last 90 days.
- Lending oracle dependency on a single source without a fallback (e.g. a single Chainlink feed and the protocol pauses on staleness — that's a feature, but it's also a halt risk).

## 3. Liquidation risk (lending only)

For Aave / Compound / Morpho / similar lending positions.

**Inputs:**
- Current health factor (HF).
- Loan-to-value (LTV) and liquidation threshold (LT) of the collateral asset.
- Distance to liquidation: at what collateral price does HF = 1?
- Liquidation penalty (typically 5-10%).
- Available liquidity to repay: can you repay this loan quickly if you need to, or are you stuck waiting for utilization to drop?

**Thresholds we flag:**
- HF < 1.5 — actively risky. HF < 1.2 — already in scope of a sharp move triggering liquidation.
- Single collateral asset providing > 70% of borrow capacity — no diversification.
- Distance to liquidation < 1 standard deviation of the collateral's 30-day return — liquidation is within "normal" weekly volatility.

## 4. Impermanent loss / range risk (LP only)

For DEX LPs (Uniswap v2/v3, Aerodrome, Curve, etc.).

**Inputs:**
- For v2-style (constant-product) LPs: realized volatility of the pair over the holding period, expected IL at ±20%/±50%/±100% moves.
- For v3 ranged LPs: current price vs. range bounds, % of position lifetime in-range, fees earned vs. theoretical max if always in-range.
- For Curve / stable pools: imbalance ratio between pool assets — large imbalances precede depegs.

**Thresholds we flag:**
- Position out-of-range > 30% of its lifetime — likely earning much less than the user thinks.
- Realized IL > fees earned for > 60 days — position is structurally losing to HODL.
- Curve pool imbalance > 80/20 — one side is being aggressively swapped *out*, often a leading indicator of a depeg.

## 5. Concentration / exit risk

Can you actually exit the position at the marked value, and is the position too large for the pool?

**Inputs:**
- Pool TVL.
- User's share of pool TVL.
- 24-hour pool volume.
- For lending: utilization rate (high utilization means you may not be able to withdraw your supply immediately).

**Thresholds we flag:**
- User's position > 5% of pool TVL — exit will materially move the price.
- 24h volume < 10% of pool TVL — illiquid; LPs are providing more depth than the market uses.
- Lending utilization > 90% — supply may be temporarily un-withdrawable; not loss per se, but liquidity risk.
- For ranged LPs: the position's tokens are concentrated in a single tick range where withdrawal could be all in the side-that-decreased.

## Red lines

End every Mode 3 audit with three to four "if this happens, my assessment changes" lines. These should be **observable**, not vibes. Good red lines:

- "TVL drops below $5M (currently $14M)."
- "USDC trades below $0.98 on Chainlink for > 1 hour."
- "Aave HF drops below 1.5 (currently 2.1) — would happen at ETH = $X."
- "Aerodrome's slipstream gauge gets unvoted next epoch (currently ~3% of votes) — would cut reward APR roughly in half."

Bad red lines: "if the market goes down" (not observable), "if the protocol gets hacked" (true of everything, not actionable).

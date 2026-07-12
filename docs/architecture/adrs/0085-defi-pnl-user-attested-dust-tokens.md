# ADR-0085 — DeFi P&L: user-attested dust-token zero-value override

> **Status:** proposed (accepts at Plan 0093 close)
> **Date:** 2026-07-12
> **Related plan(s):** [Plan 0093](../plans/0093-defi-pnl-dust-token-override.md) (implements this)
> **Amends:** [ADR-0036](0036-defi-pnl-reconstruction.md) — narrowly, on the "an unpriceable leg marks the position incomplete, never zeroed" rule, for **user-attested** dust tokens only. Every other ADR-0036 invariant (block-time pricing, average-cost lots, determinism-by-snapshot, machine-never-fabricates-a-value) stands. Builds on [ADR-0082](0082-defi-pnl-partial-totals-and-windowed-lp-profitability.md) (partial totals).

## Context

ADR-0036's loud-failure rule is deliberate: a leg the engine cannot price marks its position `incomplete` and is **never zeroed**, because the engine cannot tell a $10 dust token from a $5,000 one, and a silently-zeroed large position is a confident, invisible error. ADR-0082 softened the *wallet total* (a partial sum over complete positions) but a token that cannot be priced still blocks its own position.

The 2026-07-12 consolidated live smoke ([`../consolidated-smoke.md`](../consolidated-smoke.md), C1) confirmed on the test wallet that the "Wanderers" token `base:0xef0fd52e65ddcdc201e2055a94d2abff6ff10a7a` is unpriceable even via the Alchemy fallback (no coverage), so that one non-LP position stays `incomplete` — 4/5 reconstruct, the wallet total is honestly partial. The originally-proposed fix (a keyed price source for exotic tokens, carried from Plans 0087/0088) is disproportionate: **the user attests this token is dust — negligible value, costs nothing.**

The tension is real. Loud-failure exists precisely so the machine never invents a value for what it cannot price. But the *user* has knowledge the engine does not: that this specific token is dust. The honest resolution is not to make the machine guess (a heuristic "value < $X → 0" is impossible — the value is exactly what we cannot read), but to let the **user attest** the token is negligible, explicitly and auditably.

## Decision

We add a **user-attested dust-token override**: a user-maintained list of `chain:address` token keys (the ADR-0036 `token_key` form) that the P&L replay values at **$0** instead of failing on a missing price.

1. **Attested, not inferred.** A token is dust only because the *user* listed it — never because the engine guessed from an unreadable value. This preserves ADR-0036's core ("the machine never fabricates a value"): the machine still refuses to guess; the human declares the value negligible and owns that declaration.
2. **Zero in the price path, non-blocking.** During replay, a dust-listed token resolves to a `$0` price wherever a block-time price is required, so its legs contribute $0 to basis / realized / unrealized and its unpriceability no longer raises `_MissingPrice`. The position completes.
3. **Never silent.** A position that had a dust token zeroed carries a `notes` entry naming the token(s) valued at $0 by config, so the override is visible in the output — an attested zero is still a *disclosed* zero.
4. **Default unchanged.** Any token **not** on the list keeps ADR-0036's loud-failure behavior exactly. The override is opt-in, per-token, and narrow.
5. **Deterministic.** The dust list is a run input (like the price snapshots and `now`), so a re-run with the same list is byte-identical.

The list lives in the app config (ADR-0006 `config.json`, non-secret user config — distinct from the ADR-0038 secrets), e.g. `defi_dust_tokens: ["base:0xef0fd52e…"]`, and is threaded from the job into `compute_wallet_pnl`.

## Consequences

**Positive:**
- One user-attested dust token stops blocking an otherwise fully-reconstructed position; the test wallet reaches 5/5 complete without a new price source.
- No new dependency, no new data source, migration-free (a config field + an engine parameter).
- The audit property survives: the zero is explicit, user-owned, per-token, and disclosed in `notes`.

**Negative / the price we pay:**
- **A mis-listed non-dust token would silently zero real value.** This is the exact failure loud-failure guards against — re-admitted, but only under explicit user attestation and with a disclosing note. Mitigated by: the list is opt-in and user-maintained; the default stays loud-failure; the note surfaces every zeroed token so a mistake is visible on the next read.
- **A second run-input to carry for determinism** (the dust list), same category as the price snapshots.
- **Config, not per-wallet.** A dust designation is global across wallets in the first cut; a token that is dust in one wallet is dust everywhere. Acceptable — dust is a property of the token, not the holder.

## Alternatives considered

- **Build a keyed price source for the exotic token** (the Plan 0087/0088 followup). Rejected as disproportionate: sourcing a price for a token the user calls dust is effort spent to value something declared worthless; and many exotics have no priced venue at all.
- **Auto-classify dust by a value threshold** (`value < $X → 0`). Rejected: the token is dust *because it cannot be priced*, so there is no value to threshold — any auto-rule reintroduces the machine-guesses-a-value failure ADR-0036 forbids. User attestation is the only honest zero.
- **Leave it `incomplete` forever.** Rejected: the user has the domain knowledge, the position is otherwise complete, and ADR-0082's partial-total already proved we would rather report the reconstructable truth than block on one leg.
- **Drop the token's legs entirely** (exclude, don't zero). Rejected in favor of an explicit $0: dropping hides the token from the reconstruction, while a disclosed $0 keeps it visible in `notes` — the same honesty argument that made ADR-0082 flag rather than silently sum.

# ADR-0044 — Segregated trade-secret store: OS keychain via keyring

> **Status:** proposed — accepts at the execution skeleton plan close ([Plan 0044](../plans/0044-execution-skeleton.md))
> **Date:** 2026-06-05
> **Related plan(s):** [Plan 0044](../plans/0044-execution-skeleton.md) (integrates the store)
> **Related ADRs:** [ADR-0025](0025-trade-execution-feasibility.md) (invariant 4 — segregated secret store; this is its mechanism), [ADR-0038](0038-third-party-api-key-storage.md) (the *read-only* third-party key store this is deliberately distinct from), [ADR-0011](0011-bearer-secret-transport.md) (the per-launch IPC bearer — a different, lower-value secret class), [ADR-0043](0043-execution-venue-protocol.md) (the venue that receives the injected credentials)

## Context

[ADR-0025](0025-trade-execution-feasibility.md) invariant 4 requires that **trade-permissioned keys live in the OS keychain (Windows Credential Manager / DPAPI), never in `config.json`, never in the IPC bearer path, never logged or serialized**, and that CEX keys be configured **withdrawals-disabled + IP-allowlisted**. ADR-0025 named the requirement but deferred the mechanism to a dedicated ADR. This is it.

A trade-permissioned secret is a **categorically higher-value** secret than anything the repo holds today. [ADR-0011](0011-bearer-secret-transport.md)'s per-launch localhost bearer is worthless after the process exits; [ADR-0038](0038-third-party-api-key-storage.md)'s `0600` secrets file holds **read-only** third-party data keys (Zerion, a Binance *read* key) whose theft leaks data but moves no funds. A Binance **trade** key, or a Polygon **hot-wallet private key**, can move money. Co-mingling it with the read-key file would collapse exactly the value distinction that justifies a separate store.

A 2026-06-05 research pass (adversarially verified) confirmed the pragmatic state: the `keyring` library backed by Windows Credential Manager / DPAPI is the standard Python pattern for this, and its **realistic limit** is that **DPAPI-protected secrets are recoverable by an attacker who already has the user's logged-in session** (they can invoke the same DPAPI that decrypts them). The keychain protects against casual disk inspection, file copying, and repo leakage — not against malware running as the logged-in user. That is why ADR-0025 pairs the keychain with **withdrawals-disabled + IP-allowlist** (defense-in-depth: even a stolen key can't withdraw and only works from one IP). The user chose this keychain mechanism (2026-06-05); execution is testnet-first, so the first keys stored are worthless testnet keys, but the store is designed for the real-funds future.

## Decision

We will store **trade-permissioned secrets** (the Binance trade-API key/secret; later, a Polygon hot-wallet private key) in the **OS keychain via the `keyring` library** (Windows Credential Manager / DPAPI), in the isolated `execution/` domain — **never** in `config.json`, **never** in the [ADR-0038](0038-third-party-api-key-storage.md) read-key file, **never** in the [ADR-0011](0011-bearer-secret-transport.md) IPC bearer path, **never** logged or serialized into any plan/ADR/diagram/log. Credentials are read from the keychain only at the point of injection into the `ExecutionVenue` ([ADR-0043](0043-execution-venue-protocol.md)) and never echoed. CEX keys are operated **withdrawals-disabled + IP-allowlisted** as a required complement (the keychain is not the only control). The threat model is stated explicitly: this protects against disk/file inspection, copying, and repo leakage; it does **not** protect against malware running as the logged-in user (the DPAPI same-session limit). For the higher-value **hot-wallet** key, a **hardware signer** is the documented future escalation, and an app-level encrypted store with a session passphrase + a hard spend cap is the documented fallback — both deferred until the DeFi/Polymarket signing path is actually built.

## Consequences

### Positive
- **[ADR-0025](0025-trade-execution-feasibility.md) invariant 4 is satisfied with the named mechanism**, keeping the high-value trade secret out of every existing lower-value path and out of the repo.
- **The value distinction is preserved**: read-only keys stay in the [ADR-0038](0038-third-party-api-key-storage.md) file; trade keys go to the keychain. No accretion collapses the two.
- **Honest threat model.** The DPAPI same-session limit is documented, not hidden, and the withdrawals-off + IP-allowlist complement is mandatory — so the security posture is defense-in-depth, not a single point of false comfort.
- **Testnet-first lowers the initial stakes**: the mechanism is proven storing worthless testnet keys before any real key exists.

### Negative
- **DPAPI does not stop a same-login attacker.** Malware running as the user can recover the key. This is a real, documented limit; the mitigation is operational (withdrawals-off, IP allowlist, spend caps), not cryptographic.
- **A hot-wallet private key is higher-value than a CEX API key** and DPAPI alone is a weaker fit for it — which is why the hardware-signer escalation is named now and the wallet signing path is deferred, not bolted onto this baseline.
- **`keyring` is a new exact-pinned dependency** under the cooldown/pin policy, with a platform-specific backend (Windows Credential Manager) whose behavior must be verified on the target machine.
- **Key rotation and revocation are operational burdens** the repo did not have; a compromised key means rotating at the exchange and re-storing — a runbook this ADR implies but does not automate.

### Neutral
- `proposed` until [Plan 0044](../plans/0044-execution-skeleton.md) integrates and closes; accepts there.
- The IPC bearer ([ADR-0011](0011-bearer-secret-transport.md)) and read-key file ([ADR-0038](0038-third-party-api-key-storage.md)) are unchanged — this is a third, higher-value secret class alongside them, not a replacement.

## Alternatives considered

### Alternative A — Reuse the [ADR-0038](0038-third-party-api-key-storage.md) `0600` read-key file
Put trade keys in the existing secrets file. **Rejected** because a trade key (moves money) is a categorically higher-value secret than a read key (leaks data), and co-mingling them collapses the value distinction [ADR-0025](0025-trade-execution-feasibility.md) draws. The keychain is the named higher-assurance store; the file stays for read-only keys.

### Alternative B — Encrypted file + session passphrase as the baseline
App-level encryption, unlocked once per session, independent of DPAPI (so a same-login attacker without the passphrase can't read it). **Rejected as the baseline** because it adds per-session unlock friction and a passphrase to manage, and the keychain is [ADR-0025](0025-trade-execution-feasibility.md)'s explicitly-named pattern. It is **retained as the documented fallback/escalation** for the higher-value hot-wallet key, where the DPAPI limit bites hardest.

### Alternative C — Hardware signer only
Require a hardware device for all signing. **Rejected as the day-one CEX mechanism** because a CEX **API key** is not a signing key — it cannot be held on a hardware signer — and mandating hardware adds setup burden the testnet-first prototype doesn't need. It **is** the named future escalation for the Polygon hot-wallet key, where hardware signing genuinely applies.

## Notes
- **No secret value, ever** — this ADR names secret *classes* (trade-API key, hot-wallet private key), never a value, mirroring [ADR-0025](0025-trade-execution-feasibility.md). Any execution code that logs or serializes a key is an immediate review blocker.
- **Withdrawals-off + IP-allowlist is not optional** — it is the operational complement that makes the DPAPI limit tolerable for a CEX key.
- The hot-wallet key is **out of scope until** the DeFi/Polymarket signing path is built; this ADR's baseline governs the Binance trade key, with the wallet escalation path recorded so it isn't rediscovered.

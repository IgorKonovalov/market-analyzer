# ADR-0063 — In-house renderer-side i18n via typed catalogs + structured sidecar reason-codes

> **Status:** accepted (2026-07-10, at Plan 0069's close)
> **Date:** 2026-07-08
> **Related plan(s):** [0069-russian-localization](../plans/0069-russian-localization.md)

## Context

The user wants the whole desktop UI available in Russian (full parity — chrome, static content, and the sidecar-generated prose the UI shows), with English staying the default. This is a real decision rather than a no-brainer because of four forces specific to this app:

1. **The primary control surface is the agent, not the renderer** (ADR-0015). The user drives the app by talking to Claude Code, which already re-expresses tool output — including `recommend`'s rationale — in the user's language for free. So in-app translation is only *needed* for the surfaces the **renderer renders verbatim**: `RecommendationsView`, `ForecastView`, error toasts, chrome, glossary. Localizing the whole sidecar would translate text the agent already handles.

2. **Dependency discipline is strict.** ADR-0012 imposes a 14-day cooldown and ADR-0013 exact-pins every direct dependency; ADR-0009 set an in-house ethos for the data layer. `format.ts` is deliberately tiny and already `en-US` with a comment deferring to "a future i18n plan". The app is **single-user, two locales** (`en`, `ru`) with no near-term third — a shape that a hand-rolled resolver covers without importing an i18n framework's plural/interpolation/negotiation machinery.

3. **Native `Intl` already does the hard part.** `Intl.PluralRules('ru')` gives Russian's three plural categories and `Intl.NumberFormat` (already used in `format.ts`) gives formatting — both with zero dependencies. The gap between "use `Intl`" and "use react-i18next" is small for two locales.

4. **The authored prose is centralized; the untranslatable tail is not.** True authored sidecar prose lives almost entirely in `advisor/fusion.py` (blockers, directional rationale, `basis` fragments, ~14 fixed gate-check labels) and `forecast/explain.py` (two constants). But a scattered tail — ~11 route files' HTTP `detail=` strings, many of them dynamic `str(exc)` from `data/errors.py`, plus upstream-passthrough enums and external news headlines — **cannot be cleanly or honestly translated in-house**. Any approach has to draw a boundary here, not pretend the whole surface is translatable.

A paired sub-decision rides along: whether to localize numbers/dates/currency to `ru-RU`. Traders read `en-US` financial formatting (`1,234.56`, `+12.34%`, `$`) natively, and `ru-RU` (`1 234,56`) mixes awkwardly with USD amounts.

## Decision

We will localize **in the renderer** with an in-house, zero-dependency `t(key, params?)` over typed `en`/`ru` catalogs, using native `Intl.PluralRules` for pluralization and leaving `Intl.NumberFormat` at `en-US`. **English is both the default locale and the test-suite locale**, so existing renderer specs stay green unchanged. The **sidecar remains English-authoritative and negotiates no locale**: for the finite authored prose the renderer displays verbatim, `advisor/fusion.py` and `forecast/explain.py` additionally emit structured `{code, params}` **reason-codes** that the renderer translates from its catalogs, while their existing English prose fields are **preserved untouched** for the agent/MCP consumer. Dynamic upstream text — external headlines, `str(exc)` data-layer errors, raw upstream classification values — **stays English by design**, a documented seam. Numbers, dates and currency stay `en-US`.

## Consequences

### Positive
- **One catalog, one i18n language.** All translatable strings live in `desktop/renderer/locales/{en,ru}.ts` (plus locale-keyed glossary) — no second catalog in Python, no split source of truth.
- **Zero new dependencies** — honors ADR-0012/0013 and the ADR-0009 in-house ethos; nothing to pin, nothing under cooldown.
- **No locale plumbing on the sidecar** — no `Accept-Language` on every HTTP/MCP endpoint, no touching `data/errors.py`.
- **Cleans a layering smell** — presentation prose leaves the data/fusion layer as codes; the sidecar ships facts, the renderer owns wording.
- **Agent path untouched** — the English prose fields stay, so the `recommend` tool's agent-facing output is unchanged; reason-codes are purely additive.
- **Correct Russian plurals for free** via `Intl.PluralRules`; tests stay green because `en` is the default and test locale.

### Negative
- **Each authored fact has two representations** (English prose + reason-code) in `fusion.py` — a drift risk. Mitigated by co-generating the code beside the prose and a `directional ⟺ reason_codes present` invariant test, but the duplication is real maintenance cost.
- **We own a slice of i18n we'd otherwise import** — interpolation, plural selection, key fallback — and must test it. A hand-rolled `t()` is small but not free.
- **The UI is honestly bilingual at the seams** — dynamic `str(exc)` errors, external headlines, and symbol names render English even in Russian mode. Correct, but a visible inconsistency.
- **No extraction tooling** — a future third language means hand-authoring another full catalog and keeping it in parity manually (the parity test helps, authoring effort does not shrink).

### Neutral
- Catalogs version with the renderer, not the sidecar.
- The glossary schema becomes locale-keyed (prose fields carry `en`/`ru`; structural keys unchanged).

## Alternatives considered

### Alternative A — Library + Python i18n layer
react-i18next in the renderer plus a locale-negotiated, gettext-style i18n layer in the sidecar (an `Accept-Language` header, prose resolved server-side). Rejected because it adds a pinned dependency under the cooldown, forces **two catalogs across two stack languages**, and pushes locale negotiation onto every route — including `data/errors.py`, whose messages are dynamic — for a single-user, two-locale app. The cost dwarfs the benefit, and it localizes text the agent already handles.

### Alternative B — Renderer-only, sidecar stays English
Translate chrome + glossary only; leave all sidecar prose English and rely on the agent to translate it in conversation. Rejected because `RecommendationsView`, `ForecastView`, and error toasts would render English **verbatim** in Russian mode — which walks back the full-parity requirement the user explicitly chose.

### Alternative C — Localize numbers to `ru-RU`
Render numbers/dates/currency in Russian convention via `Intl` `ru-RU` in `format.ts`. Rejected: traders read `en-US` financial formatting natively, `1 234,56 $` mixes awkwardly with USD amounts, and it would churn the deliberately-tiny `format.ts`. `en-US` is retained.

## Notes

- Grounding for the "prose is centralized, tail is untranslatable" claim: an inventory of `src/market_analyser/` sidecar prose sites (2026-07-08) found authored prose concentrated in `advisor/fusion.py` and `forecast/explain.py`; the `Signal.reason` contract field is reserved but populated by no strategy; the glossary is entirely renderer-owned.
- **Condition/signal enums are structured codes, not parsed prose (Plan 0069 phase 4b, added 2026-07-10).** The first pass (phase 4) coded the rationale, blocker, and gate-check surface but left `RecommendationBasis.conditions`/`signals` as English *prose* lists (`"trend=up"`, `"rsi: position=long, fresh_signal"`), and the `reason.conditions` code carried raw enum values. Having the renderer parse that prose to translate it would violate this ADR's core principle — the sidecar ships facts, the renderer owns wording. Because every condition/signal value is a **closed** vocabulary (`Trend`, `MomentumStance`, `VolumeStance`, pattern `Direction`, `current_position`, the 13 fixed candlestick pattern names), the fix stays inside the reason-code model: `_build_basis` co-emits additive `condition_codes`/`signal_codes` (`list[ReasonCode]`) beside the untouched prose, with the enum values as **raw tokens in `params`**, and the renderer translates the tokens through an enum-label catalog. This extends — does not change — the reason-code contract: still `{code, params}`, still additive, still English-prose-preserving.
- **`fallback_reason` is the boundary case that stays English.** The forecast-basis `fallback_reason` (Plan 0066) is dynamic prose composed with embedded counts (`"v2-full unavailable: 3 of 240 bars survived the join (floor 500)"`), not a closed vocabulary — so it is *not* tokenized and joins the documented English-residue seam. The rule the two cases together establish: **closed-vocabulary authored strings become reason-codes; dynamic/composed/external strings stay English.**
- Extends the spirit of [ADR-0039](0039-renderer-theming-localstorage.md) (renderer-owned presentation preferences in `localStorage`) — the locale preference joins the theme preference in the same store shape.

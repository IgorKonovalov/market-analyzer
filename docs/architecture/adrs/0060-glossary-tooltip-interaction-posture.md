# 0060 — Glossary tooltips: a scoped exception to the advisory panels' no-interactive-element posture

> **Status:** accepted (Plan 0065 close, 2026-07-09)
> **Created:** 2026-07-08
> **Related:** [ADR-0029](0029-advisory-recommendation-boundary.md) (the advisory boundary — the app recommends, never acts), [ADR-0025](0025-trade-execution-feasibility.md) (execution is an untaken decision — no order path exists), [ADR-0058](0058-forecast-recommendation-explainability.md) (the explanation surfaces the tooltips annotate), [ADR-0046](0046-mcp-large-result-delivery.md) (the small-wire posture the glossary delivery respects)

## Context

The Forecast and Recommendation panels are deliberately **inert**: they display conditions and an advisory call but carry no controls that *do* anything. Plan 0063 encoded this as tests — `RecommendationsView` asserts zero `button`/`input`/`select`/`a`/`[role=button]`/`summary` elements, and the Forecast view admits exactly one control, the "Why" `details`/`summary` disclosure. This inertness is how the advisory boundary (ADR-0029) and the no-execution posture (ADR-0025) are made visible in the UI: there is nothing to click because the app takes no action.

Plan 0065 adds hover-explanations for every term on those panels. To be accessible — usable by keyboard and announced by a screen reader, not a mouse-only OS `title` tooltip — each explained term needs a **focusable, ARIA-addressable trigger**. That is, by the letter of the 0063 specs, a new interactive element on panels that asserted none. The forcing tension: accessibility (WAI-ARIA tooltip pattern needs focus and `aria-describedby`) versus the no-interactive-element invariant that encodes the no-action posture. A native `title` attribute would sidestep the posture change but is keyboard-inaccessible, touch-inaccessible, unstyled, and can't carry the dual-hat two-line card — a worse answer for the exact readers the feature serves.

## Decision

Informational glossary tooltips are a **sanctioned, scoped exception** to the panels' no-interactive-element posture. A `<GlossaryTerm>` trigger may be focusable and screen-reader-addressable, because it is **informational only**: it discloses text, performs no action, mutates no state, and opens no order/trade path. The no-*action* guarantee — the actual content of ADR-0025/0029 — is unchanged and stays asserted: Plan 0065 re-scopes the 0063 specs rather than deleting them, so the panels still prove they contain zero action controls; the specs now permit exactly the glossary triggers and nothing else. The distinction the codebase now draws is **interactive-for-disclosure** (allowed, bounded to glossary tooltips) versus **interactive-for-action** (still forbidden). The glossary content itself is delivered as a build-time renderer asset (a shared `glossary.json`), never on the SSE/MCP wire, preserving ADR-0046.

## Consequences

**Positive.** The explanations are accessible to keyboard and assistive-technology users, not just mouse users, and can carry the dual-hat card 0063 established. The no-interactive-element specs become *sharper*, not weaker: they now distinguish disclosure affordances from action affordances, which is the distinction that actually matters for the advisory boundary. The delivery choice keeps the wire small and the content in one file.

**Negative — the price.** The blanket "zero interactive elements" invariant is gone; its replacement ("zero *action* elements; disclosure triggers only via the sanctioned glossary component") is a finer line that a future contributor could erode — someone could add a focusable control and wave at this ADR. The mitigation is that the exception is bounded to one named component and the specs enumerate what is allowed; anything else focusable on those panels still fails. There is also a standing accessibility obligation: every glossary trigger must implement the full pattern (focus, `aria-describedby`, Escape-dismiss), or it degrades the very accessibility this exception exists to provide.

## Alternatives considered

- **Native `title` attribute (no posture change).** Keeps the strict no-interactive-element invariant untouched, but is keyboard- and touch-inaccessible, renders an unstyled OS tooltip, and cannot present the two-line dual-hat card — it fails the readers the feature is for.
- **Keep the panels fully inert; explanations live elsewhere (a separate help page/tab).** Preserves the posture but divorces the explanation from the term at the point of confusion — the reader has to leave the panel to learn what "conviction" means, which defeats on-hover-at-the-number.
- **Sidecar-served glossary with the tooltips reading it over the wire.** Rejected in Plan 0065's delivery decision for a different reason (new wire surface + presentation text in the sidecar); it does not change this interaction-posture question, which applies regardless of where the content comes from.

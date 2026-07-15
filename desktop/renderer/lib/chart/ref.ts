/**
 * MutRef — a minimal mutable ref container, structurally compatible with React's
 * `RefObject<T | null>` so the plain-TS chart controller (Plan 0098 / ADR-0092) can
 * hand its imperative series/primitive handles to the still-React hooks unchanged
 * during the strangler refactor. Deliberately dependency-free — the controller and
 * its sub-units import no React.
 */
export interface MutRef<T> {
  current: T | null
}

/**
 * Holder — like MutRef but the value is always present (e.g. a reconciler's Map,
 * created once and only ever cleared, never nulled). Assignable to React's
 * `RefObject<T | null>` (Map ⊆ Map | null), so it can back the controller's exposed
 * ref handles while staying non-null for the controller's own internal reads.
 */
export interface Holder<T> {
  current: T
}

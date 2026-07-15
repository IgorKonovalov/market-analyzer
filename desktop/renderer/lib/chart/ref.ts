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

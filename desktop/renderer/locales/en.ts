/**
 * English catalog (Plan 0069, ADR-0063).
 *
 * `en` is both the default locale and the test-suite locale, so each value here
 * is authored to equal the literal the corresponding renderer spec greps for —
 * a catalog typo surfaces as a failing existing spec rather than silent drift.
 *
 * Phase 1 seeds only the strings the i18n foundation itself introduces (the
 * Settings *Language* control). Phase 2 grows this to cover all renderer chrome;
 * phases 5–6 add the sidecar reason-codes, enum labels, and fixed-error entries.
 * Keep keys dotted and namespaced by surface (e.g. `settings.appearance.*`).
 */
import type { Catalog } from '../lib/i18n'

export const en = {
  'settings.appearance.language.label': 'Language',
} satisfies Catalog

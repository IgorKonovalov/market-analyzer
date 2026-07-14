/**
 * The shared glossary: dual-hat term definitions (Plan 0065 phase 1, ADR-0060).
 *
 * One record per term, each carrying BOTH hats the app already serves (the 0063
 * framing): `howComputed` — what it is / how it is computed (developer) — and
 * `whatItMeans` — what it means for your decision (trader). Content lives in the
 * sibling `glossary.json`, imported at build time and rendered by
 * `<GlossaryTerm>`; it never rides the SSE/MCP wire (ADR-0046).
 *
 * Accuracy is enforced by tests, not by this loader: `glossary.test.ts` pins the
 * record shape and the indicator set from the TS side, and
 * `tests/glossary/test_glossary_accuracy.py` (phase 3) ties each `formulaAnchor`
 * and every `indicator` key to the computing Python code cross-language.
 */
import type { Locale } from '../lib/i18n'
import glossaryJson from './glossary.json'

export type GlossaryCategory =
  | 'forecast'
  | 'recommendation'
  | 'condition'
  | 'indicator'
  | 'overlay'
  | 'candlestick'
  | 'defi'
  | 'divergence'
  | 'chart_pattern'

/**
 * A locale-keyed prose field (Plan 0069 phase 3). `en` is always present (the
 * default + test locale); `ru` is authored in phase 6, with per-field fallback
 * to `en` when a translation is absent. Only the three *prose* fields are
 * localized — the structural keys (`category`, `formulaAnchor`) stay flat so the
 * cross-language formula-anchor pin (Plan 0065) keeps binding to the same shape.
 */
export interface LocalizedString {
  en: string
  ru?: string
}

export interface GlossaryRecord {
  /** Display name shown as the tooltip card's heading (e.g. "Conviction"). */
  term: LocalizedString
  category: GlossaryCategory
  /** The developer hat: what it is / how it is computed. */
  howComputed: LocalizedString
  /** The trader hat: what it means for the decision. */
  whatItMeans: LocalizedString
  /** Present only on formula-bearing terms; the Python accuracy test pins it to
   * a canonical constant in the owning module (conviction, edge_strength). */
  formulaAnchor?: string
}

/** Resolve a locale-keyed prose field, falling back to `en` per-field when the
 * active locale has no translation — never to the key, so a missing `ru` shows
 * the English prose rather than a blank or a raw identifier. */
export function localize(value: LocalizedString, locale: Locale): string {
  return value[locale] ?? value.en
}

// `resolveJsonModule` widens JSON string properties to `string`, so the imported
// value does not statically satisfy the narrowed `category` union — the cast
// bridges that. Shape correctness is guaranteed by the two accuracy tests above,
// not by this line.
const GLOSSARY = glossaryJson as unknown as Record<string, GlossaryRecord>

/** The dual-hat record for `key`, or `undefined` when the key is not in the
 * glossary — the caller (a `<GlossaryTerm>`) renders plain text in that case, so
 * a stale or misspelled key degrades gracefully rather than crashing. */
export function term(key: string): GlossaryRecord | undefined {
  return GLOSSARY[key]
}

/** Every key present in the glossary — used by the completeness/accuracy tests. */
export function glossaryKeys(): string[] {
  return Object.keys(GLOSSARY)
}

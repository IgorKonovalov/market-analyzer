/**
 * Plan 0069 phase 6: en/ru catalog parity.
 *
 * The `ru` catalog is typed `satisfies Record<keyof typeof en, string>`, so a
 * missing or extra key already fails the typecheck. This test is the runtime
 * backstop the plan asks for — it keeps the guarantee even if the type is ever
 * loosened, and it adds a check the type cannot express: that every `{param}`
 * placeholder in an `en` template survives into its `ru` translation (a dropped
 * `{symbol}` or plural selector would silently break interpolation at runtime).
 */
import { en } from './en'
import { ru } from './ru'

/** Index of the `}` matching the `{` at `open`, accounting for nesting —
 * mirrors `formatMessage`'s `matchBrace`. */
function matchBrace(s: string, open: number): number {
  let depth = 0
  for (let i = open; i < s.length; i++) {
    if (s[i] === '{') depth++
    else if (s[i] === '}' && --depth === 0) return i
  }
  return s.length
}

const PLURAL_RE = /^(\w+)\s*,\s*plural\s*,\s*([\s\S]*)$/

/** The param names a template *interpolates*, parsed the way `formatMessage`
 * renders it — so ICU plural-*arm* text (`one {Target}`) counts as literal
 * output, not a placeholder. A bare regex can't tell `{symbol}` (a real ref)
 * from `{Target}` (arm literal); the brace walk can, because it treats the arm
 * braces as delimiters and only recurses into the arm *body*. Records simple
 * `{name}` refs and the `{name, plural, …}` selector variable, recursing into
 * arm bodies to catch nested `{name}` refs while ignoring the arm literals. */
function placeholderNames(template: string): Set<string> {
  const names = new Set<string>()
  const visit = (s: string): void => {
    let i = 0
    while (i < s.length) {
      if (s[i] !== '{') {
        i++
        continue
      }
      const close = matchBrace(s, i)
      const inner = s.slice(i + 1, close)
      const plural = PLURAL_RE.exec(inner)
      if (plural) {
        names.add(plural[1]) // the selector variable, e.g. `count`
        for (const body of armBodies(plural[2])) visit(body)
      } else {
        names.add(inner.trim()) // a simple `{name}` interpolation
      }
      i = close + 1
    }
  }
  visit(template)
  return names
}

/** Bodies of `selector {body}` arms in a plural block; the selectors (`one`,
 * `=0`) and any text between arms are literal, so only the braced bodies are
 * returned for recursion. */
function armBodies(arms: string): string[] {
  const bodies: string[] = []
  let i = 0
  while (i < arms.length) {
    const open = arms.indexOf('{', i)
    if (open === -1) break
    const close = matchBrace(arms, open)
    bodies.push(arms.slice(open + 1, close))
    i = close + 1
  }
  return bodies
}

describe('en/ru catalog parity', () => {
  const enKeys = Object.keys(en).sort()
  const ruKeys = Object.keys(ru).sort()

  it('has identical key sets in both directions', () => {
    expect(ruKeys).toEqual(enKeys)
  })

  it('preserves every en placeholder in the matching ru template', () => {
    const mismatches: string[] = []
    for (const key of enKeys) {
      const enNames = placeholderNames(en[key as keyof typeof en])
      const ruNames = placeholderNames(ru[key as keyof typeof ru])
      // Same set of top-level placeholders, both directions.
      const missing = [...enNames].filter((n) => !ruNames.has(n))
      const extra = [...ruNames].filter((n) => !enNames.has(n))
      if (missing.length > 0 || extra.length > 0) {
        mismatches.push(`${key}: missing [${missing.join(', ')}], extra [${extra.join(', ')}]`)
      }
    }
    expect(mismatches).toEqual([])
  })
})

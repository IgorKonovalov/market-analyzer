/**
 * Custom ESLint rule (Plan 0069 phase 2): fail on un-keyed user-facing string
 * literals in the renderer so every visible string is routed through `t()` from
 * `lib/i18n`. A local rule (not a third-party plugin) keeps zero new pinned
 * dependencies under the ADR-0012 cooldown / ADR-0013 exact-pinning discipline.
 *
 * It flags the categories Plan 0069 phase 2 enumerates:
 *   - JSX text nodes (`<button>Chart</button>`),
 *   - string / template values of the text-bearing attributes
 *     (`placeholder`, `title`, `aria-label`, `label`, `alt`),
 *   - string literals rendered as JSX children through an expression container
 *     (`{cond ? 'Refreshing…' : 'Refresh'}`, `{x ?? 'no data'}`).
 *
 * A literal counts as "user-facing" when its trimmed text contains a Latin or
 * Cyrillic letter — pure punctuation / digits / symbols (`·`, `—`, `→`, `%`,
 * `▲`) are ignored. `t(...)` calls are skipped (their key argument is not
 * user-facing prose). An `allow` option carries the small set of deliberately
 * un-keyed literals (endonyms, the brand name, unit abbreviations).
 */

const LETTER = /[A-Za-zЀ-ӿ]/
const TEXT_ATTRS = new Set(['placeholder', 'title', 'aria-label', 'label', 'alt'])

function attrName(node) {
  return node.name && typeof node.name.name === 'string' ? node.name.name : ''
}

function clip(text) {
  return text.length > 48 ? `${text.slice(0, 48)}…` : text
}

export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow un-keyed user-facing string literals in the renderer; route them through t() from lib/i18n.',
    },
    schema: [
      {
        type: 'object',
        properties: { allow: { type: 'array', items: { type: 'string' } } },
        additionalProperties: false,
      },
    ],
    messages: {
      unkeyed:
        'User-facing literal "{{text}}" must be routed through t() from lib/i18n, not hardcoded.',
    },
  },
  create(context) {
    const allow = new Set(context.options[0]?.allow ?? [])

    function flagIfUserFacing(node, raw) {
      const text = raw.trim()
      if (text === '' || !LETTER.test(text) || allow.has(text)) return
      context.report({ node, messageId: 'unkeyed', data: { text: clip(text) } })
    }

    // A `t(...)` call carries a catalog key, not prose — never flag inside one.
    function insideTCall(node) {
      for (let p = node.parent; p; p = p.parent) {
        if (
          p.type === 'CallExpression' &&
          p.callee.type === 'Identifier' &&
          p.callee.name === 't'
        ) {
          return true
        }
      }
      return false
    }

    // Report string / template literals reachable as a rendered value: recurse
    // through the conditional / logical shapes that produce JSX child text, but
    // stop at calls (t() and formatters alike) and identifiers.
    function scanChildExpression(node) {
      if (node == null) return
      switch (node.type) {
        case 'Literal':
          if (typeof node.value === 'string') flagIfUserFacing(node, node.value)
          return
        case 'TemplateLiteral':
          flagIfUserFacing(node, node.quasis.map((q) => q.value.cooked ?? '').join(' '))
          return
        case 'ConditionalExpression':
          scanChildExpression(node.consequent)
          scanChildExpression(node.alternate)
          return
        case 'LogicalExpression':
          scanChildExpression(node.left)
          scanChildExpression(node.right)
          return
        default:
          return
      }
    }

    return {
      JSXText(node) {
        flagIfUserFacing(node, node.value)
      },
      JSXAttribute(node) {
        if (!TEXT_ATTRS.has(attrName(node)) || node.value == null) return
        const value = node.value
        if (value.type === 'Literal' && typeof value.value === 'string') {
          flagIfUserFacing(value, value.value)
        } else if (value.type === 'JSXExpressionContainer' && !insideTCall(value.expression)) {
          scanChildExpression(value.expression)
        }
      },
      JSXExpressionContainer(node) {
        const parentType = node.parent?.type
        if (parentType !== 'JSXElement' && parentType !== 'JSXFragment') return
        if (insideTCall(node.expression)) return
        scanChildExpression(node.expression)
      },
    }
  },
}

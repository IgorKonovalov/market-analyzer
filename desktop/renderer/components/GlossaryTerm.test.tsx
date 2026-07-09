/**
 * Plan 0065 phase 2 done-when: the accessible GlossaryTerm tooltip.
 *
 * Defends: a known term renders its child label plus a focusable, ARIA-addressable
 * trigger; on hover AND on keyboard focus it reveals a card carrying BOTH the
 * howComputed and whatItMeans lines; the card is `aria-describedby`-linked and
 * `role="tooltip"`; blur and Escape dismiss it; and an UNKNOWN key degrades to
 * plain text — no trigger, no empty tooltip, no crash (the no-orphan path). The
 * trigger is deliberately not a button/link/[role=button] — informational
 * disclosure only (ADR-0060).
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen } from '@testing-library/react'

import { GlossaryTerm } from './GlossaryTerm'
import * as glossary from '../glossary/types'
import { localize, term } from '../glossary/types'
import { setLocale } from '../lib/i18n'

function trigger(): HTMLElement {
  return screen.getByText('Conviction', { selector: '[data-glossary-term]' })
}

afterEach(() => {
  // Locale-mutating tests below persist `ma.locale`; reset so the default-en
  // tests are not order-dependent, and drop any `term` spy.
  window.localStorage.clear()
  jest.restoreAllMocks()
})

it('renders the child label with a focusable, ARIA-addressable trigger', () => {
  render(<GlossaryTerm termKey="conviction">Conviction</GlossaryTerm>)
  const el = trigger()
  expect(el).toHaveAttribute('data-glossary-term', 'conviction')
  expect(el).toHaveAttribute('tabindex', '0')
  // Disclosure, not action: never a button/link/[role=button].
  expect(el.tagName).toBe('SPAN')
  expect(el).not.toHaveAttribute('role', 'button')
  const card = screen.getByRole('tooltip', { hidden: true })
  expect(el.getAttribute('aria-describedby')).toBe(card.id)
})

it('reveals the dual-hat card on hover and hides it on mouse-leave', () => {
  const record = term('conviction')
  render(<GlossaryTerm termKey="conviction">Conviction</GlossaryTerm>)
  const card = screen.getByRole('tooltip', { hidden: true })

  expect(card).toHaveAttribute('data-visible', 'false')
  // Both hats are present in the card (announced via aria-describedby regardless
  // of the visual reveal). Default locale is en.
  expect(card).toHaveTextContent(localize(record!.howComputed, 'en'))
  expect(card).toHaveTextContent(localize(record!.whatItMeans, 'en'))

  fireEvent.mouseEnter(trigger())
  expect(card).toHaveAttribute('data-visible', 'true')
  fireEvent.mouseLeave(trigger())
  expect(card).toHaveAttribute('data-visible', 'false')
})

it('reveals the card on keyboard focus and hides it on blur', () => {
  render(<GlossaryTerm termKey="conviction">Conviction</GlossaryTerm>)
  const card = screen.getByRole('tooltip', { hidden: true })

  fireEvent.focus(trigger())
  expect(card).toHaveAttribute('data-visible', 'true')
  fireEvent.blur(trigger())
  expect(card).toHaveAttribute('data-visible', 'false')
})

it('dismisses the card on Escape while focus stays on the trigger', () => {
  render(<GlossaryTerm termKey="conviction">Conviction</GlossaryTerm>)
  const el = trigger()
  const card = screen.getByRole('tooltip', { hidden: true })

  fireEvent.focus(el)
  expect(card).toHaveAttribute('data-visible', 'true')
  fireEvent.keyDown(el, { key: 'Escape' })
  expect(card).toHaveAttribute('data-visible', 'false')
  // The trigger is still the addressable element (focus never left it).
  expect(el).toHaveAttribute('data-glossary-term', 'conviction')
})

it('degrades an unknown key to plain text — no trigger, no tooltip, no crash', () => {
  const { container } = render(<GlossaryTerm termKey="no_such_key">plain label</GlossaryTerm>)
  expect(container).toHaveTextContent('plain label')
  expect(container.querySelector('[data-glossary-term]')).toBeNull()
  expect(container.querySelector('[role="tooltip"]')).toBeNull()
})

it('renders ru prose under the ru locale, falling back to en per field (Plan 0069 phase 3)', () => {
  setLocale('ru')
  // A term whose trader hat has no ru translation exercises per-field fallback.
  jest.spyOn(glossary, 'term').mockReturnValue({
    term: { en: 'Conviction', ru: 'Уверенность' },
    category: 'recommendation',
    howComputed: { en: 'derived from the fusion', ru: 'выводится из слияния' },
    whatItMeans: { en: 'how convinced the call is' },
  })
  render(<GlossaryTerm termKey="conviction">Уверенность</GlossaryTerm>)
  const card = screen.getByRole('tooltip', { hidden: true })
  expect(card).toHaveTextContent('Уверенность') // term: ru
  expect(card).toHaveTextContent('выводится из слияния') // howComputed: ru
  expect(card).toHaveTextContent('how convinced the call is') // whatItMeans: en fallback
  expect(card).not.toHaveTextContent('derived from the fusion') // ru won, en hidden
})

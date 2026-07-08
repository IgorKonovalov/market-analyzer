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
import { term } from '../glossary/types'

function trigger(): HTMLElement {
  return screen.getByText('Conviction', { selector: '[data-glossary-term]' })
}

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
  // of the visual reveal).
  expect(card).toHaveTextContent(record!.howComputed)
  expect(card).toHaveTextContent(record!.whatItMeans)

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

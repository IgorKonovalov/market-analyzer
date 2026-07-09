/**
 * Accessible glossary tooltip (Plan 0065 phase 2, ADR-0060).
 *
 * Wraps an in-scope label and, on hover AND keyboard focus, discloses the term's
 * dual-hat card — `howComputed` (developer) over `whatItMeans` (trader). It
 * implements the WAI-ARIA tooltip pattern: a focusable, screen-reader-addressable
 * trigger whose `aria-describedby` points at the card, so the description is
 * announced on focus and the card is revealed visually on hover/focus, dismissed
 * on blur or Escape.
 *
 * It is deliberately NOT a `button`/`[role=button]`/`a` — the trigger is
 * informational-only (interactive-for-disclosure, never interactive-for-action),
 * which is exactly the scoped exception ADR-0060 grants to the otherwise-inert
 * advisory panels. A `termKey` absent from the glossary degrades to plain text —
 * no affordance, no crash — so a stale or misspelled key never breaks a view.
 *
 * The card is rendered through a portal to `document.body`: it stays in the
 * accessibility tree (so `aria-describedby` always resolves and is announced on
 * focus) while staying OUT of the wrapping view's text content, so an inline term
 * never splits a neighbouring label's text.
 */
import { useCallback, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { localize, term } from '../glossary/types'
import { useLocalePref } from '../hooks/useLocalePref'
import { t } from '../lib/i18n'
import styles from './GlossaryTerm.module.css'

interface Props {
  /** The glossary key to look up (e.g. 'conviction', 'rsi_14', 'ema'). */
  termKey: string
  /** The visible label the tooltip explains. */
  children: ReactNode
  /** Optional extra class on the trigger, so a call site can keep its own
   * label styling (e.g. a monospace `code` driver name). */
  className?: string
}

/** Card offset below the trigger, in px. */
const CARD_GAP = 6

export function GlossaryTerm({ termKey, children, className }: Props): JSX.Element {
  const record = term(termKey)
  const [locale] = useLocalePref()
  const tooltipId = useId()
  const triggerRef = useRef<HTMLSpanElement>(null)
  const [visible, setVisible] = useState(false)
  const [coords, setCoords] = useState<{ left: number; top: number }>({ left: 0, top: 0 })

  const show = useCallback(() => {
    const el = triggerRef.current
    if (el) {
      const rect = el.getBoundingClientRect()
      setCoords({ left: rect.left, top: rect.bottom + CARD_GAP })
    }
    setVisible(true)
  }, [])
  const hide = useCallback(() => setVisible(false), [])

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLSpanElement>) => {
      // Dismiss the visual card; focus stays on the trigger (it never left).
      if (event.key === 'Escape' && visible) {
        event.stopPropagation()
        setVisible(false)
      }
    },
    [visible],
  )

  // No-orphan / no-regression path: an unknown key renders exactly the child,
  // with no trigger and no empty tooltip.
  if (record === undefined) {
    return <>{children}</>
  }

  const triggerClass = className ? `${styles.trigger} ${className}` : styles.trigger

  return (
    <span className={styles.wrapper}>
      <span
        ref={triggerRef}
        className={triggerClass}
        tabIndex={0}
        data-glossary-term={termKey}
        aria-describedby={tooltipId}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onKeyDown={handleKeyDown}
      >
        {children}
      </span>
      {createPortal(
        // Always in the DOM so `aria-describedby` resolves and is announced on
        // focus; `data-visible` toggles only the VISUAL reveal for sighted users.
        <span
          role="tooltip"
          id={tooltipId}
          className={styles.card}
          data-visible={visible}
          data-testid={`glossary-card:${termKey}`}
          style={{ left: coords.left, top: coords.top }}
        >
          <span className={styles.cardTerm}>{localize(record.term, locale)}</span>
          <span className={styles.cardHow}>
            <span className={styles.cardHat}>{t('glossary.howComputedLabel')}</span>
            {localize(record.howComputed, locale)}
          </span>
          <span className={styles.cardMeaning}>
            <span className={styles.cardHat}>{t('glossary.whatItMeansLabel')}</span>
            {localize(record.whatItMeans, locale)}
          </span>
        </span>,
        document.body,
      )}
    </span>
  )
}

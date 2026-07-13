/**
 * Collapsed navigation menu (Plan 0096 phase 5).
 *
 * The top bar keeps only Chart; the other destinations fold into this one
 * grouped dropdown so the symbol/timeframe controls aren't crowded. Purely
 * presentational: the caller passes resolved, grouped items (label + current
 * flag) and an `onNavigate` that flips the app's `view` through the existing
 * state machine — no routing, no new view logic here. The panel stays mounted
 * (so every destination is reachable/queryable) and is `hidden` when closed;
 * opening reveals the groups. Closes on selection, outside click, or Escape.
 */
import { useEffect, useId, useRef, useState } from 'react'

import styles from './NavMenu.module.css'

export interface NavMenuItem {
  view: string
  label: string
  testid: string
  current: boolean
}

export interface NavMenuGroup {
  label: string
  items: NavMenuItem[]
}

interface Props {
  groups: NavMenuGroup[]
  onNavigate: (view: string) => void
  triggerLabel: string
  triggerAria: string
}

export function NavMenu({ groups, onNavigate, triggerLabel, triggerAria }: Props): JSX.Element {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent): void => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Any menu destination being the active view highlights the trigger, so the
  // user sees "you're somewhere in the menu" while it's collapsed.
  const anyCurrent = groups.some((group) => group.items.some((item) => item.current))

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={styles.trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-current={anyCurrent ? 'page' : undefined}
        onClick={() => setOpen((v) => !v)}
        data-testid="nav-menu-trigger"
      >
        {triggerLabel}
      </button>
      <div
        id={menuId}
        className={styles.panel}
        role="menu"
        aria-label={triggerAria}
        hidden={!open}
        data-testid="nav-menu-panel"
      >
        {groups.map((group) => (
          <div key={group.label} className={styles.group} role="group" aria-label={group.label}>
            <p className={styles.groupLabel}>{group.label}</p>
            {group.items.map((item) => (
              <button
                key={item.view}
                type="button"
                role="menuitem"
                className={styles.item}
                aria-current={item.current ? 'page' : undefined}
                onClick={() => {
                  onNavigate(item.view)
                  setOpen(false)
                }}
                data-testid={item.testid}
              >
                {item.label}
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

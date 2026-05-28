/**
 * Symbol picker: a debounced autocomplete over `GET /search` (Plan 0024
 * phase 3) plus the timeframe select. Fully controlled — props own the
 * committed symbol; typing drives a search dropdown of `symbol · name ·
 * exchange · type`, and picking a row (click, or ArrowUp/Down + Enter)
 * commits it via `onSymbolChange`. Because suggestions come from the same
 * provider as OHLCV, every pick is directly chartable (ADR-0026).
 *
 * Free-typed Enter / blur still commits the raw (upper-cased) draft as a
 * fallback when no row is highlighted, preserving the pre-autocomplete UX.
 */
import { useEffect, useId, useRef, useState } from 'react'

import { useSymbolSearch } from '../hooks/useSymbolSearch'
import type { SymbolInfo } from '../types/sidecar/symbol-info'
import styles from './SymbolPicker.module.css'

export const TIMEFRAMES = ['1d', '1h', '5m', '1m'] as const
export type Timeframe = (typeof TIMEFRAMES)[number]

interface Props {
  symbol: string
  timeframe: Timeframe
  onSymbolChange: (symbol: string) => void
  onTimeframeChange: (timeframe: Timeframe) => void
  disabled?: boolean
}

export function SymbolPicker({
  symbol,
  timeframe,
  onSymbolChange,
  onTimeframeChange,
  disabled = false,
}: Props): JSX.Element {
  const [draft, setDraft] = useState(symbol)
  const [isOpen, setIsOpen] = useState(false)
  // -1 means "nothing highlighted" — Enter then falls through to free-text commit.
  const [highlighted, setHighlighted] = useState(-1)

  const rootRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()

  const { results } = useSymbolSearch(draft)
  const showDropdown = isOpen && results.length > 0

  // Outside-click dismissal. Blur and Escape also close, but a click elsewhere
  // in the window (that doesn't blur a focused control) needs this listener.
  useEffect(() => {
    if (!isOpen) return
    const onPointerDown = (event: MouseEvent): void => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setIsOpen(false)
        setHighlighted(-1)
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [isOpen])

  const commitFreeText = (): void => {
    const next = draft.trim().toUpperCase()
    if (next.length > 0 && next !== symbol) {
      onSymbolChange(next)
    }
    setDraft(next)
  }

  const selectResult = (info: SymbolInfo): void => {
    setDraft(info.symbol)
    setIsOpen(false)
    setHighlighted(-1)
    // Symbols come straight from Yahoo's namespace — do NOT upper-case (would
    // mangle e.g. nothing today, but keeps the picked value byte-identical to
    // what get_ohlcv expects).
    if (info.symbol !== symbol) {
      onSymbolChange(info.symbol)
    }
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>): void => {
    switch (event.key) {
      case 'ArrowDown':
        if (results.length === 0) return
        event.preventDefault()
        setIsOpen(true)
        setHighlighted((i) => Math.min(i + 1, results.length - 1))
        break
      case 'ArrowUp':
        if (results.length === 0) return
        event.preventDefault()
        setIsOpen(true)
        setHighlighted((i) => Math.max(i - 1, 0))
        break
      case 'Enter':
        if (showDropdown && highlighted >= 0 && results[highlighted]) {
          // Selecting a row — suppress the form submit so we don't also
          // commit the (stale) free-text draft.
          event.preventDefault()
          selectResult(results[highlighted])
        }
        // else: let the form's onSubmit handle the free-text commit.
        break
      case 'Escape':
        setIsOpen(false)
        setHighlighted(-1)
        break
      default:
        break
    }
  }

  return (
    <div className={styles.root} ref={rootRef}>
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault()
          setIsOpen(false)
          setHighlighted(-1)
          commitFreeText()
        }}
      >
        <label className={styles.field}>
          <span className={styles.labelText}>Symbol</span>
          <input
            className={styles.input}
            type="text"
            role="combobox"
            aria-expanded={showDropdown}
            aria-controls={listboxId}
            aria-autocomplete="list"
            aria-activedescendant={
              showDropdown && highlighted >= 0 ? `${listboxId}-opt-${highlighted}` : undefined
            }
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value)
              setIsOpen(true)
              setHighlighted(-1)
            }}
            onFocus={() => {
              if (draft.trim().length > 0) setIsOpen(true)
            }}
            onBlur={() => {
              setIsOpen(false)
              setHighlighted(-1)
              commitFreeText()
            }}
            onKeyDown={onKeyDown}
            disabled={disabled}
            aria-label="Symbol"
            autoComplete="off"
            spellCheck={false}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.labelText}>Timeframe</span>
          <select
            className={styles.select}
            value={timeframe}
            onChange={(event) => onTimeframeChange(event.target.value as Timeframe)}
            disabled={disabled}
            aria-label="Timeframe"
          >
            {TIMEFRAMES.map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
        </label>
      </form>

      {showDropdown && (
        <ul
          className={styles.dropdown}
          role="listbox"
          id={listboxId}
          aria-label="Symbol suggestions"
        >
          {results.map((info, i) => (
            <li
              key={info.symbol}
              id={`${listboxId}-opt-${i}`}
              role="option"
              aria-selected={i === highlighted}
              className={`${styles.option} ${i === highlighted ? styles.optionActive : ''}`}
              // mousedown fires before the input's blur; preventing default keeps
              // focus on the input so the blur-commit doesn't pre-empt the click.
              onMouseDown={(event) => {
                event.preventDefault()
                selectResult(info)
              }}
              onMouseEnter={() => setHighlighted(i)}
            >
              <span className={styles.optionSymbol}>{info.symbol}</span>
              <span className={styles.optionName}>{info.name}</span>
              <span className={styles.optionTags}>
                {info.exchange ? <span>{info.exchange}</span> : null}
                {info.quote_type ? <span>{info.quote_type}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

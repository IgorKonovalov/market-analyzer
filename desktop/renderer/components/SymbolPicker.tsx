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

import { t } from '../lib/i18n'
import { useSymbolSearch } from '../hooks/useSymbolSearch'
import { TIMEFRAMES, type Timeframe } from '../lib/timeframes'
import type { SymbolInfo } from '../types/sidecar/symbol-info'
import styles from './SymbolPicker.module.css'

interface Props {
  symbol: string
  timeframe: Timeframe
  onSymbolChange: (symbol: string) => void
  onTimeframeChange: (timeframe: Timeframe) => void
  disabled?: boolean
}

// A Coinbase USD-native pair (deep, USD-quoted history — Plan 0081 / ADR-0076).
// `exchange` is the routed source (relabeled server-side in search_symbols), so
// this is the truthful signal that the suggestion charts deep USD from Coinbase
// rather than a shallow Yahoo composite; the picker flags it as the preferred
// crypto suggestion with a hint label (never a rewrite of what the user typed).
function isDeepUsd(info: SymbolInfo): boolean {
  return info.exchange === 'Coinbase' && info.symbol.endsWith('-USD')
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
  const tfLabelId = useId()
  const tfGroupRef = useRef<HTMLDivElement>(null)

  const { results } = useSymbolSearch(draft)
  const showDropdown = isOpen && results.length > 0

  // Follow an EXTERNAL symbol change: an agent `chart.show` updates App's
  // committed symbol (the `symbol` prop), and the input must reflect it, not
  // just the chart. Safe against clobbering in-progress typing — user input
  // only calls `setDraft`, never `onSymbolChange`, so the `symbol` prop is
  // unchanged mid-type and this effect doesn't fire; on the user's own commit
  // it re-sets `draft` to a value it already holds (a no-op).
  useEffect(() => {
    setDraft(symbol)
  }, [symbol])

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

  // Roving-tabindex keyboard nav for the segmented timeframe group: only the
  // active segment is in the tab order; arrows/Home/End move between segments
  // and commit the landed-on one (select-on-navigate, like a radio group).
  const onTimeframeKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const current = TIMEFRAMES.indexOf(timeframe)
    const last = TIMEFRAMES.length - 1
    let next: number
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = current >= last ? 0 : current + 1
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        next = current <= 0 ? last : current - 1
        break
      case 'Home':
        next = 0
        break
      case 'End':
        next = last
        break
      default:
        return
    }
    event.preventDefault()
    const value = TIMEFRAMES[next]
    if (value !== timeframe) onTimeframeChange(value)
    // Move focus with selection; the button order is stable across re-render so
    // indexing the live DOM is safe.
    tfGroupRef.current?.querySelectorAll<HTMLButtonElement>(`.${styles.segment}`)?.[next]?.focus()
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
          <span className={styles.labelText}>{t('symbolPicker.symbol')}</span>
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
            aria-label={t('symbolPicker.symbol')}
            autoComplete="off"
            spellCheck={false}
          />
        </label>

        <div className={styles.field}>
          <span className={styles.labelText} id={tfLabelId}>
            {t('symbolPicker.timeframe')}
          </span>
          <div
            className={styles.segmented}
            role="group"
            aria-labelledby={tfLabelId}
            ref={tfGroupRef}
            onKeyDown={disabled ? undefined : onTimeframeKeyDown}
          >
            {TIMEFRAMES.map((tf) => {
              const active = tf === timeframe
              return (
                <button
                  key={tf}
                  type="button"
                  className={`${styles.segment} ${active ? styles.segmentActive : ''}`}
                  aria-pressed={active}
                  aria-current={active ? 'true' : undefined}
                  tabIndex={active ? 0 : -1}
                  disabled={disabled}
                  onClick={() => {
                    if (tf !== timeframe) onTimeframeChange(tf)
                  }}
                >
                  {tf}
                </button>
              )
            })}
          </div>
        </div>
      </form>

      {showDropdown && (
        <ul
          className={styles.dropdown}
          role="listbox"
          id={listboxId}
          aria-label={t('symbolPicker.symbolSuggestions')}
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
              <span className={styles.optionMeta}>
                {isDeepUsd(info) ? (
                  <span className={styles.deepHint}>{t('symbolPicker.deepUsdHint')}</span>
                ) : null}
                {info.exchange ? <span className={styles.sourceBadge}>{info.exchange}</span> : null}
                {info.quote_type ? (
                  <span className={styles.optionType}>{info.quote_type}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

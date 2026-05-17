/**
 * Controlled symbol input + timeframe select. Fully controlled — props own
 * the value; the parent commits to symbol on form submit (Enter or button)
 * so a fetch isn't triggered on every keystroke.
 */
import { useState } from "react";

import styles from "./SymbolPicker.module.css";

export const TIMEFRAMES = ["1d", "1h", "5m", "1m"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

interface Props {
  symbol: string;
  timeframe: Timeframe;
  onSymbolChange: (symbol: string) => void;
  onTimeframeChange: (timeframe: Timeframe) => void;
  disabled?: boolean;
}

export function SymbolPicker({
  symbol,
  timeframe,
  onSymbolChange,
  onTimeframeChange,
  disabled = false,
}: Props): JSX.Element {
  const [draft, setDraft] = useState(symbol);

  const commit = (): void => {
    const next = draft.trim().toUpperCase();
    if (next.length > 0 && next !== symbol) {
      onSymbolChange(next);
    }
    setDraft(next);
  };

  return (
    <form
      className={styles.root}
      onSubmit={(event) => {
        event.preventDefault();
        commit();
      }}
    >
      <label className={styles.field}>
        <span className={styles.labelText}>Symbol</span>
        <input
          className={styles.input}
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
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
  );
}

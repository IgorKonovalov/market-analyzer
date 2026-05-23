// Template: desktop/renderer/components/<ComponentName>.tsx
//
// Use this for a presentational or lightly-stateful component. For chart components
// (anything wrapping a non-React library that owns DOM / WebGL / observers), use
// chart-component-template.tsx instead — the disposal pattern is different.
//
// Co-locate <ComponentName>.module.css next to this file.
// Co-locate <ComponentName>.test.tsx if there's logic worth testing
// (presentational components don't need snapshot tests; ADR-0008 §Renderer testing).

import { useState } from 'react'
import styles from './ComponentName.module.css'

interface Props {
  // Always typed via an interface — easier to refactor and to export.
  // Required vs optional matters; mark optional explicitly with `?`.
  label: string
  initialValue?: number
  onChange?: (value: number) => void
}

export function ComponentName({ label, initialValue = 0, onChange }: Props) {
  const [value, setValue] = useState(initialValue)

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const next = Number(event.target.value)
    setValue(next)
    onChange?.(next)
  }

  return (
    <div className={styles.root}>
      <label className={styles.label}>
        {label}
        <input
          className={styles.input}
          type="number"
          value={value}
          onChange={handleChange}
        />
      </label>
    </div>
  )
}

// Notes:
// - Named export, not default. Better grep, better refactoring.
// - Controlled input — `value` + `onChange`, never `defaultValue` + `useRef`.
// - Accessibility: the label wraps the input so clicks on the label focus the input.
//   If your design splits them, use `htmlFor` + `id`.
// - No `useCallback` on `handleChange` — there's no `React.memo` child and no
//   measured re-render problem. Add it when there's a reason, not prophylactically.

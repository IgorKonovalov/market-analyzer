/**
 * Add-indicator form (Plan 0082 phase 4, ADR-0077). A compact form in the layers
 * panel: an indicator dropdown, a period input (for the period kinds), and a
 * std-dev `k` input (Bollinger only). "Add" writes a user overlay to the phase-3
 * store via `onAdd`. Purely presentational + renderer-owned — no sidecar call, no
 * wire. Accessible: labelled controls, a real submit button, and an `alert`-role
 * validation message tied to the invalid input.
 */
import { useState } from 'react'
import type { FormEvent } from 'react'

import {
  OVERLAY_FORM_KINDS,
  buildOverlayFromForm,
  defaultPeriodFor,
  formKindTakesPeriod,
  formKindTakesStdDev,
} from '../lib/overlayForm'
import { t } from '../lib/i18n'
import type { OverlayKind, OverlaySpec } from '../types/events'
import styles from './LayersPanel.module.css'

interface Props {
  onAdd: (spec: OverlaySpec) => void
}

export function AddOverlayForm({ onAdd }: Props): JSX.Element {
  const [kind, setKind] = useState<OverlayKind>('ema')
  const [period, setPeriod] = useState<string>(String(defaultPeriodFor('ema')))
  const [stdDev, setStdDev] = useState<string>('2')
  const [error, setError] = useState<string | null>(null)

  const takesPeriod = formKindTakesPeriod(kind)
  const takesStdDev = formKindTakesStdDev(kind)

  const onKindChange = (next: OverlayKind): void => {
    setKind(next)
    setPeriod(String(defaultPeriodFor(next)))
    setError(null)
  }

  const submit = (e: FormEvent): void => {
    e.preventDefault()
    const result = buildOverlayFromForm(kind, Number(period), Number(stdDev))
    if (!result.ok) {
      setError(result.error === 'period' ? t('layers.invalidPeriod') : t('layers.invalidStdDev'))
      return
    }
    setError(null)
    onAdd(result.spec)
  }

  return (
    <form className={styles.form} onSubmit={submit} noValidate data-testid="add-overlay-form">
      <div className={styles.formField}>
        <label htmlFor="add-overlay-kind">{t('layers.kindLabel')}</label>
        <select
          id="add-overlay-kind"
          data-testid="add-overlay-kind"
          value={kind}
          onChange={(e) => onKindChange(e.target.value as OverlayKind)}
        >
          {OVERLAY_FORM_KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`layers.kind.${k}`)}
            </option>
          ))}
        </select>
      </div>

      {takesPeriod && (
        <div className={styles.formField}>
          <label htmlFor="add-overlay-period">{t('layers.periodLabel')}</label>
          <input
            id="add-overlay-period"
            data-testid="add-overlay-period"
            type="number"
            min={1}
            step={1}
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            aria-invalid={error !== null && !takesStdDev ? true : undefined}
          />
        </div>
      )}

      {takesStdDev && (
        <div className={styles.formField}>
          <label htmlFor="add-overlay-stddev">{t('layers.stdDevLabel')}</label>
          <input
            id="add-overlay-stddev"
            data-testid="add-overlay-stddev"
            type="number"
            min={0}
            step="any"
            value={stdDev}
            onChange={(e) => setStdDev(e.target.value)}
          />
        </div>
      )}

      {error !== null && (
        <p className={styles.formError} role="alert" data-testid="add-overlay-error">
          {error}
        </p>
      )}

      <button type="submit" className={styles.addSubmit} data-testid="add-overlay-submit">
        {t('layers.addButton')}
      </button>
    </form>
  )
}

/**
 * App-level toast host for `alert.triggered v1` (Plan 0060 phase 4).
 *
 * Subscribes to the renderer-internal `alertBus` (fed by App's single
 * `useEventStream`), so the toast appears whichever view is active —
 * most-recent-wins: a new alert replaces the one on screen. Dismiss is
 * manual (an alert is a deliberate, user-configured signal, not ambient
 * noise to auto-fade). The message is the payload's condition FACT —
 * never advice (ADR-0029).
 */
import { useEffect, useState } from 'react'

import { subscribeAlerts } from '../handlers/alertBus'
import type { AlertTriggeredPayloadV1 } from '../types/events'
import styles from './AlertToaster.module.css'
import { Toast } from './Toast'

export function alertToastMessage(alert: AlertTriggeredPayloadV1): string {
  return `Alert: ${alert.symbol} ${alert.timeframe} — ${alert.condition}`
}

export function AlertToaster(): JSX.Element | null {
  const [current, setCurrent] = useState<AlertTriggeredPayloadV1 | null>(null)

  useEffect(() => subscribeAlerts(setCurrent), [])

  if (current === null) return null

  return (
    <div className={styles.host} data-testid="alert-toaster">
      <Toast message={alertToastMessage(current)} onDismiss={() => setCurrent(null)} />
    </div>
  )
}

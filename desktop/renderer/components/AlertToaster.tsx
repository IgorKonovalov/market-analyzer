/**
 * App-level toast host for `alert.triggered v1` (Plan 0060 phase 4) and
 * `defi.position_alert v1` (Plan 0099 phase 4).
 *
 * Subscribes to the renderer-internal `alertBus` + `defiPositionAlertBus`
 * (both fed by App's single `useEventStream`), so the toast appears whichever
 * view is active — most-recent-wins across both sources: a new alert replaces
 * the one on screen. Dismiss is manual (an alert is a deliberate,
 * user-configured signal, not ambient noise to auto-fade). The message is the
 * payload's condition FACT — never advice (ADR-0029).
 */
import { useEffect, useState } from 'react'

import { subscribeAlerts } from '../handlers/alertBus'
import { subscribeDefiPositionAlerts } from '../handlers/defiPositionAlertBus'
import { defiAlertMessage } from '../lib/defiPositionAlert'
import type { AlertTriggeredPayloadV1 } from '../types/events'
import styles from './AlertToaster.module.css'
import { Toast } from './Toast'

export function alertToastMessage(alert: AlertTriggeredPayloadV1): string {
  return `Alert: ${alert.symbol} ${alert.timeframe} — ${alert.condition}`
}

export function AlertToaster(): JSX.Element | null {
  const [current, setCurrent] = useState<string | null>(null)

  useEffect(() => subscribeAlerts((payload) => setCurrent(alertToastMessage(payload))), [])
  useEffect(
    () => subscribeDefiPositionAlerts((payload) => setCurrent(defiAlertMessage(payload))),
    [],
  )

  if (current === null) return null

  return (
    <div className={styles.host} data-testid="alert-toaster">
      <Toast message={current} onDismiss={() => setCurrent(null)} />
    </div>
  )
}

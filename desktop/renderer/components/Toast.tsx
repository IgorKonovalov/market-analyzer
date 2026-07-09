/**
 * Minimal dismissible toast (Plan 0013 phase 4).
 *
 * Plan 0007 phase 4 deferred the toast component with "ui-builder adds a minimal
 * one if not present" — it wasn't added (run.completed drives a view swap, not a
 * toast), so this is that minimal one. Used for backfill-failure surfacing in
 * OhlcvView; reusable for any transient, dismissible notice.
 */
import { t } from '../lib/i18n'
import styles from './Toast.module.css'

export interface ToastProps {
  message: string
  onDismiss: () => void
  tone?: 'error' | 'info'
}

export function Toast({ message, onDismiss, tone = 'info' }: ToastProps): JSX.Element {
  return (
    <div
      className={`${styles.toast} ${tone === 'error' ? styles.error : ''}`}
      role={tone === 'error' ? 'alert' : 'status'}
      data-testid="toast"
    >
      <span className={styles.message}>{message}</span>
      <button
        type="button"
        className={styles.dismiss}
        aria-label={t('toast.dismiss')}
        onClick={onDismiss}
      >
        ×
      </button>
    </div>
  )
}

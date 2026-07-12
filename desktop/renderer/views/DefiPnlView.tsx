/**
 * DeFi Wallet P&L view (Plan 0088 phase 5) — the first DeFi renderer surface.
 *
 * A read-only, renderer-originated lookup (mirroring the OHLCV/scan precedent, no
 * new ADR — it does not cross ADR-0015): paste a `0x…` EVM address, and
 * `POST /defi/pnl` reconstructs the wallet's DeFi P&L through the typed fetch
 * client. LP positions are the headline (exact 7d/30d/90d/all realized figures,
 * with a muted estimated-total-return sub-row); non-LP positions are muted in an
 * "Other" section and, thanks to the partial-total rule (ADR-0082), never
 * suppress the LP view. A partial banner + excluded count appear only when one or
 * more positions couldn't be priced.
 *
 * The MCP `compute_wallet_pnl` tool remains the primary agent surface (ADR-0015);
 * this is the human paste-and-scan twin.
 */
import { useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError, sanitizeApiErrorBody } from '../api/client'
import { isValidAddress, loadRecentWallets, rememberWallet } from '../lib/defiWallets'
import { formatUsd, formatUsdSigned } from '../lib/format'
import { t } from '../lib/i18n'
import { useWalletPnl } from '../hooks/useWalletPnl'
import {
  PNL_WINDOWS,
  type PnlWindow,
  type PositionPnl,
  type RewardAmount,
  type WalletPnlResponse,
  type WindowPnl,
} from '../types/defiPnl'
import styles from './DefiPnlView.module.css'

export function DefiPnlView(): JSX.Element {
  const [addressInput, setAddressInput] = useState('')
  const [refresh, setRefresh] = useState(false)
  const [invalid, setInvalid] = useState(false)
  const [recent, setRecent] = useState<string[]>(() => loadRecentWallets())
  const { state, analyze } = useWalletPnl()

  const run = (address: string, doRefresh: boolean): void => {
    setRecent(rememberWallet(address))
    analyze(address, doRefresh)
  }

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault()
    const address = addressInput.trim()
    // Client-side gate: an invalid address is rejected BEFORE any fetch.
    if (!isValidAddress(address)) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    run(address, refresh)
  }

  const onChip = (address: string): void => {
    setAddressInput(address)
    setInvalid(false)
    run(address, false) // recent addresses replay the cache (fast)
  }

  const ready = state.status === 'ready' ? state.result : null

  return (
    <section className={styles.root} aria-label={t('defi.title')}>
      <header className={styles.header}>
        <h2 className={styles.title}>{t('defi.title')}</h2>
        <p className={styles.lede}>{t('defi.lede')}</p>
      </header>

      <form className={styles.controls} onSubmit={onSubmit}>
        <div className={styles.field}>
          <label htmlFor="defi-address">{t('defi.addressLabel')}</label>
          <input
            id="defi-address"
            type="text"
            className={styles.address}
            value={addressInput}
            placeholder={t('defi.addressPlaceholder')}
            spellCheck={false}
            autoComplete="off"
            data-testid="defi-address"
            onChange={(e) => setAddressInput(e.target.value)}
          />
        </div>
        <label className={styles.refresh}>
          <input
            type="checkbox"
            checked={refresh}
            data-testid="defi-refresh"
            onChange={(e) => setRefresh(e.target.checked)}
          />
          {t('defi.refreshLabel')}
        </label>
        <button type="submit" data-testid="defi-analyze">
          {t('defi.analyze')}
        </button>
      </form>

      {invalid && (
        <div className={styles.invalid} role="alert" data-testid="defi-invalid">
          {t('defi.invalidAddress')}
        </div>
      )}

      {recent.length > 0 && (
        <div className={styles.recent}>
          <span className={styles.recentLabel}>{t('defi.recentLabel')}</span>
          {recent.map((address) => (
            <button
              key={address}
              type="button"
              className={styles.chip}
              title={address}
              data-testid="defi-recent-chip"
              onClick={() => onChip(address)}
            >
              {shortAddress(address)}
            </button>
          ))}
        </div>
      )}

      {state.status === 'idle' && (
        <div className={styles.statusBlock} role="status" data-testid="defi-idle">
          {t('defi.idle')}
        </div>
      )}
      {state.status === 'loading' && (
        <div className={styles.statusBlock} role="status" data-testid="defi-loading">
          {t('defi.loading')}
        </div>
      )}
      {state.status === 'error' && (
        <div className={styles.error} role="alert" data-testid="defi-error">
          {errorMessage(state.error)}
        </div>
      )}

      {ready && <Results result={ready} />}
    </section>
  )
}

interface ResultsProps {
  result: WalletPnlResponse
}

function Results({ result }: ResultsProps): JSX.Element {
  const lp = result.positions.filter((p) => p.is_lp)
  const other = result.positions.filter((p) => !p.is_lp)
  const complete = result.position_count - result.incomplete_position_count
  return (
    <div className={styles.results}>
      {result.partial && (
        <div className={styles.partialBanner} role="status" data-testid="defi-partial-banner">
          {t('defi.partialBanner', {
            excluded: result.incomplete_position_count,
            total: result.position_count,
          })}
        </div>
      )}

      {result.positions.length === 0 ? (
        <div className={styles.statusBlock} role="status" data-testid="defi-empty">
          {t('defi.empty')}
        </div>
      ) : (
        <>
          <p className={styles.totals} data-testid="defi-totals">
            {t('defi.totals', {
              realized: signedUsd(result.realized_usd),
              unrealized: signedUsd(result.unrealized_usd),
              complete,
            })}
          </p>
          {lp.length > 0 && <LpTable positions={lp} />}
          {other.length > 0 && <OtherPositions positions={other} />}
        </>
      )}
    </div>
  )
}

interface LpTableProps {
  positions: PositionPnl[]
}

function LpTable({ positions }: LpTableProps): JSX.Element {
  return (
    <table className={styles.table} aria-label={t('defi.tableLabel')}>
      <thead>
        <tr>
          <th scope="col">{t('defi.col.position')}</th>
          {PNL_WINDOWS.map((w) => (
            <th key={w} scope="col" className={styles.num}>
              {w}
            </th>
          ))}
          <th scope="col" className={styles.num}>
            {t('defi.col.unclaimed')}
          </th>
        </tr>
      </thead>
      <tbody>
        {positions.map((position) => (
          <LpRows key={position.position_id} position={position} />
        ))}
      </tbody>
    </table>
  )
}

interface LpRowsProps {
  position: PositionPnl
}

/** An LP position's exact-realized row plus a muted estimated-total-return
 * sub-row (parenthesized; an em dash where a window's estimate is null). */
function LpRows({ position }: LpRowsProps): JSX.Element {
  const byWindow = indexWindows(position.windows)
  return (
    <>
      <tr className={styles.lpRow} data-testid="defi-lp-row">
        <td className={styles.positionCell} title={position.position_id}>
          {position.position_id}
        </td>
        {PNL_WINDOWS.map((w) => (
          <td key={w} className={styles.num}>
            {realizedCell(byWindow[w])}
          </td>
        ))}
        <td className={styles.num}>{formatUnclaimed(position.unclaimed_rewards)}</td>
      </tr>
      <tr className={styles.estRow} data-testid="defi-est-row">
        <td className={styles.estLabel}>{t('defi.estReturnLabel')}</td>
        {PNL_WINDOWS.map((w) => (
          <td key={w} className={styles.num}>
            {estimateCell(byWindow[w])}
          </td>
        ))}
        <td aria-hidden="true" />
      </tr>
    </>
  )
}

interface OtherPositionsProps {
  positions: PositionPnl[]
}

/** Non-LP positions (lending, loose tokens, unpriceable exotics), muted and
 * clearly secondary. An incomplete one shows its reason; a complete one its
 * realized/unrealized. Never suppresses the LP view (ADR-0082 partial totals). */
function OtherPositions({ positions }: OtherPositionsProps): JSX.Element {
  return (
    <section className={styles.other} aria-label={t('defi.otherLabel')}>
      <h3 className={styles.otherTitle}>{t('defi.otherLabel')}</h3>
      <ul className={styles.otherList}>
        {positions.map((position) => (
          <li key={position.position_id} className={styles.otherRow} data-testid="defi-other-row">
            <span className={styles.otherId} title={position.position_id}>
              {position.position_id}
            </span>
            <span className={styles.otherReason}>{otherReason(position)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ── Cell + value helpers ─────────────────────────────────────────────────────

/** USD with an explicit sign; an em dash for a null figure (never a fake 0). */
function signedUsd(value: number | null): string {
  return value === null ? '—' : formatUsdSigned(value)
}

/** Exact realized P&L for a window (never null in the model; em dash if absent). */
function realizedCell(window: WindowPnl | undefined): string {
  return window === undefined ? '—' : formatUsdSigned(window.realized_usd)
}

/** The labeled estimate, parenthesized; an em dash when the window start couldn't
 * be priced (an honest per-window gap) or the window is absent. */
function estimateCell(window: WindowPnl | undefined): string {
  if (window === undefined || window.total_return_usd === null) return '—'
  return `(${formatUsdSigned(window.total_return_usd)})`
}

function indexWindows(windows: WindowPnl[]): Partial<Record<PnlWindow, WindowPnl>> {
  const map: Partial<Record<PnlWindow, WindowPnl>> = {}
  for (const window of windows) map[window.window] = window
  return map
}

/** Owed-but-unclaimed rewards: the summed USD value when every leg is priced,
 * else the raw token amounts (honest — no fabricated USD). Em dash when none. */
function formatUnclaimed(rewards: RewardAmount[] | null): string {
  if (rewards === null || rewards.length === 0) return '—'
  if (rewards.every((r) => r.usd_value !== null)) {
    return formatUsd(rewards.reduce((sum, r) => sum + (r.usd_value ?? 0), 0))
  }
  return rewards.map((r) => `${formatTokenAmount(r.amount)} ${r.symbol}`).join(', ')
}

function formatTokenAmount(amount: number): string {
  return amount.toLocaleString('en-US', { maximumFractionDigits: 4 })
}

function otherReason(position: PositionPnl): string {
  if (position.incomplete) {
    return position.notes.length > 0 ? position.notes.join('; ') : t('defi.incompleteGeneric')
  }
  return t('defi.otherFigures', {
    realized: signedUsd(position.realized_usd),
    unrealized: signedUsd(position.unrealized_usd),
  })
}

/** `0x1234…abcd` — a compact chip label for a full address (title carries the full). */
function shortAddress(address: string): string {
  return `${address.slice(0, 6)}…${address.slice(-4)}`
}

/** Map a fetch failure to an actionable message. The two source-config 503s point
 * the user at Settings (set the Zerion key); everything else keeps the client's
 * already-localized message. */
function errorMessage(error: Error): string {
  if (error instanceof ApiError) {
    const detail = sanitizeApiErrorBody(error.body)
    if (
      detail === 'no wallet-positions source configured' ||
      detail === 'no historical price source configured'
    ) {
      return t('defi.error.setKeyHint')
    }
    if (detail === 'agent mode is off') return t('defi.error.agentModeOff')
  }
  return error.message
}

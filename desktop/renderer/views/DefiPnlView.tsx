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
 * Every P&L term the table uses (realized vs unrealized, the rolling windows,
 * estimated total return, unclaimed rewards, partial totals) is a `<GlossaryTerm>`
 * — hover or focus discloses the dual-hat card (ADR-0060). Positions carry a
 * block-explorer link: the wallet on its chain's explorer, plus the pool contract
 * itself once the sidecar folds `pool_address` onto the response. Links open in
 * the OS browser via `shell.openExternal` (ADR-0008), never in-app.
 *
 * The MCP `compute_wallet_pnl` tool remains the primary agent surface (ADR-0015);
 * this is the human paste-and-scan twin.
 */
import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'

import { ApiError, sanitizeApiErrorBody } from '../api/client'
import { GlossaryTerm } from '../components/GlossaryTerm'
import { isValidAddress, loadRecentWallets, rememberWallet } from '../lib/defiWallets'
import {
  displayPositionId,
  explorerAddressUrl,
  explorerName,
  parsePositionId,
  type DefiChain,
} from '../lib/defiExplorer'
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
  const [analyzed, setAnalyzed] = useState('')
  const [refresh, setRefresh] = useState(false)
  const [invalid, setInvalid] = useState(false)
  const [recent, setRecent] = useState<string[]>(() => loadRecentWallets())
  const { state, analyze } = useWalletPnl()

  const run = (address: string, doRefresh: boolean): void => {
    setRecent(rememberWallet(address))
    setAnalyzed(address)
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

      {ready && <Results result={ready} wallet={analyzed} />}
    </section>
  )
}

interface ResultsProps {
  result: WalletPnlResponse
  /** The full (unmasked) address just analyzed — for the wallet explorer link. */
  wallet: string
}

function Results({ result, wallet }: ResultsProps): JSX.Element {
  const lp = result.positions.filter((p) => p.is_lp)
  const other = result.positions.filter((p) => !p.is_lp)
  const complete = result.position_count - result.incomplete_position_count
  return (
    <div className={styles.results}>
      {result.partial && (
        <div className={styles.partialBanner} role="status" data-testid="defi-partial-banner">
          <GlossaryTerm termKey="defi_partial">
            {t('defi.partialBanner', {
              excluded: result.incomplete_position_count,
              total: result.position_count,
            })}
          </GlossaryTerm>
        </div>
      )}

      {result.positions.length === 0 ? (
        <div className={styles.statusBlock} role="status" data-testid="defi-empty">
          {t('defi.empty')}
        </div>
      ) : (
        <>
          <Summary
            realized={result.realized_usd}
            unrealized={result.unrealized_usd}
            complete={complete}
            total={result.position_count}
            wallet={wallet}
            positions={result.positions}
          />
          {lp.length > 0 && <LpTable positions={lp} />}
          {other.length > 0 && <OtherPositions positions={other} />}
        </>
      )}
    </div>
  )
}

interface SummaryProps {
  realized: number | null
  unrealized: number | null
  complete: number
  total: number
  wallet: string
  positions: PositionPnl[]
}

/** The headline stat strip: realized / unrealized / complete-count, each a
 * glossary term, plus a per-chain "view wallet on explorer" link. */
function Summary({
  realized,
  unrealized,
  complete,
  total,
  wallet,
  positions,
}: SummaryProps): JSX.Element {
  return (
    <div className={styles.summary} data-testid="defi-totals">
      <dl className={styles.stats}>
        <Stat termKey="defi_realized" label={t('defi.summary.realized')}>
          <span className={signClass(realized)}>{signedUsd(realized)}</span>
        </Stat>
        <Stat termKey="defi_unrealized" label={t('defi.summary.unrealized')}>
          <span className={signClass(unrealized)}>{signedUsd(unrealized)}</span>
        </Stat>
        <Stat termKey="defi_partial" label={t('defi.summary.complete')}>
          {t('defi.summary.completeValue', { complete, total })}
        </Stat>
      </dl>
      <WalletLinks wallet={wallet} positions={positions} />
    </div>
  )
}

interface StatProps {
  termKey: string
  label: string
  children: ReactNode
}

function Stat({ termKey, label, children }: StatProps): JSX.Element {
  return (
    <div className={styles.stat}>
      <dt className={styles.statLabel}>
        <GlossaryTerm termKey={termKey}>{label}</GlossaryTerm>
      </dt>
      <dd className={styles.statValue}>{children}</dd>
    </div>
  )
}

interface WalletLinksProps {
  wallet: string
  positions: PositionPnl[]
}

/** One explorer link per distinct chain the wallet holds positions on (usually
 * one). Correct today from the address alone — the pool-contract deep links
 * light up per row once the sidecar exposes `pool_address`. */
function WalletLinks({ wallet, positions }: WalletLinksProps): JSX.Element | null {
  const chains = distinctChains(positions)
  const links = chains
    .map((chain) => ({ chain, url: explorerAddressUrl(chain, wallet) }))
    .filter((l): l is { chain: DefiChain; url: string } => l.url !== null)
  if (links.length === 0) return null
  return (
    <div className={styles.walletLinks}>
      {links.map(({ chain, url }) => {
        const name = explorerName(chain) ?? chain
        return (
          <ExternalLink
            key={chain}
            url={url}
            title={t('defi.explorerWalletTitle', { explorer: name })}
            className={styles.walletLink}
            testId="defi-wallet-link"
          >
            {t('defi.explorerLink', { explorer: name })} ↗
          </ExternalLink>
        )
      })}
    </div>
  )
}

interface LpTableProps {
  positions: PositionPnl[]
}

function LpTable({ positions }: LpTableProps): JSX.Element {
  return (
    <>
      <table className={styles.table} aria-label={t('defi.tableLabel')}>
        <thead>
          <tr>
            <th scope="col">
              <GlossaryTerm termKey="defi_position">{t('defi.col.position')}</GlossaryTerm>
            </th>
            {PNL_WINDOWS.map((w) => (
              <th key={w} scope="col" className={styles.num}>
                <GlossaryTerm termKey="defi_window">{w}</GlossaryTerm>
              </th>
            ))}
            <th scope="col" className={styles.num}>
              <GlossaryTerm termKey="defi_unclaimed">{t('defi.col.unclaimed')}</GlossaryTerm>
            </th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <LpRows key={position.position_id} position={position} />
          ))}
        </tbody>
      </table>
      <p className={styles.legend}>{t('defi.legend')}</p>
    </>
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
        <td className={styles.positionCell}>
          <PositionCell position={position} />
        </td>
        {PNL_WINDOWS.map((w) => (
          <td key={w} className={styles.num}>
            {realizedCell(byWindow[w])}
          </td>
        ))}
        <td className={styles.num}>{formatUnclaimed(position.unclaimed_rewards)}</td>
      </tr>
      <tr className={styles.estRow} data-testid="defi-est-row">
        <td className={styles.estLabel}>
          <GlossaryTerm termKey="defi_est_return">{t('defi.estReturnLabel')}</GlossaryTerm>
        </td>
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

interface PositionCellProps {
  position: PositionPnl
}

/** The position identity: a chain badge + protocol name, the canonical id
 * (ref-shortened, full in `title`) with a copy button, and — when the sidecar
 * exposes a real `pool_address` — a deep link to the pool contract. */
function PositionCell({ position }: PositionCellProps): JSX.Element {
  const parsed = parsePositionId(position.position_id)
  const chain = normalizeChain(position.chain) ?? parsed.chain
  const poolUrl = explorerAddressUrl(chain, position.pool_address ?? null)
  const explorer = explorerName(chain)
  return (
    <div className={styles.position}>
      <div className={styles.positionHead}>
        {chain && <span className={styles.chainBadge}>{chain}</span>}
        {parsed.protocol && <span className={styles.protocol}>{parsed.protocol}</span>}
        {poolUrl && explorer && (
          <ExternalLink
            url={poolUrl}
            title={t('defi.poolLinkTitle', { explorer })}
            className={styles.poolLink}
            testId="defi-pool-link"
          >
            {t('defi.explorerLink', { explorer })} ↗
          </ExternalLink>
        )}
      </div>
      <div className={styles.positionIdRow}>
        <code className={styles.positionId} title={position.position_id}>
          {displayPositionId(position.position_id)}
        </code>
        <CopyButton value={position.position_id} />
      </div>
    </div>
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
            <code className={styles.otherId} title={position.position_id}>
              {displayPositionId(position.position_id)}
            </code>
            <span className={styles.otherReason}>{otherReason(position)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ── Small shared components ──────────────────────────────────────────────────

interface ExternalLinkProps {
  url: string
  title: string
  className?: string
  testId?: string
  children: ReactNode
}

/** An anchor that opens in the OS browser via `shell.openExternal` (ADR-0008),
 * never navigating the renderer. */
function ExternalLink({ url, title, className, testId, children }: ExternalLinkProps): JSX.Element {
  return (
    <a
      href={url}
      rel="noreferrer"
      title={title}
      className={className}
      data-testid={testId}
      onClick={(e) => {
        e.preventDefault()
        void window.api?.shell?.openExternal({ url })
      }}
    >
      {children}
    </a>
  )
}

interface CopyButtonProps {
  value: string
}

/** Copies the full position id to the clipboard, flashing "Copied" briefly. */
function CopyButton({ value }: CopyButtonProps): JSX.Element {
  const [copied, setCopied] = useState(false)
  const onCopy = (): void => {
    void navigator.clipboard?.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }
  return (
    <button
      type="button"
      className={styles.copyButton}
      title={t('defi.copyId')}
      aria-label={t('defi.copyId')}
      data-testid="defi-copy-id"
      onClick={onCopy}
    >
      {copied ? t('defi.copied') : '⧉'}
    </button>
  )
}

// ── Cell + value helpers ─────────────────────────────────────────────────────

/** A sign-driven color class: bull for a gain, bear for a loss, none for zero
 * or a null (an unpriceable figure shows an em dash, not a colored zero). */
function signClass(value: number | null): string | undefined {
  if (value === null || value === 0 || !Number.isFinite(value)) return undefined
  return value > 0 ? styles.pos : styles.neg
}

/** USD with an explicit sign; an em dash for a null figure (never a fake 0). */
function signedUsd(value: number | null): string {
  return value === null ? '—' : formatUsdSigned(value)
}

/** Exact realized P&L for a window (never null in the model; em dash if absent),
 * sign-colored. */
function realizedCell(window: WindowPnl | undefined): ReactNode {
  if (window === undefined) return '—'
  return (
    <span className={signClass(window.realized_usd)}>{formatUsdSigned(window.realized_usd)}</span>
  )
}

/** The labeled estimate, parenthesized; an em dash when the window start couldn't
 * be priced (an honest per-window gap) or the window is absent. Deliberately
 * muted (no sign color) so the exact figures stay the visual headline. */
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

/** The distinct, known chains among a position set — the sidecar `chain` field
 * when present, else the chain parsed from `position_id`. Order-stable. */
function distinctChains(positions: PositionPnl[]): DefiChain[] {
  const seen = new Set<DefiChain>()
  const out: DefiChain[] = []
  for (const p of positions) {
    const chain = normalizeChain(p.chain) ?? parsePositionId(p.position_id).chain
    if (chain && !seen.has(chain)) {
      seen.add(chain)
      out.push(chain)
    }
  }
  return out
}

const KNOWN_CHAINS = new Set<string>(['ethereum', 'base', 'arbitrum', 'optimism'])

/** Narrow the sidecar's free-form `chain` string to a known `DefiChain`. */
function normalizeChain(chain: string | null | undefined): DefiChain | null {
  return chain && KNOWN_CHAINS.has(chain) ? (chain as DefiChain) : null
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

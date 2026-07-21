/**
 * Portfolio view (Plan 0043 phase 2, ADR-0042 / ADR-0037 / ADR-0029).
 *
 * The renderer surface over the cross-venue portfolio: unified holdings with
 * average-cost basis, unrealized P&L and exposure (by asset and by venue), each
 * venue leg stamped with its OWN as-of time — freshness is never blended into a
 * single implied "now" (the ADR-0042 negative-consequence mitigation). Plus a
 * DeFi risk panel: an Aave account leg (dial a collateral shock → health factor
 * + liquidation distance before/after; or a conditional P(liquidation) whose
 * volatility assumption is shown inline) and a constant-product LP leg (supply
 * the position's token amounts/prices, dial a single-underlying shock →
 * impermanent loss). Every conditional probability travels with its assumption
 * (ADR-0037); a bare probability is malformed and never rendered.
 *
 * Facts only — this view carries NO rebalance / exit / buy / sell control. That
 * crossing is the advisor's alone (ADR-0029); action does not exist here.
 *
 * Data comes from `GET /portfolio` + `POST /portfolio/risk` through the typed,
 * Zod-validating client — the REST twins of the `portfolio_summary` / `defi_risk`
 * MCP tools (the agent's surfaces).
 */
import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'

import { ApiError, sanitizeApiErrorBody } from '../api/client'
import { GlossaryTerm } from '../components/GlossaryTerm'
import { isValidAddress } from '../lib/defiWallets'
import { formatDateTime, formatUsd, formatUsdSigned } from '../lib/format'
import { t } from '../lib/i18n'
import { usePortfolio } from '../hooks/usePortfolio'
import { usePortfolioRisk } from '../hooks/usePortfolioRisk'
import type { Holding, PortfolioSurface } from '../schemas/portfolio'
import styles from './PortfolioView.module.css'

export function PortfolioView(): JSX.Element {
  const [walletInput, setWalletInput] = useState('')
  const [includeBasis, setIncludeBasis] = useState(true)
  const [invalid, setInvalid] = useState(false)
  const { state, load } = usePortfolio()

  // Load the CEX + manual legs immediately on mount (no wallet needed); the
  // DeFi leg switches on once a wallet is supplied.
  useEffect(() => {
    load(undefined, true)
  }, [load])

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault()
    const wallet = walletInput.trim()
    if (wallet !== '' && !isValidAddress(wallet)) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    load(wallet === '' ? undefined : wallet, includeBasis)
  }

  const ready = state.status === 'ready' ? state.result : null

  return (
    <section className={styles.root} aria-label={t('portfolio.title')}>
      <header className={styles.header}>
        <h2 className={styles.title}>{t('portfolio.title')}</h2>
        <p className={styles.lede}>{t('portfolio.lede')}</p>
      </header>

      <form className={styles.controls} onSubmit={onSubmit}>
        <div className={styles.field}>
          <label htmlFor="portfolio-wallet">{t('portfolio.walletLabel')}</label>
          <input
            id="portfolio-wallet"
            type="text"
            className={styles.wallet}
            value={walletInput}
            placeholder={t('portfolio.walletPlaceholder')}
            spellCheck={false}
            autoComplete="off"
            data-testid="portfolio-wallet"
            onChange={(e) => setWalletInput(e.target.value)}
          />
        </div>
        <label className={styles.checkbox}>
          <input
            type="checkbox"
            checked={includeBasis}
            data-testid="portfolio-include-basis"
            onChange={(e) => setIncludeBasis(e.target.checked)}
          />
          {t('portfolio.includeBasis')}
        </label>
        <button type="submit" data-testid="portfolio-load">
          {t('portfolio.load')}
        </button>
      </form>

      {invalid && (
        <div className={styles.invalid} role="alert" data-testid="portfolio-invalid">
          {t('portfolio.invalidWallet')}
        </div>
      )}

      {state.status === 'loading' && (
        <div className={styles.statusBlock} role="status" data-testid="portfolio-loading">
          {t('portfolio.loading')}
        </div>
      )}
      {state.status === 'error' && (
        <div className={styles.error} role="alert" data-testid="portfolio-error">
          {errorMessage(state.error)}
        </div>
      )}

      {ready && <Holdings result={ready} />}

      <RiskPanel />
    </section>
  )
}

// ── Holdings + exposure ──────────────────────────────────────────────────────

interface HoldingsProps {
  result: PortfolioSurface
}

function Holdings({ result }: HoldingsProps): JSX.Element {
  const { summary, leg_errors, notes } = result
  const legs = Object.entries(summary.legs_as_of)
  return (
    <div className={styles.results}>
      <div className={styles.summary} data-testid="portfolio-summary">
        <dl className={styles.stats}>
          <Stat termKey="defi_unrealized" label={t('portfolio.summary.unrealized')}>
            <span className={signClass(summary.unrealized_pnl_usd)}>
              {signedUsd(summary.unrealized_pnl_usd)}
            </span>
          </Stat>
        </dl>
        <ExposureList
          label={t('portfolio.summary.exposureVenue')}
          exposure={summary.exposure_by_venue}
          testId="portfolio-exposure-venue"
        />
        <ExposureList
          label={t('portfolio.summary.exposureAsset')}
          exposure={summary.exposure_by_asset}
          testId="portfolio-exposure-asset"
        />
      </div>

      {/* Per-leg freshness — each venue keeps its OWN as-of; never blended. */}
      {legs.length > 0 && (
        <div className={styles.legsAsOf} data-testid="portfolio-legs-asof">
          <span className={styles.legsAsOfLabel}>{t('portfolio.legsAsOfLabel')}</span>
          {legs.map(([venue, asOf]) => (
            <span
              key={venue}
              className={styles.legStamp}
              data-testid={`portfolio-leg-asof-${venue}`}
            >
              {venue}: {formatDateTime(asOf)}
            </span>
          ))}
        </div>
      )}

      {Object.keys(leg_errors).length > 0 && (
        <ul className={styles.legErrors} data-testid="portfolio-leg-errors">
          {Object.entries(leg_errors).map(([venue, reason]) => (
            <li key={venue} role="alert">
              {t('portfolio.legError', { venue, reason })}
            </li>
          ))}
        </ul>
      )}

      {summary.holdings.length === 0 ? (
        <div className={styles.statusBlock} role="status" data-testid="portfolio-empty">
          {t('portfolio.empty')}
        </div>
      ) : (
        <table className={styles.table} aria-label={t('portfolio.holdingsLabel')}>
          <thead>
            <tr>
              <th scope="col">{t('portfolio.col.venue')}</th>
              <th scope="col">{t('portfolio.col.asset')}</th>
              <th scope="col" className={styles.num}>
                {t('portfolio.col.quantity')}
              </th>
              <th scope="col" className={styles.num}>
                {t('portfolio.col.avgCost')}
              </th>
              <th scope="col" className={styles.num}>
                {t('portfolio.col.value')}
              </th>
              <th scope="col">{t('portfolio.col.pricingSource')}</th>
              <th scope="col">{t('portfolio.col.asOf')}</th>
            </tr>
          </thead>
          <tbody>
            {summary.holdings.map((h) => (
              <HoldingRow key={`${h.venue}:${h.symbol}:${h.kind ?? ''}`} holding={h} />
            ))}
          </tbody>
        </table>
      )}

      {notes.length > 0 && (
        <details className={styles.notes} data-testid="portfolio-notes">
          <summary>{t('portfolio.notesLabel')}</summary>
          <ul>
            {notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function HoldingRow({ holding }: { holding: Holding }): JSX.Element {
  return (
    <tr data-testid="portfolio-holding-row">
      <td>
        <span className={styles.venueBadge}>{holding.venue}</span>
      </td>
      <td className={styles.asset}>{holding.symbol}</td>
      <td className={styles.num}>{formatQty(holding.quantity)}</td>
      <td className={styles.num}>{holding.avg_cost == null ? '—' : formatUsd(holding.avg_cost)}</td>
      <td className={styles.num}>
        {holding.usd_value == null ? '—' : signedUsd(holding.usd_value)}
      </td>
      <td className={styles.source}>{holding.pricing_source ?? '—'}</td>
      <td className={styles.source}>{formatDateTime(holding.as_of)}</td>
    </tr>
  )
}

interface ExposureListProps {
  label: string
  exposure: Record<string, number>
  testId: string
}

function ExposureList({ label, exposure, testId }: ExposureListProps): JSX.Element | null {
  const rows = Object.entries(exposure).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
  if (rows.length === 0) return null
  return (
    <div className={styles.exposure} data-testid={testId}>
      <span className={styles.exposureLabel}>{label}</span>
      <div className={styles.chips}>
        {rows.map(([key, value]) => (
          <span key={key} className={styles.chip}>
            {key} {formatUsd(value)}
          </span>
        ))}
      </div>
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

// ── DeFi risk panel ──────────────────────────────────────────────────────────

function RiskPanel(): JSX.Element {
  return (
    <section
      className={styles.risk}
      aria-label={t('portfolio.risk.title')}
      data-testid="risk-panel"
    >
      <header className={styles.riskHeader}>
        <h3 className={styles.riskTitle}>{t('portfolio.risk.title')}</h3>
        <p className={styles.riskLede}>{t('portfolio.risk.lede')}</p>
      </header>
      <div className={styles.riskLegs}>
        <AaveRisk />
        <LpRisk />
      </div>
    </section>
  )
}

type Chain = 'ethereum' | 'base'
type RiskMode = 'scenario' | 'conditional'

function AaveRisk(): JSX.Element {
  const [address, setAddress] = useState('')
  const [chain, setChain] = useState<Chain>('base')
  const [shock, setShock] = useState(-0.3)
  const [mode, setMode] = useState<RiskMode>('scenario')
  const [collateralSymbol, setCollateralSymbol] = useState('')
  const [horizon, setHorizon] = useState(30)
  const { state, recompute } = usePortfolioRisk()

  const canRun = isValidAddress(address)

  const runScenario = (nextShock: number): void => {
    if (!canRun) return
    recompute({ kind: 'scenario', address, chain, collateral_shock: nextShock })
  }
  const runConditional = (): void => {
    if (!canRun || collateralSymbol.trim() === '') return
    recompute({
      kind: 'conditional',
      address,
      chain,
      collateral_symbol: collateralSymbol.trim(),
      horizon_days: horizon,
    })
  }

  const aave = state.status === 'ready' ? state.result.aave : null

  return (
    <div className={styles.leg} data-testid="risk-aave">
      <h4 className={styles.legTitle}>{t('portfolio.risk.aaveTitle')}</h4>

      <div className={styles.field}>
        <label htmlFor="risk-aave-address">{t('portfolio.risk.addressLabel')}</label>
        <input
          id="risk-aave-address"
          type="text"
          value={address}
          placeholder={t('portfolio.walletPlaceholder')}
          spellCheck={false}
          autoComplete="off"
          data-testid="risk-aave-address"
          onChange={(e) => setAddress(e.target.value)}
        />
      </div>
      <div className={styles.field}>
        <label htmlFor="risk-aave-chain">{t('portfolio.risk.chainLabel')}</label>
        <select
          id="risk-aave-chain"
          value={chain}
          data-testid="risk-aave-chain"
          onChange={(e) => setChain(e.target.value as Chain)}
        >
          <option value="base">{t('portfolio.risk.chainBase')}</option>
          <option value="ethereum">{t('portfolio.risk.chainEthereum')}</option>
        </select>
      </div>

      <ModeTabs mode={mode} onChange={setMode} idPrefix="risk-aave" />

      {mode === 'scenario' ? (
        <>
          <ShockSlider
            value={shock}
            onChange={(v) => {
              setShock(v)
              runScenario(v)
            }}
            testId="risk-aave-shock"
          />
          <button
            type="button"
            disabled={!canRun}
            data-testid="risk-aave-recompute"
            onClick={() => runScenario(shock)}
          >
            {t('portfolio.risk.recompute')}
          </button>
        </>
      ) : (
        <>
          <div className={styles.field}>
            <label htmlFor="risk-aave-symbol">{t('portfolio.risk.collateralSymbolLabel')}</label>
            <input
              id="risk-aave-symbol"
              type="text"
              value={collateralSymbol}
              placeholder={t('portfolio.risk.collateralSymbolPlaceholder')}
              spellCheck={false}
              autoComplete="off"
              data-testid="risk-aave-symbol"
              onChange={(e) => setCollateralSymbol(e.target.value)}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="risk-aave-horizon">{t('portfolio.risk.horizonLabel')}</label>
            <input
              id="risk-aave-horizon"
              type="number"
              min={1}
              value={horizon}
              data-testid="risk-aave-horizon"
              onChange={(e) => setHorizon(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
          <button
            type="button"
            disabled={!canRun || collateralSymbol.trim() === ''}
            data-testid="risk-aave-probability"
            onClick={runConditional}
          >
            {t('portfolio.risk.computeProbability')}
          </button>
        </>
      )}

      {!canRun && <p className={styles.hint}>{t('portfolio.risk.aaveInputsHint')}</p>}
      <RiskStatus status={state.status} error={state.status === 'error' ? state.error : null} />

      {aave?.scenario && (
        <dl className={styles.riskResult} data-testid="risk-aave-scenario">
          <RiskRow termKey="portfolio_health_factor" label={t('portfolio.risk.hfBefore')}>
            {formatNum(aave.scenario.health_factor_before)}
          </RiskRow>
          <RiskRow termKey="portfolio_health_factor" label={t('portfolio.risk.hfAfter')}>
            <strong>{formatNum(aave.scenario.health_factor_after)}</strong>
          </RiskRow>
          <RiskRow
            termKey="portfolio_liquidation_distance"
            label={t('portfolio.risk.liqDistBefore')}
          >
            {formatPercent(aave.scenario.liquidation_distance_before)}
          </RiskRow>
          <RiskRow
            termKey="portfolio_liquidation_distance"
            label={t('portfolio.risk.liqDistAfter')}
          >
            <strong>{formatPercent(aave.scenario.liquidation_distance_after)}</strong>
          </RiskRow>
        </dl>
      )}

      {aave?.liquidation && (
        <dl className={styles.riskResult} data-testid="risk-aave-conditional">
          <RiskRow
            termKey="portfolio_liq_probability"
            label={t('portfolio.risk.pLiq', { days: aave.liquidation.horizon_days })}
          >
            <strong>{formatPercent(aave.liquidation.probability)}</strong>
          </RiskRow>
          {/* The vol assumption travels WITH the probability — never a bare number. */}
          <p className={styles.assumption} data-testid="risk-aave-assumption">
            {t('portfolio.risk.assumption', { assumption: aave.liquidation.assumption })}
          </p>
        </dl>
      )}

      {aave?.error && (
        <p className={styles.hint} role="alert" data-testid="risk-aave-leg-error">
          {aave.message ?? aave.error}
        </p>
      )}
    </div>
  )
}

function LpRisk(): JSX.Element {
  const [amount0, setAmount0] = useState('1')
  const [price0, setPrice0] = useState('3000')
  const [amount1, setAmount1] = useState('3000')
  const [price1, setPrice1] = useState('1')
  const [shock, setShock] = useState(-0.3)
  const { state, recompute } = usePortfolioRisk()

  const nums = {
    amount0: Number(amount0),
    price0: Number(price0),
    amount1: Number(amount1),
    price1: Number(price1),
  }
  const canRun = Object.values(nums).every((n) => Number.isFinite(n) && n > 0)

  const run = (nextShock: number): void => {
    if (!canRun) return
    recompute({
      kind: 'scenario',
      // Single-underlying: token 1 is the numeraire (unshocked), the shock moves token 0.
      lp: { ...nums, shock0: nextShock, shock1: 0 },
    })
  }

  const lp = state.status === 'ready' ? state.result.lp : null
  const scenario = lp && 'impermanent_loss' in lp ? lp : null

  return (
    <div className={styles.leg} data-testid="risk-lp">
      <h4 className={styles.legTitle}>{t('portfolio.risk.lpTitle')}</h4>

      <div className={styles.lpGrid}>
        <NumField
          id="risk-lp-amount0"
          label={t('portfolio.risk.amount0')}
          value={amount0}
          onChange={setAmount0}
        />
        <NumField
          id="risk-lp-price0"
          label={t('portfolio.risk.price0')}
          value={price0}
          onChange={setPrice0}
        />
        <NumField
          id="risk-lp-amount1"
          label={t('portfolio.risk.amount1')}
          value={amount1}
          onChange={setAmount1}
        />
        <NumField
          id="risk-lp-price1"
          label={t('portfolio.risk.price1')}
          value={price1}
          onChange={setPrice1}
        />
      </div>
      <p className={styles.hint}>{t('portfolio.risk.lpHint')}</p>

      <ShockSlider
        value={shock}
        onChange={(v) => {
          setShock(v)
          run(v)
        }}
        testId="risk-lp-shock"
      />
      <button
        type="button"
        disabled={!canRun}
        data-testid="risk-lp-recompute"
        onClick={() => run(shock)}
      >
        {t('portfolio.risk.recompute')}
      </button>

      <RiskStatus status={state.status} error={state.status === 'error' ? state.error : null} />

      {scenario && (
        <dl className={styles.riskResult} data-testid="risk-lp-scenario">
          <RiskRow termKey="portfolio_impermanent_loss" label={t('portfolio.risk.impermanentLoss')}>
            <strong className={signClass(-Math.abs(scenario.impermanent_loss))}>
              {formatPercent(scenario.impermanent_loss)}
            </strong>
          </RiskRow>
          <RiskRow termKey="defi_position" label={t('portfolio.risk.valueBefore')}>
            {formatUsd(scenario.value_before)}
          </RiskRow>
          <RiskRow termKey="defi_position" label={t('portfolio.risk.lpValueAfter')}>
            {formatUsd(scenario.lp_value_after)}
          </RiskRow>
          <RiskRow termKey="defi_position" label={t('portfolio.risk.hodlValueAfter')}>
            {formatUsd(scenario.hodl_value_after)}
          </RiskRow>
        </dl>
      )}
    </div>
  )
}

// ── Small shared risk-panel components ───────────────────────────────────────

interface ModeTabsProps {
  mode: RiskMode
  onChange: (mode: RiskMode) => void
  idPrefix: string
}

function ModeTabs({ mode, onChange, idPrefix }: ModeTabsProps): JSX.Element {
  return (
    <div className={styles.modeTabs} role="tablist" aria-label={t('portfolio.risk.title')}>
      <button
        type="button"
        role="tab"
        aria-selected={mode === 'scenario'}
        className={mode === 'scenario' ? styles.modeActive : styles.mode}
        data-testid={`${idPrefix}-tab-scenario`}
        onClick={() => onChange('scenario')}
      >
        {t('portfolio.risk.scenarioTab')}
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === 'conditional'}
        className={mode === 'conditional' ? styles.modeActive : styles.mode}
        data-testid={`${idPrefix}-tab-conditional`}
        onClick={() => onChange('conditional')}
      >
        {t('portfolio.risk.conditionalTab')}
      </button>
    </div>
  )
}

interface ShockSliderProps {
  value: number
  onChange: (value: number) => void
  testId: string
}

function ShockSlider({ value, onChange, testId }: ShockSliderProps): JSX.Element {
  return (
    <div className={styles.field}>
      <label htmlFor={testId}>
        {t('portfolio.risk.shockLabel')}{' '}
        <span className={styles.shockValue}>{formatPercent(value)}</span>
      </label>
      <input
        id={testId}
        type="range"
        min={-0.9}
        max={0.9}
        step={0.05}
        value={value}
        data-testid={testId}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  )
}

interface NumFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
}

function NumField({ id, label, value, onChange }: NumFieldProps): JSX.Element {
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        min={0}
        step="any"
        value={value}
        data-testid={id}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

interface RiskRowProps {
  termKey: string
  label: string
  children: ReactNode
}

function RiskRow({ termKey, label, children }: RiskRowProps): JSX.Element {
  return (
    <div className={styles.riskRow}>
      <dt>
        <GlossaryTerm termKey={termKey}>{label}</GlossaryTerm>
      </dt>
      <dd>{children}</dd>
    </div>
  )
}

function RiskStatus({
  status,
  error,
}: {
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: Error | null
}): JSX.Element | null {
  if (status === 'loading') {
    return (
      <p className={styles.hint} role="status">
        {t('portfolio.risk.loading')}
      </p>
    )
  }
  if (status === 'error' && error) {
    return (
      <p className={styles.error} role="alert" data-testid="risk-error">
        {t('portfolio.risk.error', { message: errorMessage(error) })}
      </p>
    )
  }
  return null
}

// ── Value helpers ────────────────────────────────────────────────────────────

/** A signed USD figure; an em dash for a null (never a fake 0). */
function signedUsd(value: number | null): string {
  return value === null ? '—' : formatUsdSigned(value)
}

/** A bull/bear/none sign class; none for zero, null, or non-finite. */
function signClass(value: number | null): string | undefined {
  if (value === null || value === 0 || !Number.isFinite(value)) return undefined
  return value > 0 ? styles.pos : styles.neg
}

/** A plain fixed-decimal number (health factor, ratios) — an em dash for a
 * null/non-finite. A very large health factor (a no-debt account) reads as ∞. */
function formatNum(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return '—'
  if (value >= 1e6) return '∞'
  return value.toFixed(digits)
}

/** A fraction as an unsigned percent ("6.20%") — probabilities, IL, shocks,
 * liquidation distance. An em dash for a null/non-finite. */
function formatPercent(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** A token quantity with a bounded fraction; signed so a short/liability reads
 * as negative. */
function formatQty(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return value.toLocaleString('en-US', { maximumFractionDigits: 6, signDisplay: 'exceptZero' })
}

/** Map a fetch failure to an actionable message: the venue-key 503s point at
 * Settings; everything else keeps the client's already-localized message. */
function errorMessage(error: Error): string {
  if (error instanceof ApiError) {
    const detail = sanitizeApiErrorBody(error.body)
    if (detail === 'no account-holdings source configured') {
      return t('portfolio.error.setKeyHint')
    }
  }
  return error.message
}

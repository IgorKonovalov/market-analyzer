/**
 * Settings page — reveal, copy, and rotate the MCP bearer secret. Plan 0006 phase 5.
 *
 * The secret is fetched on mount and held in component state but is *not*
 * rendered into the DOM until the user clicks Reveal. The Jest spec asserts
 * this: a screen reader walking the a11y tree on initial render must not see
 * the secret. Copy uses `navigator.clipboard.writeText`. Rotate calls the
 * sidecar's POST /settings/mcp-secret/rotate, which mutates the running
 * middleware's secret so the *next* MCP request with the old bearer 401s.
 */
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '../api/client'
import { ChartStyleControls } from '../components/ChartStyleControls'
import { useThemePref } from '../hooks/useThemePref'
import type { ThemePref } from '../lib/theme'
import type { McpSecretRecord } from '../types/sidecar/mcp-secret-record'
import styles from './SettingsView.module.css'

const THEME_OPTIONS: ReadonlyArray<{ value: ThemePref; label: string }> = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
]

interface State {
  record: McpSecretRecord | null
  port: number | null
  revealed: boolean
  copiedAt: number | null
  rotatingAt: number | null
  stoppingAt: number | null
  stopRequested: boolean
  error: string | null
}

const INITIAL_STATE: State = {
  record: null,
  port: null,
  revealed: false,
  copiedAt: null,
  rotatingAt: null,
  stoppingAt: null,
  stopRequested: false,
  error: null,
}

const COPY_FEEDBACK_MS = 1500

export function SettingsView(): JSX.Element {
  const [state, setState] = useState<State>(INITIAL_STATE)
  const [themePref, setThemePref] = useThemePref()

  useEffect(() => {
    let alive = true
    Promise.all([api.getMcpSecret(), api.getSidecarPort()])
      .then(([record, port]) => {
        if (!alive) return
        setState((s) => ({ ...s, record, port, error: null }))
      })
      .catch((err: unknown) => {
        if (!alive) return
        const message = err instanceof Error ? err.message : 'failed to load MCP settings'
        setState((s) => ({ ...s, error: message }))
      })
    return () => {
      alive = false
    }
  }, [])

  const handleReveal = useCallback((): void => {
    setState((s) => ({ ...s, revealed: true }))
  }, [])

  const handleHide = useCallback((): void => {
    setState((s) => ({ ...s, revealed: false }))
  }, [])

  const handleCopy = useCallback(async (): Promise<void> => {
    if (!state.record) return
    try {
      await navigator.clipboard.writeText(state.record.secret)
      setState((s) => ({ ...s, copiedAt: Date.now() }))
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'clipboard write failed'
      setState((s) => ({ ...s, error: message }))
    }
  }, [state.record])

  const handleRotate = useCallback(async (): Promise<void> => {
    setState((s) => ({ ...s, rotatingAt: Date.now(), error: null }))
    try {
      const next = await api.rotateMcpSecret()
      setState((s) => ({
        ...s,
        record: next,
        revealed: true,
        copiedAt: null,
        rotatingAt: null,
        error: null,
      }))
    } catch (err: unknown) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'rotate failed'
      setState((s) => ({ ...s, rotatingAt: null, error: message }))
    }
  }, [])

  const handleStop = useCallback(async (): Promise<void> => {
    setState((s) => ({ ...s, stoppingAt: Date.now(), error: null }))
    try {
      await api.stopSidecar()
      setState((s) => ({ ...s, stoppingAt: null, stopRequested: true, error: null }))
    } catch (err: unknown) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'stop failed'
      setState((s) => ({ ...s, stoppingAt: null, error: message }))
    }
  }, [])

  // Clear the "Copied!" indicator after the feedback window.
  useEffect(() => {
    if (state.copiedAt == null) return
    const timer = setTimeout(() => {
      setState((s) => (s.copiedAt == null ? s : { ...s, copiedAt: null }))
    }, COPY_FEEDBACK_MS)
    return () => clearTimeout(timer)
  }, [state.copiedAt])

  const endpointUrl =
    state.port != null ? `http://127.0.0.1:${state.port}/mcp` : 'http://127.0.0.1:<loading>/mcp'

  const snippet = buildClaudeDesktopSnippet({
    url: endpointUrl,
    bearer: state.revealed && state.record ? state.record.secret : '<click Reveal to see secret>',
  })

  const isLoading = state.record == null && state.error == null

  return (
    <div className={styles.root}>
      <section className={styles.block} aria-labelledby="appearance-heading">
        <h2 id="appearance-heading" className={styles.heading}>
          Appearance
        </h2>
        <p className={styles.lede}>
          Choose how the app looks. <strong>System</strong> follows your operating system&apos;s
          light/dark setting; Light and Dark pin it regardless of the OS.
        </p>
        <div className={styles.field}>
          <span className={styles.fieldLabel} id="theme-label">
            Theme
          </span>
          <div className={styles.segmented} role="radiogroup" aria-labelledby="theme-label">
            {THEME_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={styles.segment}
                data-active={themePref === opt.value}
                data-testid={`theme-option-${opt.value}`}
              >
                <input
                  type="radio"
                  name="theme-pref"
                  className={styles.segmentInput}
                  value={opt.value}
                  checked={themePref === opt.value}
                  onChange={() => setThemePref(opt.value)}
                />
                {opt.label}
              </label>
            ))}
          </div>
        </div>
      </section>

      <section className={styles.block} aria-labelledby="chart-style-heading">
        <h2 id="chart-style-heading" className={styles.heading}>
          Chart style
        </h2>
        <p className={styles.lede}>
          Recolour and resize the candlestick chart&apos;s lines and markers. Colours and widths are
          saved <strong>per theme</strong>; you&apos;re editing the theme the chart is currently
          showing.
        </p>
        <ChartStyleControls />
      </section>

      <section className={styles.block} aria-labelledby="settings-heading">
        <h2 id="settings-heading" className={styles.heading}>
          MCP access
        </h2>
        <p className={styles.lede}>
          Claude Desktop and other MCP clients connect to the sidecar at the URL below using the
          bearer token. The token is long-lived and survives app restarts.
        </p>

        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mcp-endpoint-url">
            Endpoint URL
          </label>
          <input
            id="mcp-endpoint-url"
            className={styles.codeInput}
            type="text"
            value={endpointUrl}
            readOnly
            onFocus={(e) => e.currentTarget.select()}
          />
        </div>

        <div className={styles.field}>
          <span className={styles.fieldLabel} id="mcp-secret-label">
            Bearer token
          </span>
          {isLoading && (
            <div className={styles.loading} role="status">
              Loading…
            </div>
          )}
          {!isLoading && state.error != null && state.record == null && (
            <div className={styles.error} role="alert">
              {state.error}
            </div>
          )}
          {state.record != null && (
            <div className={styles.secretRow}>
              {state.revealed ? (
                <code
                  className={styles.secret}
                  data-testid="mcp-secret-plaintext"
                  aria-labelledby="mcp-secret-label"
                >
                  {state.record.secret}
                </code>
              ) : (
                <code
                  className={styles.secret}
                  aria-labelledby="mcp-secret-label"
                  aria-hidden="true"
                  data-testid="mcp-secret-hidden"
                >
                  {'•'.repeat(16)}
                </code>
              )}
              <div className={styles.controls}>
                {state.revealed ? (
                  <button
                    type="button"
                    className={styles.button}
                    onClick={handleHide}
                    data-testid="mcp-secret-hide"
                  >
                    Hide
                  </button>
                ) : (
                  <button
                    type="button"
                    className={styles.button}
                    onClick={handleReveal}
                    data-testid="mcp-secret-reveal"
                  >
                    Reveal
                  </button>
                )}
                <button
                  type="button"
                  className={styles.button}
                  onClick={handleCopy}
                  disabled={!state.revealed}
                  data-testid="mcp-secret-copy"
                >
                  {state.copiedAt != null ? 'Copied!' : 'Copy'}
                </button>
                <button
                  type="button"
                  className={styles.danger}
                  onClick={handleRotate}
                  disabled={state.rotatingAt != null}
                  data-testid="mcp-secret-rotate"
                >
                  {state.rotatingAt != null ? 'Rotating…' : 'Rotate'}
                </button>
              </div>
            </div>
          )}
          {state.record != null && (
            <p className={styles.warning}>
              Rotating generates a new token and invalidates the existing one immediately. Any
              active MCP clients will need to be reconfigured with the new token.
            </p>
          )}
          {state.error != null && state.record != null && (
            <p className={styles.errorInline} role="alert">
              {state.error}
            </p>
          )}
        </div>

        <div className={styles.field}>
          <span className={styles.fieldLabel}>Sidecar lifecycle</span>
          <p className={styles.lede}>
            The sidecar runs as a standalone process — closing this window does not stop it. MCP
            clients can keep talking to it. Click below to stop it explicitly.
          </p>
          <div className={styles.controls}>
            <button
              type="button"
              className={styles.danger}
              onClick={handleStop}
              disabled={state.stoppingAt != null || state.stopRequested}
              data-testid="sidecar-stop"
            >
              {state.stoppingAt != null
                ? 'Stopping…'
                : state.stopRequested
                  ? 'Stop requested'
                  : 'Stop sidecar'}
            </button>
          </div>
          {state.stopRequested && (
            <p className={styles.warning}>
              Sidecar shutdown requested. The viewer will lose its sidecar connection.
            </p>
          )}
        </div>

        <div className={styles.field}>
          <span className={styles.fieldLabel}>Claude Desktop snippet</span>
          <p className={styles.lede}>
            Paste this into <code>claude_desktop_config.json</code>. Reveal the token first so the
            snippet contains the real value.
          </p>
          <pre className={styles.snippet} data-testid="mcp-config-snippet">
            {snippet}
          </pre>
        </div>
      </section>
    </div>
  )
}

interface SnippetParams {
  url: string
  bearer: string
}

function buildClaudeDesktopSnippet({ url, bearer }: SnippetParams): string {
  return [
    '{',
    '  "mcpServers": {',
    '    "market-analyser": {',
    `      "url": "${url}",`,
    '      "headers": {',
    `        "Authorization": "Bearer ${bearer}"`,
    '      }',
    '    }',
    '  }',
    '}',
  ].join('\n')
}

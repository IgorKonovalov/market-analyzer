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
import type { McpSecretRecord } from '../types/sidecar/mcp-secret-record'
import styles from './SettingsView.module.css'

interface State {
  record: McpSecretRecord | null
  port: number | null
  revealed: boolean
  copiedAt: number | null
  rotatingAt: number | null
  error: string | null
}

const INITIAL_STATE: State = {
  record: null,
  port: null,
  revealed: false,
  copiedAt: null,
  rotatingAt: null,
  error: null,
}

const COPY_FEEDBACK_MS = 1500

export function SettingsView(): JSX.Element {
  const [state, setState] = useState<State>(INITIAL_STATE)

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
    <section className={styles.root} aria-labelledby="settings-heading">
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
            Rotating generates a new token and invalidates the existing one immediately. Any active
            MCP clients will need to be reconfigured with the new token.
          </p>
        )}
        {state.error != null && state.record != null && (
          <p className={styles.errorInline} role="alert">
            {state.error}
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

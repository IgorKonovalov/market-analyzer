# Best practices — ui-builder

Longer-form notes on the things that bite in this stack. SKILL.md has the rules; this file has the *why* and the patterns. Read on demand.

## Chart lifecycle (lightweight-charts)

`lightweight-charts` is the canonical case but the pattern applies to anything non-React that owns DOM (Monaco, AG Grid, Chart.js, ResizeObservers, ResizeSensors, `setInterval`).

### The contract

A chart owns a Canvas/WebGL context plus event listeners. If you don't tear it down on unmount, every navigation leaks a chart, and DevTools shows your renderer's memory climbing monotonically until the user quits the app.

```tsx
function CandlestickChart({ bars }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  // First effect: create the chart once.
  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' } },
      autoSize: true,
    })
    const series = chart.addCandlestickSeries()
    chartRef.current = chart
    seriesRef.current = series
    return () => {
      chart.remove()  // <- non-negotiable
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])  // empty deps — chart instance is stable

  // Second effect: push data when bars change. Don't recreate the chart for new data.
  useEffect(() => {
    seriesRef.current?.setData(bars.map(toLightweightBar))
  }, [bars])

  return <div ref={containerRef} className={styles.chartContainer} />
}
```

### Why two effects, not one

If you put `setData` in the same effect that creates the chart and depend on `[bars]`, you re-create the chart on every data change. That's a different bug from a memory leak — it's a perf bug — and it visibly flickers. Keep creation and data separate.

### Resize

`autoSize: true` covers most cases. If it doesn't (e.g. the container animates), use a `ResizeObserver`:

```tsx
useEffect(() => {
  if (!containerRef.current || !chartRef.current) return
  const observer = new ResizeObserver((entries) => {
    const { width, height } = entries[0].contentRect
    chartRef.current?.applyOptions({ width, height })
  })
  observer.observe(containerRef.current)
  return () => observer.disconnect()
}, [])
```

Note the cleanup on the observer too. Same rule.

### Data shape mismatch

`lightweight-charts` wants `{ time, open, high, low, close }` with `time` as seconds-since-epoch *or* `'YYYY-MM-DD'`. Our `Bar` has `event_ts` as ISO 8601. The mapping lives next to the typed client:

```ts
// desktop/renderer/api/client.ts
export function toLightweightBar(b: Bar): CandlestickData {
  return {
    time: Math.floor(new Date(b.event_ts).getTime() / 1000) as UTCTimestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }
}
```

Don't inline this in components — every chart needs the same mapping; centralize it.

## IPC ergonomics

### The mental model

The renderer calls `window.api.foo(...)` and gets a Promise back. Under the hood that's `ipcRenderer.invoke(channel, payload)`. The main-process handler `ipcMain.handle(channel, (event, payload) => ...)` does the work and returns a value or throws — `invoke` rejects on throw.

### Adding a new channel — the checklist

Six places to touch, in order:

1. **`desktop/shared/ipc-channels.ts`** — add the constant.
   ```ts
   export const IPC_CHANNELS = {
     // ...existing
     DIALOG_OPEN_FILE: 'dialog:open-file',
   } as const
   ```
2. **`desktop/shared/schemas/dialog.ts`** — Zod schema for the payload.
   ```ts
   export const OpenFileRequestSchema = z.object({
     filters: z.array(z.object({ name: z.string(), extensions: z.array(z.string()) })).optional(),
   })
   export type OpenFileRequest = z.infer<typeof OpenFileRequestSchema>
   ```
3. **`desktop/electron/ipc/dialogHandlers.ts`** — handler. Validates with Zod before doing anything.
   ```ts
   export function registerDialogHandlers() {
     ipcMain.handle(IPC_CHANNELS.DIALOG_OPEN_FILE, async (_, payload: unknown) => {
       const req = OpenFileRequestSchema.parse(payload)  // throws → invoke rejects in renderer
       const result = await dialog.showOpenDialog({ properties: ['openFile'], filters: req.filters })
       return result.canceled ? null : result.filePaths[0]
     })
   }
   export function cleanupDialogHandlers() {
     ipcMain.removeHandler(IPC_CHANNELS.DIALOG_OPEN_FILE)
   }
   ```
4. **`desktop/electron/preload/api/dialog.ts`** — preload binding. Type comes from the schema.
   ```ts
   export const dialog = {
     openFile: (req: OpenFileRequest) => ipcRenderer.invoke(IPC_CHANNELS.DIALOG_OPEN_FILE, req) as Promise<string | null>,
   }
   ```
5. **`desktop/electron/preload/index.ts`** — already imports and merges per-domain modules; nothing new.
6. **Wire registration in `main.ts`** — already calls `registerIpcHandlers()` which calls `registerDialogHandlers()`; nothing new.

The renderer then calls `await window.api.dialog.openFile({})` with full type inference.

### Push events (M→R)

Push events follow a different shape because the renderer subscribes, not invokes:

```ts
// desktop/electron/preload/api/sidecar.ts
export const sidecar = {
  onStatus(callback: (status: SidecarStatus) => void): () => void {
    const handler = (_: unknown, status: unknown) => {
      callback(SidecarStatusSchema.parse(status))  // validate on receive too
    }
    ipcRenderer.on(IPC_CHANNELS.SIDECAR_STATUS, handler)
    return () => ipcRenderer.off(IPC_CHANNELS.SIDECAR_STATUS, handler)
  },
}
```

Component:

```tsx
useEffect(() => {
  return window.api.sidecar.onStatus((status) => {
    setSidecarStatus(status)
  })
}, [])
```

The `return` is critical — that's the cleanup that prevents the listener from accumulating across mounts. No naked `onStatus` without storing the cleanup.

### Don't add a channel for domain logic

If you're tempted to add `ohlcv:get`, `strategy:list`, `backtest:run`, stop. Those go through the sidecar's HTTP API. The IPC surface is for OS integration (file dialogs, external URLs, native menus) and shell-level coordination (sidecar status, app info). That's it.

## The typed fetch client

`desktop/renderer/api/client.ts` is the only place that calls `fetch` directly. Everything else imports from here.

```ts
// desktop/renderer/api/client.ts
import type { Bar } from '@/types/sidecar/types'

let cached: { port: number; secretToken: string } | null = null

async function getConfig() {
  if (cached) return cached
  cached = await window.api.sidecar.getPort()
  return cached
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const { port, secretToken } = await getConfig()
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    ...init,
    headers: { ...init?.headers, Authorization: `Bearer ${secretToken}` },
  })
  if (!res.ok) throw new ApiError(res.status, await res.text())
  return res.json() as Promise<T>
}

export const api = {
  getOhlcv: (params: { symbol: string; timeframe: string; start?: string; end?: string }) =>
    call<Bar[]>(`/ohlcv?${new URLSearchParams(params).toString()}`),
  // ...other endpoints
}
```

### Why a single client

- The bearer secret is fetched once per session and cached. Components don't need to know.
- Base URL is centralized — production vs. dev doesn't change anything because the port is always per-launch.
- Type narrowing happens here. Components import `api.getOhlcv` and get a `Promise<Bar[]>` for free.
- Adding logging, retries, or React Query cache keys is one place to edit.

### React Query integration

```tsx
function CandlestickChartView({ symbol, timeframe }: Props) {
  const { data: bars, isLoading, error } = useQuery({
    queryKey: ['ohlcv', symbol, timeframe],
    queryFn: () => api.getOhlcv({ symbol, timeframe }),
    staleTime: 60_000,
  })
  if (isLoading) return <ChartSkeleton />
  if (error) return <ChartError error={error} />
  if (!bars || bars.length === 0) return <ChartEmpty />
  return <CandlestickChart bars={bars} />
}
```

Query keys: tuple of `[domain, ...identifying-params]`. Don't include things that aren't identifying (e.g. don't put `Date.now()` in there).

## CSS Modules patterns

One `.module.css` per component, co-located:

```
components/
├── CandlestickChart.tsx
├── CandlestickChart.module.css
├── CandlestickChart.test.tsx
```

```css
/* CandlestickChart.module.css */
.chartContainer {
  width: 100%;
  height: 100%;
  min-height: 320px;
}

.toolbar {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-1);
}
```

```tsx
import styles from './CandlestickChart.module.css'

return <div className={styles.chartContainer}>...</div>
```

### Variables, not magic numbers

Define design tokens in `desktop/renderer/styles.css` as CSS custom properties:

```css
:root {
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 16px;
  --color-bg: #fafafa;
  --color-fg: #1a1a1a;
  --color-border: #e5e5e5;
  --font-mono: ui-monospace, SFMono-Regular, monospace;
}
```

Use `var(--space-2)` everywhere. Hex codes scattered in component CSS is a refactor smell.

### Composing class names

If you need conditional classes, use a tiny helper rather than `clsx`/`classnames`:

```ts
function cx(...classes: (string | false | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}
```

```tsx
<button className={cx(styles.btn, isActive && styles.btnActive)}>
```

A 3-line helper beats a dependency.

## Loading / error / empty states

Every async view has four states. **All four must be visible in the code** — not commented out, not deferred:

1. **Loading** — a skeleton or spinner, never a frozen UI.
2. **Error** — the error message, the action that failed, and a retry button. Never silent.
3. **Empty** — explicit "no data" state when the request succeeded but returned `[]`. Distinct from loading and error.
4. **Populated** — the happy path.

Spinners that never resolve are the most common UX failure. If a request can hang, give it a timeout in the fetch client and surface the timeout as an error state.

## Vite vs Electron gotchas

### CSP in dev vs prod

Vite injects `<script type="module">` and HMR-related code that needs `'unsafe-inline'` in `script-src`. The double-CSP rule in ADR-0008 says: allow `'unsafe-inline'` only when `app.isPackaged === false`. In production the CSP strips it.

If you're seeing CSP violations in dev that don't exist in prod, that's usually Vite. If you're seeing CSP violations in prod that don't exist in dev, that's a real bug — usually an inline event handler (`onclick="..."`) somewhere.

### File paths

In dev, renderer assets are served from `http://localhost:5173`. In prod, from `file://...dist/renderer/index.html`. Use Vite's `import.meta.env` or `import.meta.url` to derive paths, never hard-code `file://` strings.

Importing static assets:

```tsx
import logoUrl from './assets/logo.svg'
// logoUrl is a hashed URL in prod, a dev-server path in dev
<img src={logoUrl} alt="" />
```

Don't reach into the renderer's `process.resourcesPath` (that's main-process territory) — pass paths through IPC if you ever need a packaged-resource URL.

### Hot reload

Vite handles renderer hot-reload. Main and preload don't — you need to restart Electron. `pnpm --filter desktop dev:debug` includes a watcher that restarts on main/preload changes; if you don't see your IPC handler updates, that's usually missing this.

## React patterns specific to this app

### Routing

Single-window app, route via React Router or a tiny custom switch — don't over-engineer. The bootstrap is single-route (`/`). When we add more routes, they live under `desktop/renderer/views/`.

### Forms from `Params.model_json_schema()`

When rendering a strategy's `Params` as a form, the JSON schema comes from the sidecar:

```ts
const schema = await api.getStrategySchema(strategyId)  // pydantic JSON schema
```

Build a small renderer that maps schema field types to inputs:

| JSON Schema type | Input |
|------------------|-------|
| `integer` with `minimum`/`maximum` | `<input type="number">` |
| `number` | `<input type="number" step="any">` |
| `string` with `enum` | `<select>` |
| `string` | `<input type="text">` |
| `boolean` | `<input type="checkbox">` |
| `array` of primitives | comma-separated text input, parsed |

Validate on submit by sending the form values back to the sidecar (it'll re-validate with pydantic). Don't reimplement pydantic constraint validation in TypeScript.

### State

For local component state, `useState`. For server state (fetched data), React Query. For cross-component client state — defer until you have a measured need. The bootstrap doesn't need Redux/Zustand; adding one is an architect decision.

### Refs

Use `useRef` for:
- DOM nodes you give to third-party libraries (charts, editors).
- Mutable values that shouldn't trigger renders (last-seen timestamp, accumulated state inside a single effect).

Don't use `useRef` to read form input values — that's a controlled-input antipattern.

## Accessibility

Not optional. The minimum bar:

- Every button is a `<button>` element with type `button` (or `submit` inside a `<form>`). Never `<div onClick>`.
- Every input has either a visible `<label>` or `aria-label`.
- Every chart has `role="img"` + an `aria-label` describing the content ("Candlestick chart for AAPL daily prices, January through May 2026").
- Color is never the only carrier of information (e.g. don't show "green = up, red = down" without also showing the numeric delta).
- Focus visible — don't `outline: none` without a replacement focus style.
- Keyboard reachability — every interactive control reachable via `Tab`.

## Performance

In order of likely impact:

1. **Re-renders.** A parent re-render cascades to children. If a list with 200 rows is choppy, look for a parent state update that's firing on every keystroke.
2. **Chart re-creation.** Two-effect pattern above; don't recreate the chart on data change.
3. **Large fetches without pagination.** Don't pull 10 years of 1m bars at once. If the sidecar returns too much, that's a "needs pagination" task for `dev`/`architect`.
4. **Unmemoized derived data.** A `bars.map(toLightweightBar)` inside render that's not memoized re-allocates on every render. `useMemo([bars])` it.

Don't optimize without measuring. Run the dev tools profiler first; chasing perf without data is how bugs land.

## Common renderer mistakes (caught in code review)

- **`useEffect` with missing dependencies.** Use the lint rule; don't silence it without a comment explaining the omitted dep.
- **`useEffect` with `bars` as a dep that triggers a fetch.** That's a fetch loop — bars come *from* the fetch. Use React Query's `queryKey` instead.
- **`useState(null)` + later `setState(somePromise)`.** Promises don't go in state. Use React Query, or `await` the promise inside an `async` function and `setState(value)`.
- **`new Date(b.event_ts).getTime() / 1000`** scattered. Move to a helper.
- **Hardcoded `http://127.0.0.1:8000`.** The port is per-launch. Always go through the client.
- **`Authorization: Bearer ${import.meta.env.VITE_TOKEN}`.** No. The token comes from `window.api.getSidecarPort()`. There is no static token.
- **`window.api?.foo()`.** The `?.` is defensive coding for something that's always present in this app. If `window.api` is missing, the preload failed to load and the chart is the least of your problems. Throw or log loudly, don't silently degrade.
- **`process.env.NODE_ENV` in the renderer.** Use `import.meta.env.DEV` instead.

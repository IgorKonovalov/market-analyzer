# Adding a new IPC channel — checklist

A new IPC channel is a forever-decision: removing one later is a breaking change for any consumer. Before adding one, ask: **is this domain logic?** If yes, it belongs on the sidecar as an HTTP endpoint, not as an IPC channel. ADR-0008 §IPC discipline is the source of truth.

Valid IPC use cases (the ones already pinned in the bootstrap):
- App info / sidecar status (the shell's view of its own state)
- Sidecar port + bearer token forwarding
- OS integration: native file dialogs, default-browser opener, OS menus, window state
- Push events from main to renderer (sidecar restarted, app updating, etc.)

Not IPC: anything that reads market data, runs a strategy, fetches a backtest result, persists config, or talks to the database. Those are sidecar HTTP endpoints.

If you're sure you need one, walk this checklist. Skip nothing.

## 1. Constant in `desktop/shared/ipc-channels.ts`

```ts
export const IPC_CHANNELS = {
  // ...existing channels
  YOUR_DOMAIN_ACTION: 'your-domain:action',  // R→M  or  M→R
} as const
```

Naming: `<domain>:<action>` lowercase, kebab-case. Pick a domain that already has handlers if you can — `dialog`, `shell`, `sidecar`, `app`. A new domain means a new handler file and a new preload module.

## 2. Zod schema in `desktop/shared/schemas/<domain>.ts`

```ts
import { z } from 'zod'

export const YourActionRequestSchema = z.object({
  // narrow types — nothing too loose. The handler trusts this.
  someField: z.string().min(1).max(256),
  flags: z.array(z.enum(['a', 'b', 'c'])).optional(),
})
export type YourActionRequest = z.infer<typeof YourActionRequestSchema>

export const YourActionResponseSchema = z.object({
  result: z.string(),
})
export type YourActionResponse = z.infer<typeof YourActionResponseSchema>
```

Schemas exist even for "obviously" trusted shapes. Future-you adding a field will appreciate the contract.

## 3. Main-process handler in `desktop/electron/ipc/<domain>Handlers.ts`

```ts
import { ipcMain } from 'electron'
import { IPC_CHANNELS } from '@shared/ipc-channels'
import { YourActionRequestSchema } from '@shared/schemas/your-domain'

export function registerYourDomainHandlers() {
  ipcMain.handle(IPC_CHANNELS.YOUR_DOMAIN_ACTION, async (_event, payload: unknown) => {
    const req = YourActionRequestSchema.parse(payload)  // throw → invoke rejects in renderer
    // ...do the OS-level work
    return { result: '...' }
  })
}

export function cleanupYourDomainHandlers() {
  ipcMain.removeHandler(IPC_CHANNELS.YOUR_DOMAIN_ACTION)
}
```

The handler validates *before* any side effect. `parse` throws on invalid input; the throw becomes a promise rejection on the renderer side.

## 4. Wire it in `desktop/electron/ipc/index.ts`

```ts
import { registerYourDomainHandlers, cleanupYourDomainHandlers } from './yourDomainHandlers'

export function registerIpcHandlers() {
  registerAppHandlers()
  // ...
  registerYourDomainHandlers()  // <-
}

export function cleanupServices() {
  cleanupAppHandlers()
  // ...
  cleanupYourDomainHandlers()  // <- mirror, for clean shutdown
}
```

## 5. Preload binding in `desktop/electron/preload/api/<domain>.ts`

For **request-response** (R→M):

```ts
import { ipcRenderer } from 'electron'
import { IPC_CHANNELS } from '@shared/ipc-channels'
import type { YourActionRequest, YourActionResponse } from '@shared/schemas/your-domain'

export const yourDomain = {
  action: (req: YourActionRequest): Promise<YourActionResponse> =>
    ipcRenderer.invoke(IPC_CHANNELS.YOUR_DOMAIN_ACTION, req),
}
```

For **push events** (M→R) — the binding returns a cleanup function:

```ts
export const yourDomain = {
  onSomething(callback: (payload: SomethingPayload) => void): () => void {
    const handler = (_: unknown, raw: unknown) => callback(SomethingPayloadSchema.parse(raw))
    ipcRenderer.on(IPC_CHANNELS.YOUR_PUSH_CHANNEL, handler)
    return () => ipcRenderer.off(IPC_CHANNELS.YOUR_PUSH_CHANNEL, handler)
  },
}
```

The `return () => ipcRenderer.off(...)` is the contract that lets the component clean up on unmount.

## 6. Merge it into `window.api` in `desktop/electron/preload/index.ts`

```ts
import { contextBridge } from 'electron'
import { app } from './api/app'
import { sidecar } from './api/sidecar'
import { yourDomain } from './api/yourDomain'  // <-

const api = {
  app,
  sidecar,
  yourDomain,  // <-
}

contextBridge.exposeInMainWorld('api', api)

export type ElectronAPI = typeof api
```

`ElectronAPI` is the type the renderer's `declare global` block picks up. Adding a key here gives the renderer full type inference for `window.api.yourDomain.action(...)`.

## 7. Renderer usage

```tsx
const result = await window.api.yourDomain.action({ someField: 'hello' })
```

For push events:

```tsx
useEffect(() => {
  return window.api.yourDomain.onSomething((payload) => {
    setState(payload)
  })
}, [])
```

The `return` is mandatory — it's the cleanup that prevents listener accumulation.

## 8. Test it

- Main-process unit test in `desktop/electron/ipc/<domain>Handlers.test.ts` — invoke the handler with valid + invalid payloads, assert correct behavior + Zod error on invalid.
- If user-facing, a Playwright e2e in `desktop/tests/` that exercises the channel end-to-end through the UI.

## 9. Update the channel table in ADR-0008 if the channel is foundational

The initial channel set is documented in ADR-0008 §IPC discipline. If your channel is part of week-one or a core capability (not just an ad-hoc feature), propose the addition to the architect. Ad-hoc feature channels don't go in the ADR table.

---

**If this checklist feels heavy, it's by design.** Every IPC channel is a security and stability surface forever. The HTTP-to-sidecar path is the right answer for most "I need data" use cases — it has built-in validation (pydantic), built-in observability (OpenAPI), built-in testability (`curl`). Reach for IPC only when the renderer genuinely needs an OS capability the sidecar can't expose.

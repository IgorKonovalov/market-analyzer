<!-- GENERATED FILE - DO NOT EDIT BY HAND.
     Regenerate: uv run python -m market_analyser.apiref  (or: pnpm gen:api-docs)
     Rendered from the live sidecar; see Plan 0070 / ADR-0064. -->

# REST API

The 29 renderer-facing REST operations, from the FastAPI OpenAPI document. Every route is renderer-bearer gated by the central middleware except the auth-exempt `/healthz` liveness probe. Route handlers live under [`src/market_analyser/api/routes/`](../../src/market_analyser/api/routes).

| Route | Summary |
| --- | --- |
| [`GET /alerts`](#get-alerts) | List Alerts |
| [`GET /annotations`](#get-annotations) | Get Annotations |
| [`DELETE /annotations/{annotation_id}`](#delete-annotationsannotationid) | Delete Annotation |
| [`GET /backtests`](#get-backtests) | List Backtests |
| [`GET /backtests/{run_id}`](#get-backtestsrunid) | Get Backtest |
| [`POST /defi/pnl`](#post-defipnl) | Post Defi Pnl |
| [`GET /defi/position_alerts`](#get-defipositionalerts) | List Position Alerts |
| [`GET /defi/position_watches`](#get-defipositionwatches) | List Position Watches |
| [`POST /defi/scan`](#post-defiscan) | Post Defi Scan |
| [`GET /events`](#get-events) | Get Events |
| [`POST /events/ticket`](#post-eventsticket) | Mint Events Ticket |
| [`GET /healthz`](#get-healthz) | Healthz |
| [`GET /news`](#get-news) | Get News |
| [`GET /ohlcv`](#get-ohlcv) | Get Ohlcv |
| [`GET /quote`](#get-quote) | Get Quote |
| [`POST /scan_chart_patterns`](#post-scanchartpatterns) | Post Scan Chart Patterns |
| [`POST /scan_patterns`](#post-scanpatterns) | Post Scan Patterns |
| [`GET /search`](#get-search) | Search Symbols |
| [`GET /settings/mcp-secret`](#get-settingsmcp-secret) | Get Mcp Secret |
| [`POST /settings/mcp-secret/rotate`](#post-settingsmcp-secretrotate) | Post Rotate Mcp Secret |
| [`POST /settings/secret`](#post-settingssecret) | Post Set Secret |
| [`GET /settings/secrets`](#get-settingssecrets) | Get Secrets Status |
| [`POST /settings/stop`](#post-settingsstop) | Post Stop |
| [`GET /track_record`](#get-trackrecord) | Get Track Record |
| [`POST /ui_events`](#post-uievents) | Post Ui Event |
| [`PUT /user_drawings/{symbol}`](#put-userdrawingssymbol) | Put User Drawings |
| [`GET /watches`](#get-watches) | List Watches |
| [`DELETE /watches/{watch_id}`](#delete-watcheswatchid) | Delete Watch |
| [`POST /watches/{watch_id}`](#post-watcheswatchid) | Update Watch |

---

## `GET /alerts`

List Alerts

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer \| null | no | — |
| `offset` | integer | no | `0` |
| `limit` | integer | no | `50` |

**Response:** `AlertsPage`

## `GET /annotations`

Get Annotations

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | yes | — |
| `start` | string (date-time) | yes | — |
| `end` | string (date-time) | yes | — |

**Response:** `array[Annotation]`

## `DELETE /annotations/{annotation_id}`

Delete a single annotation by id. 404 when the id is unknown.

Renderer-bearer-gated by the central middleware in `app.py`; the MCP tenant
cannot reach it (same cross-tenant rule as the GET route).

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `annotation_id` | string | yes | — |

## `GET /backtests`

List Backtests

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string \| null | no | — |
| `strategy_id` | string \| null | no | — |
| `limit` | integer | no | `50` |

**Response:** `array[BacktestRunSummary]`

## `GET /backtests/{run_id}`

Get Backtest

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `run_id` | string | yes | — |

**Response:** `BacktestResult`

## `POST /defi/pnl`

Post Defi Pnl

**Auth:** renderer bearer

**Parameters**

No parameters.

**Request body:** `PnlRequest`
**Response:** `PnlResponse`

## `GET /defi/position_alerts`

List Position Alerts

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer \| null | no | — |
| `offset` | integer | no | `0` |
| `limit` | integer | no | `50` |

**Response:** `PositionAlertsPage`

## `GET /defi/position_watches`

List Position Watches

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `array[PositionWatchOut]`

## `POST /defi/scan`

Post Defi Scan

**Auth:** renderer bearer

**Parameters**

No parameters.

**Request body:** `ScanRequest`
**Response:** `ScanResponse`

## `GET /events`

Get Events

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `any`

## `POST /events/ticket`

Exchange the renderer bearer (checked by the central middleware) for a
short-TTL, single-use SSE ticket (ADR-0066). The renderer opens the stream
with `?ticket=<ticket>` and re-mints before every reconnect.

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `SseTicketResponse`

## `GET /healthz`

Healthz

**Auth:** none (liveness probe)

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `authorization` | string \| null | no | — |

**Response:** `object`

## `GET /news`

Get News

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string \| null | no | — |
| `window` | enum["1h", "4h", "24h", "7d"] | no | `"24h"` |
| `limit` | integer | no | `50` |

**Response:** `NewsResponse`

## `GET /ohlcv`

Get Ohlcv

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |
| `timeframe` | string | no | `"1d"` |
| `start` | string (date-time) | yes | — |
| `end` | string (date-time) | yes | — |

**Response:** `array[Bar]`

## `GET /quote`

Get Quote

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |

**Response:** `QuoteResponse`

## `POST /scan_chart_patterns`

Post Scan Chart Patterns

**Auth:** renderer bearer

**Parameters**

No parameters.

**Request body:** `ScanChartPatternsRequest`
**Response:** `ScanChartPatternsResponse`

## `POST /scan_patterns`

Post Scan Patterns

**Auth:** renderer bearer

**Parameters**

No parameters.

**Request body:** `ScanPatternsRequest`
**Response:** `ScanPatternsResponse`

## `GET /search`

Search Symbols

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `q` | string | no | `""` |

**Response:** `array[SymbolInfo]`

## `GET /settings/mcp-secret`

Get Mcp Secret

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `McpSecretRecord`

## `POST /settings/mcp-secret/rotate`

Post Rotate Mcp Secret

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `McpSecretRecord`

## `POST /settings/secret`

Set one key, then return the updated status map. The submitted value is
written server-side and deliberately not echoed back (ADR-0038 write-only).

**Auth:** renderer bearer

**Parameters**

No parameters.

**Request body:** `SetSecretRequest`
**Response:** `object`

## `GET /settings/secrets`

Presence/absence per known third-party API key. Never returns a value.

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `object`

## `POST /settings/stop`

Schedule a graceful sidecar shutdown after this response is sent.

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `any`

## `GET /track_record`

Get Track Record

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string \| null | no | — |
| `offset` | integer | no | `0` |
| `max_calls` | integer \| null | no | — |

**Response:** `GetTrackRecordResponse`

## `POST /ui_events`

Post Ui Event

**Auth:** renderer bearer

**Parameters**

No parameters.

**Request body:** `UIEventRequest`

## `PUT /user_drawings/{symbol}`

Declaratively replace `symbol`'s mirrored user drawing set. Returns the
stamped `synced_at` so the renderer can confirm the sync landed.

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `symbol` | string | yes | — |

**Request body:** `array[DrawingSpec]`
**Response:** `object`

## `GET /watches`

List Watches

**Auth:** renderer bearer

**Parameters**

No parameters.

**Response:** `array[WatchOut]`

## `DELETE /watches/{watch_id}`

Same cascade semantics as MCP `delete_watch`: the watch and its alert
history rows go together (`WatchesRepository.delete`).

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer | yes | — |

**Response:** `WatchDeleteResponse`

## `POST /watches/{watch_id}`

Update Watch

**Auth:** renderer bearer

**Parameters**

| Name | Type | Required | Default |
| --- | --- | --- | --- |
| `watch_id` | integer | yes | — |

**Request body:** `WatchUpdateRequest`
**Response:** `WatchOut`

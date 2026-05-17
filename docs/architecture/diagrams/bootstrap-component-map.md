# Bootstrap component map

Source of truth for the post-bootstrap component layout under [Plan 0001](../plans/0001-bootstrap.md). Update whenever a new component crosses a process or package boundary.

## Process and package boundaries

```mermaid
flowchart LR
    subgraph Shell[Electron shell - desktop/]
        Main[main process - Node]
        Preload[preload - contextBridge]
        Renderer[renderer - React/TS]
        Main --> Preload --> Renderer
    end

    subgraph Sidecar[Python sidecar - src/market_analyser/]
        API[FastAPI app - api/]
        Provider["MarketDataProvider Protocol<br/>data/provider.py"]
        Adapters[Adapters - data/adapters/]
        Persistence[(SQLite via repository - persistence/)]
        Config[(config.json)]
        Migrations[Alembic migrations]
        API --> Provider
        Provider --> Adapters
        Provider --> Persistence
        API --> Config
        Migrations -.applies on startup.-> Persistence
    end

    subgraph Vendored["Vendored from tradingview-mcp - per ADR-0003<br/>(lazy, one source per slice)"]
        YF[yahoo_finance_service]
        Screener[screener_service]
    end

    subgraph External[External sources]
        Yahoo[Yahoo Finance]
        TV[TradingView]
    end

    Main -. "spawns + supervises<br/>(passes --port, --secret)" .-> API
    Renderer -->|"HTTP 127.0.0.1<br/>Bearer per-launch secret"| API
    Adapters --> Vendored
    YF --> Yahoo
    Screener --> TV
```

Boundaries:

- **Shell** is the Electron app — its only domain responsibility is supervising the Python sidecar and rendering the UI. No business logic.
- **Sidecar** is the Python process. Owns the data layer, persistence, and (later) backtest and strategy execution. Single source of truth for all market-data answers.
- **Vendored** is mirrored from `../tradingview-mcp` per [ADR-0003](../adrs/0003-vendoring-strategy.md). Edited only via adapters, never in place.
- **External** is the network. Anything in here can return garbage, time out, or rate-limit; adapters validate at the seam.

The `MarketDataProvider` arrow is the only data-layer dependency `API` is allowed to take. Adapters are package-internal — callers never import them.

## Walking-skeleton sequence — OHLCV chart for one symbol

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant R as Renderer (React)
    participant M as Main (Node)
    participant S as Sidecar (FastAPI)
    participant P as MarketDataProvider
    participant Repo as Repository (SQLite)
    participant YA as Yahoo adapter
    participant YF as yahoo_finance_service (vendored)
    participant Yahoo as Yahoo Finance

    M->>S: spawn(--port, --secret)
    S->>Repo: migrate to head (Alembic)
    S-->>M: GET /healthz 200
    U->>R: open "AAPL 1d" view
    R->>S: GET /ohlcv?symbol=AAPL&timeframe=1d
    S->>P: get_ohlcv("AAPL","1d",as_of=None)
    P->>Repo: get_bars(...)
    alt cache hit and fresh
        Repo-->>P: list[Bar]
    else miss or stale
        P->>YA: fetch_ohlcv(...)
        YA->>YF: fetch(...)
        YF->>Yahoo: HTTPS
        Yahoo-->>YF: rows
        YF-->>YA: rows
        YA-->>P: list[Bar]
        P->>Repo: upsert_bars(...)
    end
    P-->>S: list[Bar]
    S-->>R: 200 JSON
    R-->>U: candlestick chart
```

Note the cache-hit/miss branch: this is where [ADR-0006](../adrs/0006-persistence-layout.md) and [ADR-0007](../adrs/0007-market-data-provider.md) intersect. The provider is the only code that decides "fetch fresh or serve from cache" — adapters never touch persistence directly.

## Initial SQLite schema (illustrative)

Final shape is owned by Alembic migrations under `src/market_analyser/persistence/migrations/`.

```mermaid
erDiagram
    BARS {
        string symbol PK
        string timeframe PK
        datetime event_ts PK
        float open
        float high
        float low
        float close
        float volume
        datetime ingested_at
        string source
    }
    STRATEGY {
        string id PK
        string name
        string version
        text source_path
        datetime created_at
    }
    BACKTEST_RUN {
        string id PK
        string strategy_id FK
        datetime started_at
        float sharpe
        float max_drawdown
        text params_json
    }
    TRADE {
        string id PK
        string run_id FK
        datetime entry_ts
        datetime exit_ts
        float pnl
    }
    BACKTEST_RUN ||--o{ TRADE : produces
    BACKTEST_RUN ||--|| STRATEGY : uses
```

The `BARS` table's `source` column records which adapter wrote the row — important for reproducibility and for debugging "why does this backtest say AAPL=$143 when Yahoo says $142".

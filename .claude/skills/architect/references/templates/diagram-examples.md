# Mermaid diagram patterns for market-analyser

Patterns reused across plans, ADRs, and the `diagrams/` directory. Use these as starting points — adapt the labels to the specific subsystem you're documenting.

## 1. High-level component map

Use for the "what is this whole thing?" diagram. Note the three subgraph boundaries: the desktop renderer, the sidecar (with its in-house data layer), and external services.

```mermaid
flowchart LR
    subgraph App[Desktop app]
        UI[Electron renderer]
        subgraph Sidecar[Python sidecar]
            API[FastAPI routes]
            subgraph DL[Data layer - in-house]
                Yahoo[yahoo adapter]
                Screener[tradingview screener adapter]
                Sentiment[sentiment adapter]
                Indicators[indicators]
                Backtest[backtest engine]
            end
            API --> DL
        end
        UI <-->|local IPC| API
    end

    subgraph External[External sources]
        TV[TradingView]
        YF[Yahoo Finance]
        Reddit[Reddit / RSS]
    end

    Screener --> TV
    Yahoo --> YF
    Sentiment --> Reddit
```

## 2. Sibling-skill handoff

Use in plans to show which skill owns which phase. Keep it minimal — the point is to make ownership obvious at a glance.

```mermaid
flowchart TD
    Plan[Plan NNNN] --> P1[Phase 1: schema]
    Plan --> P2[Phase 2: backtest engine]
    Plan --> P3[Phase 3: UI integration]

    P1 -.owned by.-> Architect[architect]
    P2 -.owned by.-> Backtester[backtester]
    P3 -.owned by.-> UIBuilder[ui-builder]
```

## 3. Sequence: UI → sidecar → data layer

Use when documenting interactions across the IPC boundary. Forces you to be explicit about which side of the boundary each step happens on.

```mermaid
sequenceDiagram
    participant U as User
    participant UI as UI shell
    participant SC as Python sidecar
    participant DL as Data layer (in-house)
    participant TV as TradingView

    U->>UI: Click "Run screener"
    UI->>SC: POST /screener {filters}
    SC->>DL: screener_service.scan(filters)
    DL->>TV: query
    TV-->>DL: rows
    DL-->>SC: list[ScreenerRow]
    SC-->>UI: 200 JSON
    UI-->>U: render table
```

## 4. State: backtest job lifecycle

Use anywhere there's an explicit lifecycle the user/UI cares about.

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running: worker picks up
    running --> done: success
    running --> failed: exception
    running --> cancelled: user cancels
    done --> [*]
    failed --> [*]
    cancelled --> [*]
```

## 5. Persisted data shape (ER)

Use for SQLite tables / parquet layouts when the relationships matter.

```mermaid
erDiagram
    BACKTEST_RUN ||--o{ TRADE : produces
    BACKTEST_RUN ||--|| STRATEGY : "uses"
    STRATEGY {
        string id PK
        string name
        text source
    }
    BACKTEST_RUN {
        string id PK
        string strategy_id FK
        datetime started_at
        float sharpe
        float max_drawdown
    }
    TRADE {
        string id PK
        string run_id FK
        datetime entry_ts
        datetime exit_ts
        float pnl
    }
```

## Style rules

- Prefer `flowchart LR` for component maps (left-to-right reads naturally for data flow).
- Use `subgraph` blocks to make boundaries explicit. Boundary names should be nouns (`Desktop app`, `External sources`), not verbs.
- Don't put more than ~12 nodes in one diagram. If you have more, split into separate diagrams.
- Avoid color/styling directives — they don't render consistently across editors, and add noise to diffs.

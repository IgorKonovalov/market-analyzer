# market-analyser

Desktop trading-analysis app: Electron shell + Python sidecar. See
[`docs/architecture/plans/0001-bootstrap.md`](docs/architecture/plans/0001-bootstrap.md)
for the walking-skeleton plan and [`docs/architecture/adrs/`](docs/architecture/adrs/)
for the architecture decisions.

## Run the sidecar (dev)

```
uv sync
uv run python -m market_analyser.api --port=0 --secret=test
```

The sidecar binds to `127.0.0.1` only and prints `PORT=<n>` on stdout once the
listening port is known. `GET /healthz` is auth-exempt; every other route
requires `Authorization: Bearer <secret>`.

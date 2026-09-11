# QQQ benchmark R2

QQQ is an isolated market-benchmark dataset for research consumers. It is not part of the stock universe, compliance state, rolling export, or ready-security counts.

## Canonical contract

- Pointer: `benchmarks/QQQ/current.json`
- Immutable Parquet: `benchmarks/QQQ/runs/<uuid>.parquet`
- Immutable run manifest: the pointer's `run_manifest_key`
- Identity: `benchmark:QQQ`
- Ticker: `QQQ`
- Columns: `date, security_id, ticker, open, high, low, close, adj_close, volume`
- Currency: USD
- Adjustment policy: `yahoo_auto_adjust_false_adj_close_separate_v1`

The updater is intentionally parallel to the existing SPY benchmark contract rather than sharing mutable stock-universe logic.

## Safeguards

The pipeline validates OHLCV/schema/identity, rejects duplicate dates, verifies published SHA-256 by readback, writes immutable versions first, and only then conditionally updates the canonical pointer.

Incremental refresh uses an overlap window. If the overlap shows a changed Adj Close/Close ratio or revised historical Close values, publication stops for explicit review rather than silently stitching incompatible history.

Yahoo incomplete placeholder rows are explicitly audited and discarded only when mandatory date/OHLC fields are incomplete. Valid-row loss or duplicate valid dates still stop publication.

## Bootstrap

Canonical initial bootstrap run: `34576132738` = SUCCESS.

Observed bootstrap evidence:

- mode: `bootstrap`
- source rows: 6,919
- valid rows: 6,919
- discarded incomplete rows: 0
- first date: 1999-03-10
- last date: 2026-09-10
- QC: passed
- no pre-existing ambiguous QQQ candidates were discovered before initialization

Initial immutable Parquet key:

`benchmarks/QQQ/runs/d8cb444dde2f4f12acdab0d7c87faba3.parquet`

Initial SHA-256:

`af1567feeacd8c655b02a919de82cd299e147f53622a0363e0020dde186da569`

## Schedule

Workflow: `.github/workflows/benchmark-qqq.yml`

Schedule: `04:00 UTC Tuesday-Saturday` (12:00 WITA), after the production OHLCV refresh and SPY benchmark refresh. GitHub scheduled workflows may start later than the nominal cron time.

A scheduled run does not weaken any data-quality safeguard. If a run fails, the previous pointer remains canonical until a valid replacement is published.

# Shared EMA state contract

`ussy-data` owns EMA as a shared derived market-data feature. It is not owned by TrendFoll or any downstream strategy.

## R2 layout

```text
production/
├── ready/
└── indicators/
    └── ema/
        ├── current.json
        └── runs/
            └── <uuid>.parquet
```

`production/ready/` remains the rolling OHLCV contract and is not changed by EMA publication.

## State schema

One row per current ready `security_id`:

- `security_id`
- `ticker`
- `as_of_date`
- `last_price`
- `ema20`
- `ema50`
- `ema150`
- `ema200`

Price basis is always `adj_close`. Periods are fixed at 20, 50, 150, and 200.

## Update semantics

Bootstrap uses full history from `backtest/ohlcv/<security_id>.parquet` and requires at least 200 bars. EMA uses the recursive definition with `alpha = 2 / (period + 1)` and the first available adjusted close as the initial value, equivalent to pandas `ewm(span=period, adjust=False)` over the same history.

After bootstrap, persisted EMA state is advanced only with ready bars newer than `as_of_date`:

`EMA_t = alpha * price_t + (1 - alpha) * EMA_(t-1)`

The publisher does not recompute EMA from the rolling 300-bar ready window. If persisted state is missing, outside the current ready window, has a price disagreement with the ready source, or otherwise cannot be trusted, that security is rebuilt from full history instead of continuing recursively. A corrupt EMA manifest/parquet fails closed.

## Manifest contract

`production/indicators/ema/current.json` schema version 1 includes at least:

- `schema_version`
- `created_at`
- `price_basis`
- `periods`
- `securities`
- `parquet_key`
- `sha256`
- `source_ready_parquet_key`
- `source_ready_sha256`
- `source_ready_created_at`
- `update_method`

It also carries `security_ids`, bootstrap/recursive/rebuild counters, and min/max state dates for auditability.

Publication order is immutable run first, verification second, pointer last. The ready pointer ETag is rechecked immediately before publishing the EMA pointer; if ready changes concurrently, the EMA immutable object is left unpointed and the run fails safely.

## Loader contract

Use `src/load_ema_state.py`. The loader verifies schema version, periods, price basis, immutable-key prefix, SHA256, security count, security IDs when present, and state-frame uniqueness/validity.

## Equivalence gate

`src/verify_ema_equivalence.py` recalculates EMA from full history and compares persisted values with tight numerical tolerances. On a bootstrap/rebuild run, production verifies the full EMA universe. On ordinary recursive runs it verifies a deterministic sample. Trend classification uses three states for equivalence checking only: `STACKED_UP`, `STACKED_DOWN`, and `MIXED`; classification mismatch is a hard failure.

This classification is a QA device, not a trading signal contract.

## Downstream use

CAN SLIM, SEPA, TrendFoll, signal-model, and other consumers may load this shared state. No downstream project should mutate the EMA pointer or assume a specific immutable UUID. Read `production/indicators/ema/current.json` and validate through the loader.

SMA remains intentionally non-persistent because it can be calculated exactly from the rolling ready dataset.

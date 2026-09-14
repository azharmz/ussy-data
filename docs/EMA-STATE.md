# Shared EMA state contract

`ussy-data` owns EMA as a shared derived market-data feature. It is not owned by TrendFoll or any downstream strategy.

## R2 layout

```text
validation/
└── indicators/
    └── ema/
        ├── run-<github_run_id>-<attempt>.parquet
        └── run-<github_run_id>-<attempt>.json

production/
├── ready/
└── indicators/
    └── ema/
        ├── current.json
        └── runs/
            └── run-<github_run_id>-<attempt>.parquet
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

The publisher does not recompute EMA from the rolling 300-bar ready window. If persisted state is missing, outside the current ready window, has a price disagreement with the ready source, or otherwise cannot be trusted, that security is rebuilt from full history instead of continuing recursively. A corrupt or pre-governance EMA manifest is never used as recursive state.

## Frozen promotion governance

The production contract is governed by this exact order:

```text
full history + ready
        ↓
compute candidate EMA state
        ↓
write validation/indicators/ema candidate
        ↓
equivalence validation vs long-history reference
        ↓ PASS
write immutable production/indicators/ema/runs artifact
        ↓
promote production/indicators/ema/current.json LAST
```

A failed candidate never becomes production-approved. `production/indicators/ema/current.json` must remain unchanged if equivalence validation fails, lineage changes while validation is running, immutable production upload verification fails, or any pre-promotion guardrail fails.

Candidate artifacts belong under `validation/indicators/ema/` and are explicitly non-production. Downstream consumers must never read candidate artifacts.

The immutable production object is written only after equivalence PASS. The production pointer is the final write because changing `current.json` is the semantic act of approving a state for downstream consumption.

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
- `promotion_policy`
- `equivalence`

It also carries `security_ids`, bootstrap/recursive/rebuild counters, min/max state dates, and candidate lineage for auditability.

## Loader contract

Use `src/load_ema_state.py`. The loader verifies schema version, periods, price basis, immutable-key prefix, SHA256, security count, security IDs when present, and state-frame uniqueness/validity. It also requires the frozen promotion policy plus equivalence evidence with zero numeric failures and zero classification mismatches. This deliberately fences off any pointer produced before candidate-first governance was installed.

## Equivalence gate

`src/verify_ema_equivalence.py` validates the candidate before any production approval. It recalculates EMA from full history and compares candidate values with tight numerical tolerances. On a bootstrap/rebuild run, validation covers the full EMA universe. On ordinary recursive runs it may use a deterministic sample. Trend classification uses three states for equivalence checking only: `STACKED_UP`, `STACKED_DOWN`, and `MIXED`; classification mismatch is a hard failure.

The first full bootstrap validation on 14 September 2026 covered 1,226 securities and produced max absolute error 0.0, max relative error 0.0, zero classification mismatches, and zero numeric failures. That run used the earlier publish-before-validate ordering, so its numerical result is evidence for the EMA formula but not evidence that the old promotion sequence satisfies this frozen governance.

This classification is a QA device, not a trading signal contract.

## Workflow roles

- `ema-state-smoke.yml` uses existing R2 history/ready and must not run Yahoo. It performs unit tests, candidate generation, the equivalence gate, immutable production upload, and pointer-last promotion.
- `production-daily.yml` remains the normal EOD pipeline. EMA integration may be re-enabled only after the smoke workflow passes this end-to-end sequence.

## Downstream use

CAN SLIM, SEPA, TrendFoll, signal-model, and other consumers may load the shared production EMA state once the promotion gate is production-enabled. No downstream project should mutate the EMA pointer or assume a specific immutable run key. Read `production/indicators/ema/current.json` and validate through the loader.

SMA remains intentionally non-persistent because it can be calculated exactly from the rolling ready dataset.

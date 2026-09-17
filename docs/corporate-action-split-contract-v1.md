# Corporate-Action Split Facts Contract v1

Status: **FROZEN DESIGN / NOT YET PRODUCTION-MATERIALIZED**

Owner: `azharmz/ussy-data`

Consumers: opt-in only. Initial requested consumer is `azharmz/ussy-trendfoll` FSE-014/FSE-016.

## Contract boundary

This contract stores authoritative **effective stock-split facts**. It does not change canonical raw OHLCV and does not define any strategy indicator, threshold, or trading decision.

## Source policy

Primary source: Tiingo EOD field `splitFactor` from `/tiingo/daily/<provider_ticker>/prices`.

An event exists when a returned EOD row has a finite positive `splitFactor != 1.0`.

Factor convention:

`split_factor = post_split_shares / pre_split_shares`

Examples: 10-for-1 = `10.0`; 1-for-10 reverse split = `0.1`.

Yahoo `Stock Splits` may be retained as independent validation evidence but must not silently overwrite Tiingo facts. Conflicts fail validation and require review.

Dedicated Tiingo Corporate Actions endpoint is not required by v1 because current entitlement returned HTTP 403 during feasibility.

## Identity policy

`security_id` is the primary key identity. `provider_ticker` is evidence/alias only.

A source ticker may be attached to a security only through canonical `ussy-data` identity/alias evidence. Missing or ambiguous mapping fails closed. Ticker alone never creates or merges a security identity.

## Event schema

Required fields:

- `security_id: string`
- `effective_date: date`
- `split_factor: float64` (finite, >0, !=1)
- `provider_ticker: string`
- `source_provider: string` = `tiingo_eod`
- `source_field: string` = `splitFactor`
- `retrieved_at: timestamp UTC`
- `source_as_of_date: date`
- `schema_version: string` = `corporate-action-split-v1`

Logical uniqueness: `(security_id, effective_date)`.

Duplicate same-key/same-factor evidence may deduplicate. Same-key/different-factor evidence is a hard conflict and must not publish.

## Temporal semantics

`effective_date` is the market-effective split date represented by the EOD row. It is **not** an announcement timestamp.

For any downstream as-of date `T`, only events satisfying `effective_date <= T` may participate in a derived normalization. This contract does not authorize use of future effective events.

No claim is made that v1 reconstructs what announcement information was known before the effective date.

## Proposed additive R2 namespace

No object is to be written until implementation validation passes.

Planned namespace:

- `corporate_actions/splits/current.json`
- `corporate_actions/splits/runs/<run_id>/events.parquet`
- `corporate_actions/splits/runs/<run_id>/manifest.json`

Runs are immutable. `current.json` is written LAST after validation.

## Manifest minimum

- schema/contract version
- producer commit SHA and Actions run ID
- created/retrieved timestamp
- source provider + endpoint family
- universe/identity snapshot used
- event row count
- distinct security count
- min/max effective date
- unresolved/ambiguous identity counts
- validation case summary
- content SHA256 + bytes for events parquet
- source cutoff/as-of metadata

## Derived normalization boundary

The fact contract may support a separately versioned derived factor layer, but v1 event facts remain independently readable and auditable.

For a row date `d` evaluated as-of `T`, a consumer/derived layer can form the cumulative factor from split events with `d < effective_date <= T`.

That cumulative factor can support:

- split-basis share-volume normalization (multiply historical share counts by the cumulative split factor);
- split-basis raw-price normalization (divide historical raw OHLC by the cumulative split factor).

These arithmetic transformations are data normalization only. TrendFoll remains responsible for how normalized inputs feed liquidity, ATR, tightness, or tradability.

## Required validation gates before production

- known forward splits: NVDA 2024-06-10 10:1, AVGO 2024-07-15 10:1;
- at least two additional forward splits;
- at least two reverse splits;
- identity mapping uniqueness;
- ticker-change/alias case where available;
- future-event exclusion (`effective_date > T` cannot affect T);
- factor arithmetic tests for price and volume basis;
- duplicate/conflicting-event fail-closed tests;
- source HTTP/empty/malformed-response fail-closed tests;
- immutable-run + pointer-last publication tests;
- no writes to canonical OHLCV/READY/EMA namespaces.

Only after these gates pass may a bounded pilot be published. Full-universe historical materialization is a later decision based on pilot coverage and unresolved-identity evidence.

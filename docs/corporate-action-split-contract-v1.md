# Corporate-Action Split Facts Contract v1

Status: **FROZEN DESIGN / VALIDATION IN PROGRESS / NOT YET PRODUCTION-MATERIALIZED**

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

### Tiingo request budget

The currently configured Starter entitlement is governed as **maximum 50 requests/hour**. Corporate-action development/pilot workflows must declare and enforce a bounded request budget below that ceiling. Do not use full-universe one-request-per-symbol scans under this entitlement. Prefer cached/checkpointed evidence and narrowly scoped batches.

## Identity policy

`security_id` is the primary key identity. `provider_ticker` is evidence/alias only.

A source ticker may be attached to a security only through canonical `ussy-data` identity/alias evidence. Missing or ambiguous mapping fails closed. Ticker alone never creates or merges a security identity.

Historical aliases are explicitly provider-scoped and date-bounded. Resolution requires exactly one reviewed alias whose validity interval contains the corporate-action effective date. Unknown aliases, out-of-range aliases, and overlapping aliases that map the same provider ticker to multiple securities fail closed.

The first frozen lifecycle case is `EVTV -> AZIO` for canonical security `US29414V3087`. Company/Nasdaq evidence states that AZIO began trading at market open on 2026-07-13 and that the ticker change did not affect the capital structure, CUSIP, or securityholder rights. Therefore the reviewed Tiingo alias intervals are EVTV through 2026-07-12 and AZIO from 2026-07-13. This validates a ticker-change lifecycle without changing canonical `security_id`.

A split can itself change security identifiers. GE's 2021 1-for-8 reverse split is a separate boundary case: the company stated that split-adjusted trading began 2021-08-02 with a new ISIN. Therefore current-ISIN lookup alone is not sufficient proof of historical identity continuity. Cross-identifier continuity remains evidence-driven; without reviewed lineage, the event remains unresolved rather than guessed.

Implementation: `src/corporate_action_identity.py`. The reviewed registry is intentionally small and additive; it is not a heuristic ticker-history database.

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
- Tiingo request budget and actual request count

## Derived normalization boundary

The fact contract may support a separately versioned derived factor layer, but v1 event facts remain independently readable and auditable.

For a row date `d` evaluated as-of `T`, a consumer/derived layer can form the cumulative factor from split events with `d < effective_date <= T`.

That cumulative factor can support:

- split-basis share-volume normalization (multiply historical share counts by the cumulative split factor);
- split-basis raw-price normalization (divide historical raw OHLC by the cumulative split factor).

These arithmetic transformations are data normalization only. TrendFoll remains responsible for how normalized inputs feed liquidity, ATR, tightness, or tradability.

## Validation evidence

Deterministic contract validation:

- Actions run `35237199105`: PASS.
- forward/reverse arithmetic: PASS.
- future-event exclusion: PASS.
- identity fail-closed: PASS.
- duplicate/conflict handling: PASS.
- malformed-source handling: PASS.
- pointer-last model: PASS.
- canonical OHLCV writes: zero.

Live reverse-split source validation:

- Actions run `35241593520`: PASS, read-only, exactly **2 Tiingo requests**, zero R2 writes.
- GE: Tiingo EOD `2021-08-02 splitFactor=0.125` (1-for-8): PASS.
- AIG: Tiingo EOD `2009-07-01 splitFactor=0.05` (1-for-20): PASS.

Together with prior live forward cases NVDA, AVGO, WMT, and CMG, provider factor direction is now evidenced for both forward and reverse splits.

Historical identity lifecycle validation:

- EVTV -> AZIO, canonical `US29414V3087`: reviewed date-bounded lifecycle frozen.
- unknown/out-of-range alias: fail closed.
- overlapping reused ticker across different securities: registry validation fails closed.
- non-overlapping ticker reuse can resolve deterministically by event date.
- validation uses zero Tiingo requests and zero R2 writes.

## Remaining gates before bounded publication pilot

- idempotent rerun/provider-correction lineage test;
- immutable-run + pointer-last test against a non-production fake/test namespace or equivalent isolated store;
- prove no writes to canonical OHLCV/READY/EMA namespaces.

Only after these gates pass may a bounded pilot be published. Full-universe historical materialization is a later decision based on pilot coverage and unresolved-identity evidence, and must respect Tiingo's request ceiling.

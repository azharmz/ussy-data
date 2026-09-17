# Corporate-Action / Stock-Split Feasibility — FSE-014 + FSE-016

Status: **GO FOR CONTRACT DESIGN / NO PRODUCTION INGESTION YET**

Date: 2026-09-17

Consumer: `azharmz/ussy-trendfoll`

## Scope

Investigate whether `ussy-data` can own authoritative split facts needed by TrendFoll FSE-014 (split-sensitive share-volume liquidity) and FSE-016 (split-sensitive raw-OHLC ATR), without changing canonical raw OHLCV.

## Evidence

Read-only probe runs:

- `35235886650` — provider semantics / known split cases
- `35236136930` — provider semantics + canonical identity mapping

No R2 writes were performed.

Known cases tested:

| Ticker | Effective date | Expected | Tiingo EOD `splitFactor` | Yahoo action | Current canonical identity |
|---|---:|---:|---:|---:|---|
| NVDA | 2024-06-10 | 10:1 | PASS | PASS | `US67066G1040` |
| AVGO | 2024-07-15 | 10:1 | PASS | PASS | `US11135F1012` |
| WMT | 2024-02-26 | 3:1 | PASS | PASS | not in current 2026-08-28 universe |
| CMG | 2024-06-26 | 50:1 | PASS | PASS | not in current 2026-08-28 universe |

Tiingo EOD endpoint was HTTP 200 and matched all four known events exactly. Yahoo `Stock Splits` independently matched all four.

The dedicated Tiingo Corporate Actions Splits endpoint returned HTTP 403 under the currently configured entitlement. Therefore it is **not** a production dependency for this contract. In particular, Tiingo `permaTicker` is not currently available to us through that endpoint.

## Existing data

Canonical `ussy-data` raw OHLCV currently stores raw OHLC/share volume plus `adj_close`; it does not have a governed split-event contract. `adj_close` alone is not treated as authoritative corporate-action facts.

## Source verdict

Primary candidate: **Tiingo EOD `splitFactor`**, queried through the already available EOD entitlement.

Required semantics:

- event/effective date = EOD row date on which `splitFactor != 1.0`;
- factor convention = post-split shares / pre-split shares (`splitTo / splitFrom` semantics);
- only actual effective events from EOD are materialized;
- Yahoo split actions may be used as independent audit evidence, not as silent heuristic synthesis.

Dedicated Corporate Actions endpoint remains optional/future evidence only while entitlement is unavailable.

## Temporal / PIT semantics

For FSE-014 and FSE-016 the required fact is the **effective split event**, not advance announcement knowledge. A consumer must never apply an event whose effective date is after its signal/as-of date.

Thus historical normalization for a signal at date `T` may use only split events with `effective_date <= T`. Future splits must not alter the representation used at `T`.

This is narrower than claiming announcement-date PIT knowledge. We do **not** claim historical announcement-time availability from the EOD endpoint.

Provider corrections to already-effective historical facts are treated like corrected historical market data and must carry retrieval/provenance metadata.

## Identity

Canonical owner remains `security_id`/ISIN. Provider ticker is only an alias used to retrieve evidence.

For current-universe examples NVDA and AVGO, ticker -> canonical `security_id` mapping was unique. This does not prove all historical ticker lifecycles. Production ingestion must resolve a provider alias to exactly one canonical security identity and fail closed on missing/ambiguous mapping. It must not create identity from ticker alone.

Ticker-change/reuse cases require identity-scoped alias evidence already governed by `ussy-data`; unresolved historical aliases remain not-evaluable rather than guessed.

## Shared contract feasibility

One split-fact contract can serve both findings because both transformations require the same event date and split factor:

- FSE-014 consumer can place historical share volume on a consistent share basis;
- FSE-016 consumer can remove mechanical split discontinuities from raw OHLC before True Range/ATR.

The upstream contract supplies facts/factors only. TrendFoll remains owner of liquidity thresholds, ATR, tightness, and tradability semantics.

## Minimal proposed fact schema

One row per effective split event:

- `security_id`
- `effective_date`
- `split_factor`
- `provider_ticker`
- `source_provider` = `tiingo_eod`
- `source_field` = `splitFactor`
- `retrieved_at`
- `source_as_of_date` or retrieval cutoff
- `schema_version`

Recommended lineage/manifest additionally records producer commit/run, universe snapshot/identity mapping source, row count, date range, content SHA256, source endpoint family, and validation summary.

Raw OHLCV remains unchanged.

## Validation required before production materialization

1. Freeze schema and factor convention.
2. Unit-test forward split and reverse split arithmetic.
3. Validate multiple known forward and reverse split cases.
4. Validate ticker-change/alias case(s) where available.
5. Validate that an event after as-of `T` cannot affect normalization at `T`.
6. Validate idempotent reruns and provider corrections with immutable lineage.
7. Fail closed on missing/ambiguous canonical identity.
8. Only after these pass, consider bounded/full-universe backfill.

## Risk to existing consumers

Low if implemented as an additive namespace. Do not overwrite `history/ohlcv/{security_id}.parquet`, READY, rolling, or EMA contracts. Existing consumers must remain unchanged until they explicitly adopt the new contract.

## Feasibility answers

1. Existing governed corporate-action facts? **No.**
2. Existing provider capable? **Yes: Tiingo EOD `splitFactor`; Yahoo actions as audit evidence.**
3. Exact semantics? **Effective-date split factor on EOD row; factor is post/pre share ratio.**
4. Historical coverage? **Provider advertises long EOD history and all four known historical cases passed; full-universe coverage not yet materialized/claimed.**
5. Deterministic identity? **Feasible through canonical `security_id` + governed alias resolution; must fail closed where unresolved.**
6. PIT defensible? **Yes for effective-date-only use with `effective_date <= as_of`; no claim of announcement-date PIT.**
7. One shared contract for FSE-014/FSE-016? **Yes.**
8. Minimal storage/provenance? **Event rows + immutable manifest/lineage; additive namespace.**
9. Existing OHLCV/READY risk? **Low if additive; silent rewrite prohibited.**
10. Implementation verdict? **GO FOR FROZEN CONTRACT DESIGN; full-universe ingestion remains NOT YET VALIDATED.**

## Governance

Do not infer split events from price gaps. Do not interpret zero/missing split fields as proof without source semantics. Do not silently replace raw OHLCV with adjusted data. Do not change TrendFoll thresholds or strategy logic in this repository.

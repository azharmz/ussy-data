# ussy-data

Production data infrastructure for the USSY research/trading stack.

This repository is the source of truth for the US equity universe/security identity, canonical OHLCV history, finalized daily/rolling datasets, READY production contract, shared EMA state, R2 storage/audit tooling, and downstream production handoff.

> **Continuation rule:** a new maintainer/chat should inspect this README, the current `main` branch, and the latest GitHub Actions runs before changing anything. Repository/runtime evidence outranks historical chat handoffs. Do not redesign from memory.

## 1. Production architecture

Primary architecture:

`GitHub Actions -> Cloudflare R2`

Primary EOD OHLCV provider is Yahoo/yfinance. Tiingo is available for independent audit/repair evidence and Twelve Data is a fallback/patch candidate.

Canonical analytical price is `adj_close`.

Security identity is canonical. `security_id`/ISIN owns history; ticker is a provider/listing alias and may change without creating a new security identity.

Canonical historical OHLCV:

`history/ohlcv/{security_id}.parquet`

The old production namespace `backtest/ohlcv/` has been migrated out of active production. Do not reintroduce it into production readers/writers. Historical audit evidence may legitimately contain old paths.

## 2. Production flow

The intended production lineage is:

`universe -> bootstrap missing OHLCV -> finalized OHLCV update -> daily/rolling -> readiness -> READY -> EMA candidate -> equivalence -> EMA production -> audit/web`

The main workflow is checkpointed as:

`preflight -> ohlcv -> ready -> ema -> downstream`

Workflow: `.github/workflows/production-daily.yml`

EMA-only recovery is available in `.github/workflows/production-ema-resume.yml`. It is intended to resume from authoritative READY without repeating Yahoo/bootstrap/OHLCV work after an EMA-specific code failure.

Both production-daily and EMA-resume must use the same concurrency group:

`production-daily`

with `cancel-in-progress: false`, so production mutations are serialized. A regression contract test protects this configuration.

## 3. US daily-bar finalization

A previous incident demonstrated that Yahoo can expose a same-day daily bar while the US session is still open. Such a bar is partial and must never be promoted as finalized EOD data.

The shared policy lives in `src/us_market_finalization.py`:

- regular US close: 16:00 America/New_York
- safety buffer: 90 minutes
- current-day daily bar becomes eligible at/after approximately 17:30 ET
- before that cutoff, current-day rows are rejected

This is deliberately a **regular-close + 90-minute safety-buffer policy**, not a full exchange-calendar implementation. Early closes are handled conservatively.

Prevention of future partial-bar ingestion and repair of previously persisted partial bars are separate concerns. Never claim historical partial bars are repaired without specific audit/repair evidence.

## 4. READY production contract

Authoritative pointer:

`production/ready/current.json`

READY is restricted to active/compliant securities that satisfy readiness requirements. Missing/insufficient/lifecycle-failed securities must not silently become READY.

READY records terminal-date evidence including `as_of_date`, terminal min/max, histogram, and as-of security count. A small newer partial leading edge must not define global T0.

### One canonical READY snapshot per finalized trading day

Publication is fail-closed and governed by `src/ready_snapshot_guard.py` and `src/export_ready.py`:

- same `as_of_date` + same content SHA256 -> **REUSE / no-op**
- same `as_of_date` + different SHA256 -> **FAIL CLOSED**
- older `as_of_date` than current -> **FAIL CLOSED**
- newer finalized trading date -> create one new canonical snapshot

New canonical keys use:

`production/ready/runs/YYYY-MM-DD.parquet`

Historical UUID-keyed READY snapshots remain valid lineage and should not be renamed/deleted merely for naming consistency.

Do not weaken a same-day SHA conflict guard merely to make CI green; first investigate why supposedly canonical same-day data changed.

## 5. Shared EMA production contract

Persisted shared EMA periods:

`EMA20, EMA50, EMA150, EMA200`

Recursive definition uses `alpha = 2/(period+1)` with pandas `ewm(adjust=False)` semantics.

Trend classifications include `STACKED_UP`, `STACKED_DOWN`, and `MIXED`.

Flow:

`canonical history + authoritative READY cutoff -> candidate -> equivalence -> immutable EMA production run -> current pointer LAST`

Candidate namespace:

`validation/indicators/ema/`

Production runs:

`production/indicators/ema/runs/`

Pointer:

`production/indicators/ema/current.json`

Promotion policy is `candidate_then_equivalence_then_immutable_run_then_current_pointer_last_v1`.

Equivalence reference history must be truncated through the authoritative READY/source cutoff. Do not compare a READY-cutoff candidate against a later full-history terminal date.

Known legacy bugs already fixed: EMA readers using `backtest/ohlcv/`, old READY schema assumptions, and equivalence against post-READY history. Do not reopen these without new evidence.

## 6. New-member onboarding

`src/bootstrap_missing_ohlcv.py` bootstraps eligible securities without canonical history before the daily updater. It uses bounded Yahoo `period=max` retrieval, QC, pacing, and does not overwrite existing history.

Missing history is `data_unavailable`/not READY until successfully onboarded.

## 7. Security/ticker lifecycle

Provider failures must be classified by security identity/lifecycle rather than blindly treated as data-provider errors.

Desired classification:

`Yahoo failure -> ticker_changed | acquired/delisted | halted | genuine_data_failure`

Known example: EVTV changed provider ticker to AZIO while retaining the same canonical security identity; the alias is identity-scoped.

Do not map an acquired/delisted/halted security to its acquirer or successor as though it were the same security.

## 8. R2 and dashboard

Storage guard: `src/r2_storage_guard.py` (warning around 7 GiB, hard stop around 9 GiB).

The web status/dashboard is published from `web/` and `src/publish_web_status.py`; live deployment is the `ussy-data.pages.dev` site.

Review/investigation states include `POTENTIALLY_STALE`, `UNKNOWN`, `DATA_UNAVAILABLE`, `INSUFFICIENT_HISTORY`, and `DAILY_UPDATE_FAILED`.

Large objects owned by other projects must not be deleted from this repository's cleanup work without ownership evidence. In particular, `institutional_sponsorship` is owned by `ussy-fundamentals`.

CAN SLIM candidate snapshots under `canslim/candidates/snapshots/` are a separate contract from canonical READY. Do not conflate their retention/cadence with READY governance.

## 9. Verified production baseline

Production Daily OHLCV **#54**, run `35181965379`, commit `c481348f31420060cbb347f54fbe4ffe5cc87fce`, is the verified recovery baseline after the READY/EMA/finalization incidents.

Evidence from that run:

- complete pipeline `preflight -> ohlcv -> ready -> ema -> downstream`: SUCCESS
- finalized daily date: `2026-09-16`
- READY `as_of_date`: `2026-09-16`
- EMA consumed the exact READY key/SHA lineage
- EMA equivalence: zero numeric failures, zero classification mismatches, max error 0.0
- EMA production promotion and downstream audit/web: SUCCESS

The former TrendFoll upstream blocker caused by incoherent/failed READY+EMA is therefore **CLOSED / VERIFIED** as of this baseline.

This section is a historical verified baseline, **not a current-status dashboard**. Always inspect the latest Actions run for current production state.

## 10. Known architectural caveat

READY and EMA do not yet share a single atomic generation pointer. READY may be promoted before EMA finishes. Current workflow/lineage validation is fail-closed, but a full atomic READY+EMA generation commit is **DEFERRED / NOT IMPLEMENTED**.

Do not silently redesign this contract during unrelated fixes.

## 11. GitHub Actions compute governance

When an automatic push trigger exists, treat:

**COMMIT = POTENTIAL COMPUTE EXECUTION**

Required discipline:

`INSPECT -> PLAN COMPLETE PATCH -> HARD-CODE/PATH/CONFIG AUDIT -> PREPARE -> VERIFY EXPERIMENT-READY -> ONE MEANINGFUL COMMIT -> ONE INTENTIONAL RUN -> MONITOR -> EVALUATE`

Aim for one meaningful experiment-ready commit per intentional Actions run. Avoid tiny formatting/logging/cleanup commits that each spend production compute.

Before committing, check whether the change will trigger compute, whether compute is needed, whether the patch is evidence-ready, whether known related cleanup should be batched, and whether an unnecessary rerun can be avoided.

Audit accidental hardcodes such as ticker/security IDs, R2 keys, legacy storage prefixes, arbitrary dates/sample sizes, and environment-specific paths. Do not remove intentional frozen constants or production schema semantics.

If a workflow is queued/in-progress, monitor it to terminal. If it fails in scope, diagnose and patch the failed stage. Prefer checkpoint/resume over repeating successful expensive stages.

## 12. Status language and maintenance rules

Use explicit states: `IMPLEMENTED`, `RUNNING`, `VERIFIED`, `FAILED`, `BLOCKED`, `CLOSED`, `NOT YET VALIDATED`.

Never claim a write, deletion, promotion, repair, or workflow success without repository/R2/Actions evidence.

Closed items should not be reopened without new evidence. Current closed items include canonical history namespace migration, EMA legacy-namespace bug, EMA READY-cutoff equivalence bug, and the TrendFoll upstream blocker verified by Production #54.

The shared close+90m policy closes the **future partial-bar prevention** issue; it does not by itself prove historical partial-bar remediation.

## 13. How to continue this repository in a new chat/session

Start with:

1. Read this README.
2. Inspect `main` and recent commits/PRs.
3. Inspect the latest `Production daily OHLCV` Actions run and any active EMA-resume run.
4. Compare actual state against the contracts above.
5. Continue the latest unfinished work; do not infer current state from an old chat handoff.

A sufficient handoff prompt should normally be:

> **Continue `azharmz/ussy-data` as Repository HQ. Read the repo/README and inspect the latest Actions state first, then continue the unfinished work under the documented governance.**

If README and live repository state disagree, live repository evidence wins and README should be corrected as part of the same logically complete documentation patch.

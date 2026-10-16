# CCTG 2026-09-22 isolated history repair runbook

Status: **PREPARED — production write not executed**

## Frozen incident

- Canonical identity: `KYG1993R1184` (ticker `CCTG`)
- Target date: `2026-09-22`
- Canonical object: `history/ohlcv/KYG1993R1184.parquet`
- Verified recovery source: `recovery/stooq-2026-09-25/history/ohlcv/KYG1993R1184.parquet`
- Expected canonical `adj_close` before repair: `0.5180000066757202`
- Verified Stooq / current immutable READY `adj_close`: `0.5184`
- Intended row-level change: `adj_close` only. Preserve every other column, row, and date.

## Evidence already verified

- The isolated bundle workflow is manual-only and writes only an artifact; it does not upload candidate objects to R2.
- The candidate changes only CCTG's adjusted close on the target date.
- Rebuilt CCTG EMA20/50/150/200 match the persisted EMA state under the official `rtol=1e-8, atol=1e-8` gate.
- The corrected-history EMA row does not differ from the persisted CCTG EMA row. Therefore, if the source and pointers have not changed, this incident does not justify a shared EMA-state promotion.
- The current immutable READY snapshot already contains `adj_close=0.5184` for CCTG on the target date. Do not rewrite READY or weaken its same-day immutability guard.
- The preventive overlap-overwrite fix is merged in `ussy-data` main at `2141f6180a2ac2a4730b1fe4277f8a6a2a4a59fb`.

## Required controlled execution

1. Run the existing `Prepare isolated CCTG repair bundle` workflow from branch `repair/cctg-ema-isolated-bundle`. Confirm its terminal result is successful and download artifact `cctg-repair-bundle`.
2. Before any canonical write, re-read and verify:
   - READY current pointer and the READY Parquet checksum still match the bundle manifest;
   - EMA current pointer and manifest still match the bundle manifest;
   - canonical history SHA256 still equals `canonical_history_sha256_before`;
   - frozen recovery source SHA256 still equals `recovery_history_sha256`;
   - candidate SHA256 equals `corrected_history_sha256`;
   - exact target row/value and row-level diff remain unchanged from the reviewed bundle.
3. Write the **raw canonical object bytes** to a durable, unique pre-repair backup key first. Read the backup back and require its SHA256 to equal the original canonical SHA256. If backup already exists, verify its SHA256; never overwrite a mismatching backup.
4. Only after backup verification, replace the single canonical object `history/ohlcv/KYG1993R1184.parquet` with the reviewed candidate. Do not run the broad `repair-sep22-stooq.yml` workflow in APPLY mode; that workflow covers a 1,061-security population.
5. Read the canonical object back and verify its SHA256, the exact `2026-09-22` value `adj_close=0.5184`, and that no other field/row changed.
6. Recompute CCTG EMA20/50/150/200 through the authoritative READY cutoff and verify against persisted EMA with `np.isclose(actual, expected, rtol=1e-8, atol=1e-8)`. If any EMA fails, stop; do not promote a pointer or dispatch downstream.
7. Run the official TrendFoll PROB-019 audit on the deterministic 100-security sample, using its newly recorded membership/fingerprint and official tolerance. The audit must finish with zero failures.
8. Keep downstream dispatch disabled throughout. Do not write READY, EMA production state/current pointer, dashboard, or downstream completion markers for this isolated repair. Only reconsider those actions if fresh evidence proves they are required and the owning contracts explicitly permit them.

## Stop / rollback rules

- Any changed READY/EMA pointer, changed canonical SHA, changed recovery SHA, missing/incorrect backup, row-level diff outside the target adjusted close, or failed tolerance check is a fail-closed stop.
- Do not silently regenerate the candidate against changed live inputs. Prepare a new bundle and review its new hashes.
- If the canonical write succeeds but read-back verification fails, keep downstream disabled, preserve the backup and logs, and recover only through a separately verified restoration procedure.
- Production write remains a separately authorized operation. This runbook and the existing candidate artifact do not themselves authorize or execute that write.

## Expected final evidence

Record the exact workflow run IDs, original/candidate/backup SHA256 values, pre/post canonical row, post-write read-back result, CCTG EMA tolerance result, and official TrendFoll audit result. Mark PROB-019 closed only after the official 100-security audit passes with zero failures.

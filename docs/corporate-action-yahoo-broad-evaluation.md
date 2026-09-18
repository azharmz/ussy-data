# Corporate-Action Yahoo Broad Acquisition Evaluation

Status: **EVIDENCE CAPTURED / SOURCE ROLE NOT YET CHANGED**

Date: 2026-09-18

This note records the broad read-only Yahoo split discovery experiment. It does not amend the frozen corporate-action-split-v1 source policy and performs no R2 writes.

## Evidence

Run 35291867966 scanned the current Musaffa membership snapshot: 1,327 unique tickers in 14 serialized Yahoo batches (size 100), history from 2000-01-01 through 2026-09-17. It discovered 1,077 distinct split events across 561 securities: 701 forward and 376 reverse events. There were 37 acquisition gaps (2.7882%). Tiingo requests were 0 and R2 reads/writes were 0/0.

The workflow conclusion was FAILURE only because the evidence harness deliberately returned exit code 2 whenever any acquisition gap existed. Discovery itself completed and emitted the full summary.

The 37 gaps were Yahoo symbol-resolution/data-availability failures (YFTzMissingError / missing split column), including lifecycle/stale aliases such as EVTV. They do not prove that 37 split events were missed; they mean those ticker histories were not evaluable by the current Yahoo alias used in this probe.

## Interpretation

Yahoo batched actions is strongly feasible as the scalable discovery/acquisition candidate: 1,290/1,327 current snapshot tickers were evaluable and more than one thousand historical events were discovered without Tiingo requests.

This is not yet sufficient to change the frozen source role. Remaining gates are governed alias/lifecycle handling for gaps, bounded independent Tiingo validation of Yahoo-discovered events, and fail-closed omission detection.

## Next gate

Use Yahoo as discovery candidate, then validate a deterministic bounded sample of discovered events against Tiingo EOD under the <=50 requests/hour budget. Keep identity resolution and source-role promotion separate. No production pointer may advance from this evidence alone.

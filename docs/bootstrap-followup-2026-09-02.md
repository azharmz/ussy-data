# Follow-up bootstrap: MBGL

Last reported R2 run: 33617309210-1. Based on user-provided logs, not a new live R2 check:
1327 compliant, 1299 with history, 1222 ready, 77 insufficient_history, 28 unavailable.
234 uploads, 5 failures, 0 unprocessed. Ready pointer published with 1222 securities.

The missing-history audit identifies MBGL WI, NBY, IMG, SLNO and JMG beyond the previous 23 deferred cases.

## Changes

- Provider alias for security ID US60744M1062: MBGL WI → MBGL. Source ticker stays MBGL WI.
- NBY, IMG, SLNO and JMG moved out of this retry queue into documented operational review. This does NOT change their source compliance or exclude them from membership/readiness accounting.
- Retry queue is now 235 candidates; prior run already uploaded 234. Existing history is skipped after R2 HEAD; conditional writes protect against overwriting.
- MBGL is first. No new workflow needed: commit/push, then run **Bootstrap reviewed tickers** when universe/publish writers are idle.
- The workflow rebuilds rolling/readiness, exports ready data and publishes web status after download.

## Evidence and limits

[S&P Global completed the separation on 1 July 2026](https://press.spglobal.com/2026-07-01-S-P-GLOBAL-INC-COMPLETES-SEPARATION-OF-MOBILITY-GLOBAL-INC).
[Solactive describes the temporary MBGL WI line and regular-way start](https://www.solactive.com/announcements/65565).
MBGL has a short trading history: successful download is not a guarantee of 250 bars or ready status. Never splice SPGI history into MBGL to fill the gap.

NBY announced SDEV alongside a substantial business change; identity and screening need review. IMG moved off Nasdaq to OTC CIMG. SLNO was acquired. JMG was halted and subject to delisting proceedings. Each has a source URL in the queue configuration.

This local change does not update R2 until the workflow runs. No Yahoo download was executed locally.

# Follow-up bootstrap: MBGL

Latest reported R2 run: 33623976189-1. Based on user-provided logs, not a new live R2 check:
1327 compliant, 1300 with history, 1222 ready, 78 insufficient_history, 27 unavailable.
1 upload, 234 existing histories skipped, 0 unprocessed; no failure reported.
Rolling/readiness verified at 2026-09-02T11:27:47.687268+00:00.
Last directly reported ready export: run 33617309210-1, 1222 securities.

The previous missing-history audit identified MBGL WI, NBY, IMG, SLNO and JMG beyond the previous 23 deferred cases. The MBGL follow-up has now completed.

## Changes

- Provider alias for security ID US60744M1062: MBGL WI → MBGL. Source ticker stays MBGL WI.
- NBY, IMG, SLNO and JMG moved out of this retry queue into documented operational review. This does NOT change their source compliance or exclude them from membership/readiness accounting.
- Retry queue is now 235 candidates; prior run already uploaded 234. Existing history is skipped after R2 HEAD; conditional writes protect against overwriting.
- The unpushed MBGL-only optimization was removed after the existing workflow completed successfully and quickly. No bootstrap rerun is needed.
- The workflow rebuilds rolling/readiness, exports ready data and publishes web status after download.
- Rebuilding still reads stored R2 history across the eligible universe; this is distinct from Yahoo downloads and still takes time.

## Evidence and limits

[S&P Global completed the separation on 1 July 2026](https://press.spglobal.com/2026-07-01-S-P-GLOBAL-INC-COMPLETES-SEPARATION-OF-MOBILITY-GLOBAL-INC).
[Solactive describes the temporary MBGL WI line and regular-way start](https://www.solactive.com/announcements/65565).
MBGL has a short trading history: successful download is not a guarantee of 250 bars or ready status. Never splice SPGI history into MBGL to fill the gap.

NBY announced SDEV alongside a substantial business change; identity and screening need review. IMG moved off Nasdaq to OTC CIMG. SLNO was acquired. JMG was halted and subject to delisting proceedings. Each has a source URL in the queue configuration.

Cleanup does not change R2. No Yahoo download was executed locally. See PROJECT_STATUS.md for handoff and outstanding checks.

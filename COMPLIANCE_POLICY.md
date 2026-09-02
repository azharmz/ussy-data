# Eligibility policy

Only exact `sharia_compliance == "COMPLIANT"` is eligible. Missing or other
values are excluded. `musaffaHalalRating` is preserved for audit only.
Readiness still requires the configured history threshold (250 of 300 bars).
Compliant does not automatically mean ready. Source labels and histories stay intact.

## Apply to existing R2 data

1. Commit and push these changes using GitHub Desktop.
2. Wait until production/universe workflows have finished; do not run them concurrently with this migration.
3. Run **Publish web status only**, enabling **Rebuild compliance/readiness from existing R2 history (NO Yahoo download)**.
4. The first step lists missing ticker histories and saves a policy audit in R2.
   It rebuilds rolling/readiness using existing histories, and updates the universe pointer count.
5. Subsequent steps publish `production/ready/current.json`, audit earnings metadata,
   and publish web status. Inspect the rolling step for compliant, ready,
   insufficient_history and data_unavailable totals.

No new Yahoo download is performed by this workflow. Review the missing list before
authorizing a separate bootstrap. Existing histories are not overwritten by repair.
The policy audit includes a backup of the previous universe pointer.

For snapshot 2026-08-28, local membership has 1327 eligible records. R2 readiness
counts must be measured by the workflow, not inferred from this local count.

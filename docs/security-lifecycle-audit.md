# Security lifecycle / delisting audit

Status: **AUDIT COMPLETE / PRODUCTION CHANGE NOT YET APPLIED**

Date: 2026-09-18

## Trigger

Production daily OHLCV currently derives operational_ids as the intersection of the active compliant membership snapshot and existing canonical history. It has no lifecycle/termination gate before Yahoo acquisition. A security that remains in the active membership snapshot therefore continues to be requested after exchange trading has ended.

## Repeated production evidence

The three latest successful OHLCV jobs inspected (runs 35230603797, 35184919545, 35181965379) repeatedly produced Yahoo missing-timezone/no-row evidence for the same four symbols: BLD, EMPG, MPX, PTNM. This establishes a persistent daily acquisition problem rather than a one-off provider failure.

All four remain in the 2026-08-28 Musaffa membership snapshot as COMPLIANT, so membership compliance alone is not a sufficient tradability/lifecycle gate.

## Independently verified lifecycle cases

- BLD / US89055F1030: TopBuild acquisition by QXO completed 2026-07-01; NYSE trading was suspended before the open and delisting initiated. Classification: VERIFIED_TERMINATED_PUBLIC_LISTING, effective 2026-07-01.
- MPX / US5684271084: Marine Products merger completed 2026-05-15; common stock ceased NYSE trading before the open and delisting/deregistration was requested. Classification: VERIFIED_TERMINATED_PUBLIC_LISTING, effective 2026-05-15.
- EMPG / KYG3041J1067: Nasdaq issued a delisting determination in July 2026 after a long trading halt. Public evidence reviewed establishes a delisting process/anticipated removal, but this audit does not yet encode a final effective lifecycle date. Classification: REVIEW_DELISTING_EVIDENCE.
- PTNM / VGG7111A1012: Nasdaq issued a delisting determination in July 2026 after a long trading halt. Public evidence reviewed establishes a delisting process/anticipated removal, but this audit does not yet encode a final effective lifecycle date. Classification: REVIEW_DELISTING_EVIDENCE.

## Governance decision

Do not infer DELISTED from Yahoo YFTzMissingError. Provider failure is only a candidate trigger for lifecycle investigation.

Do not delete canonical history for terminated securities. Historical OHLCV remains immutable evidence and is required for historical research/backtests.

Production daily acquisition should eventually exclude only lifecycle states backed by authoritative evidence and an effective date. UNKNOWN/UNRESOLVED securities remain fail-closed for classification and must not be silently labelled delisted.

## Proposed lifecycle contract (not production yet)

Canonical key: security_id. Suggested fields: lifecycle_status, effective_date, reason, successor_security_id when applicable, evidence_source, evidence_date, reviewed_at, schema_version.

Minimum statuses: ACTIVE, REVIEW_REQUIRED, VERIFIED_TERMINATED_PUBLIC_LISTING. A later design may distinguish merger/acquisition, exchange delisting with possible OTC continuation, ticker change, and security replacement without changing historical identity.

## Next implementation gate

Build a read-only lifecycle registry/validator first. Production exclusion must be covered by tests proving that VERIFIED_TERMINATED_PUBLIC_LISTING is not requested after its effective date, while ACTIVE and REVIEW_REQUIRED remain unaffected. Only then wire the registry into Production daily OHLCV.

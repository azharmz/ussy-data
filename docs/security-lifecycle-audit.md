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
- EMPG / KYG3041J1067: Nasdaq halted trading on 2025-10-23 after the SEC suspension. Nasdaq issued a Staff Delisting Determination on 2026-07-16, with suspension/delisting scheduled for 2026-07-27 absent appeal. The company subsequently stated that its Board decided not to request a hearing and that the suspension had taken effect; Nasdaq/SEC removal paperwork was still described as subsequent/expected. Classification: VERIFIED_NONTRADABLE_NASDAQ, effective 2025-10-23; DELISTING_FINALIZATION_PENDING_FORM25 evidence in this audit.
- PTNM / VGG7111A1012: Nasdaq halted trading on 2025-10-18 after the SEC suspension. Nasdaq announced on 2026-07-07 that the securities would be delisted on 2026-07-16 unless appealed; the company's SEC-filed 6-K repeated that Nasdaq would subsequently file Form 25-NSE. This audit did not find authoritative evidence of an appeal or a final Form 25-NSE effective date. Classification: VERIFIED_NONTRADABLE_NASDAQ, effective 2025-10-18; DELISTING_FINALIZATION_PENDING_FORM25 evidence in this audit.

## Governance decision

Do not infer DELISTED from Yahoo YFTzMissingError. Provider failure is only a candidate trigger for lifecycle investigation.

Do not delete canonical history for terminated securities. Historical OHLCV remains immutable evidence and is required for historical research/backtests.

Production daily acquisition should eventually exclude only lifecycle/tradability states backed by authoritative evidence and an effective date. A verified primary-exchange trading halt is sufficient to stop wasteful primary-ticker daily acquisition after its effective date; it is not equivalent to asserting that legal delisting/Form 25 is complete. UNKNOWN/UNRESOLVED securities remain fail-closed for classification and must not be silently labelled delisted.

## Proposed lifecycle contract (not production yet)

Canonical key: security_id. Suggested fields: lifecycle_status, effective_date, reason, successor_security_id when applicable, evidence_source, evidence_date, reviewed_at, schema_version.

Minimum statuses: ACTIVE, REVIEW_REQUIRED, VERIFIED_NONTRADABLE_PRIMARY_EXCHANGE, VERIFIED_TERMINATED_PUBLIC_LISTING. This separation is intentional: daily OHLCV acquisition should not repeatedly query a security that is authoritatively halted/nontradable on its primary exchange, even when the later legal Form 25 delisting date is not yet established. A later design may distinguish merger/acquisition, exchange delisting with possible OTC continuation, ticker change, and security replacement without changing historical identity.

## Next implementation gate

Build a read-only lifecycle registry/validator first. Production exclusion must be covered by tests proving that VERIFIED_TERMINATED_PUBLIC_LISTING is not requested after its effective date, while ACTIVE and REVIEW_REQUIRED remain unaffected. Only then wire the registry into Production daily OHLCV.

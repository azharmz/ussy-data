"""Read-only lifecycle gate for primary daily OHLCV acquisition."""
from datetime import date

EXCLUDED = {
    "US89055F1030": ("VERIFIED_TERMINATED_PUBLIC_LISTING", "2026-07-01", "BLD"),
    "US5684271084": ("VERIFIED_TERMINATED_PUBLIC_LISTING", "2026-05-15", "MPX"),
    "KYG3041J1067": ("VERIFIED_NONTRADABLE_PRIMARY_EXCHANGE", "2025-10-23", "EMPG"),
    "VGG7111A1012": ("VERIFIED_NONTRADABLE_PRIMARY_EXCHANGE", "2025-10-18", "PTNM"),
}

def acquisition_allowed(security_id: str, as_of: date) -> bool:
    record = EXCLUDED.get(str(security_id))
    return record is None or as_of < date.fromisoformat(record[1])

def exclusion_record(security_id: str, as_of: date):
    return None if acquisition_allowed(security_id, as_of) else EXCLUDED[str(security_id)]

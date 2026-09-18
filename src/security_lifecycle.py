"""Canonical read-only lifecycle gate for primary daily OHLCV acquisition."""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "config" / "security-lifecycle.json"
BLOCKING_STATUSES = {
    "VERIFIED_TERMINATED_PUBLIC_LISTING",
    "VERIFIED_NONTRADABLE_PRIMARY_EXCHANGE",
}


@lru_cache(maxsize=1)
def records() -> dict[str, dict]:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("records"), list):
        raise ValueError("Invalid security lifecycle registry")
    out = {}
    for row in payload["records"]:
        sid = str(row.get("security_id", "")).strip()
        if not sid or sid in out:
            raise ValueError("Missing or duplicate security_id in lifecycle registry")
        out[sid] = row
    return out


def acquisition_allowed(security_id: str, as_of: date) -> bool:
    record = records().get(str(security_id))
    if record is None or record.get("lifecycle_status") not in BLOCKING_STATUSES:
        return True
    effective = record.get("effective_date")
    return not effective or as_of < date.fromisoformat(effective)


def exclusion_record(security_id: str, as_of: date):
    return None if acquisition_allowed(security_id, as_of) else records()[str(security_id)]

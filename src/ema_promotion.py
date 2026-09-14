"""Pure helpers for EMA promotion governance."""
from __future__ import annotations

from datetime import UTC, datetime

from ema_state import PERIODS, PRICE_BASIS
from load_ema_state import PROMOTION_POLICY


def build_promoted_manifest(candidate: dict, production_key: str, digest: str, equivalence: dict) -> dict:
    if candidate.get("status") != "CANDIDATE_NOT_PRODUCTION_APPROVED":
        raise ValueError("Candidate status is not promotable")
    if candidate.get("price_basis") != PRICE_BASIS or candidate.get("periods") != list(PERIODS):
        raise ValueError("Candidate EMA contract mismatch")
    if equivalence.get("numeric_failures") != 0 or equivalence.get("classification_mismatches") != 0:
        raise ValueError("Equivalence did not pass")
    if not isinstance(equivalence.get("verified"), int) or equivalence["verified"] < 1:
        raise ValueError("Equivalence coverage is invalid")
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "price_basis": PRICE_BASIS,
        "periods": list(PERIODS),
        "securities": candidate["securities"],
        "security_ids": candidate["security_ids"],
        "parquet_key": production_key,
        "sha256": digest,
        "source_ready_parquet_key": candidate["source_ready_parquet_key"],
        "source_ready_sha256": candidate["source_ready_sha256"],
        "source_ready_created_at": candidate.get("source_ready_created_at"),
        "update_method": candidate["update_method"],
        "bootstrap_count": candidate.get("bootstrap_count", 0),
        "recursive_count": candidate.get("recursive_count", 0),
        "unchanged_count": candidate.get("unchanged_count", 0),
        "rebuild_count": candidate.get("rebuild_count", 0),
        "as_of_date_min": candidate["as_of_date_min"],
        "as_of_date_max": candidate["as_of_date_max"],
        "equivalence": equivalence,
        "promotion_policy": PROMOTION_POLICY,
        "candidate_manifest_key": candidate.get("candidate_manifest_key"),
    }

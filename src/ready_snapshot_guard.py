"""Fail-closed governance for canonical READY snapshots."""
from __future__ import annotations

from datetime import date


def decide_ready_publication(current: dict | None, candidate_as_of: str, candidate_sha256: str) -> str:
    """Return CREATE or REUSE; raise when publication would violate daily immutability."""
    candidate_date = date.fromisoformat(candidate_as_of)
    if not current:
        return "CREATE"

    current_as_of = current.get("as_of_date")
    current_sha = current.get("sha256")
    current_key = current.get("parquet_key")
    if not current_as_of or not current_sha or not current_key:
        raise RuntimeError("READY current pointer lacks canonical lineage fields")

    current_date = date.fromisoformat(str(current_as_of))
    if candidate_date < current_date:
        raise RuntimeError(
            f"READY rollback rejected: candidate_as_of={candidate_as_of} current_as_of={current_as_of}"
        )
    if candidate_date == current_date:
        if candidate_sha256 == current_sha:
            return "REUSE"
        raise RuntimeError(
            "READY same-day mutation rejected: "
            f"as_of_date={candidate_as_of} current_sha256={current_sha} candidate_sha256={candidate_sha256}"
        )
    return "CREATE"

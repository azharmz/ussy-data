"""Strict semantic classifier for harmless same-day READY late arrivals.

This never authorizes mutation of an immutable READY snapshot. It only proves
that a differing candidate is a pure fixed-window advance: unchanged security
set and row counts, unchanged overlap, with old leading rows replaced by newer
rows for one or more securities. A caller may then retain/reuse the existing
canonical snapshot until the next trading-date publication.
"""
from __future__ import annotations

import pandas as pd

KEY = ["security_id", "date"]
VALUE = ["ticker", "open", "high", "low", "close", "adj_close", "volume"]


def is_pure_security_set_reduction(canonical, candidate) -> tuple[bool, dict]:
    """Allow deferral only when candidate removes securities and changes nothing else."""
    required = set(KEY + VALUE)
    if not required.issubset(canonical.columns) or not required.issubset(candidate.columns):
        return False, {"reason": "missing_required_columns"}
    old = canonical[KEY + VALUE].copy(); new = candidate[KEY + VALUE].copy()
    old["date"] = pd.to_datetime(old["date"], errors="raise"); new["date"] = pd.to_datetime(new["date"], errors="raise")
    if old.duplicated(KEY).any() or new.duplicated(KEY).any(): return False, {"reason": "duplicate_keys"}
    old_sids, new_sids = set(old.security_id.astype(str)), set(new.security_id.astype(str))
    removed_sids = old_sids - new_sids
    if not removed_sids or not new_sids.issubset(old_sids): return False, {"reason": "not_security_set_reduction"}
    retained = old.loc[old.security_id.astype(str).isin(new_sids)].sort_values(KEY).reset_index(drop=True)
    candidate_sorted = new.sort_values(KEY).reset_index(drop=True)
    if len(retained) != len(candidate_sorted): return False, {"reason": "retained_row_count_changed"}
    for col in KEY + VALUE:
        a, b = retained[col], candidate_sorted[col]
        equal = a.eq(b) | (a.isna() & b.isna())
        if not bool(equal.all()): return False, {"reason": "retained_values_changed", "column": col}
    return True, {"reason": "pure_security_set_reduction", "removed_security_ids": sorted(removed_sids), "removed_security_count": len(removed_sids)}


def is_pure_late_arrival_window_advance(canonical, candidate, as_of_date: str) -> tuple[bool, dict]:
    required = set(KEY + VALUE)
    if not required.issubset(canonical.columns) or not required.issubset(candidate.columns):
        return False, {"reason": "missing_required_columns"}

    old = canonical[KEY + VALUE].copy()
    new = candidate[KEY + VALUE].copy()
    old["date"] = pd.to_datetime(old["date"], errors="raise")
    new["date"] = pd.to_datetime(new["date"], errors="raise")
    cutoff = pd.Timestamp(as_of_date)

    if old.duplicated(KEY).any() or new.duplicated(KEY).any():
        return False, {"reason": "duplicate_keys"}
    old_sids, new_sids = set(old.security_id.astype(str)), set(new.security_id.astype(str))
    if old_sids != new_sids:
        return False, {"reason": "security_set_changed"}
    old_counts = old.groupby("security_id").size()
    new_counts = new.groupby("security_id").size()
    if not old_counts.equals(new_counts):
        return False, {"reason": "per_security_row_counts_changed"}

    merged = old.merge(new, on=KEY, how="outer", suffixes=("_old", "_new"), indicator=True)
    overlap = merged[merged["_merge"] == "both"]
    for col in VALUE:
        a, b = overlap[f"{col}_old"], overlap[f"{col}_new"]
        equal = a.eq(b) | (a.isna() & b.isna())
        if not bool(equal.all()):
            return False, {"reason": "overlap_values_changed", "column": col, "rows": int((~equal).sum())}

    removed = merged[merged["_merge"] == "left_only"]
    added = merged[merged["_merge"] == "right_only"]
    if removed.empty or added.empty or len(removed) != len(added):
        return False, {"reason": "not_balanced_window_shift", "removed": len(removed), "added": len(added)}
    if (added["date"] > cutoff).any():
        return False, {"reason": "added_after_as_of"}

    affected_removed = set(removed.security_id.astype(str))
    affected_added = set(added.security_id.astype(str))
    if affected_removed != affected_added:
        return False, {"reason": "affected_security_mismatch"}

    details = []
    for sid in sorted(affected_added):
        rdates = sorted(removed.loc[removed.security_id.astype(str) == sid, "date"])
        adates = sorted(added.loc[added.security_id.astype(str) == sid, "date"])
        if len(rdates) != len(adates) or min(adates) <= max(rdates):
            return False, {"reason": "not_forward_window_advance", "security_id": sid}
        details.append({
            "security_id": sid,
            "removed_dates": [d.date().isoformat() for d in rdates],
            "added_dates": [d.date().isoformat() for d in adates],
        })

    return True, {
        "reason": "pure_late_arrival_window_advance",
        "affected_security_count": len(affected_added),
        "removed_rows": len(removed),
        "added_rows": len(added),
        "details": details,
    }

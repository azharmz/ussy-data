"""Run production OHLCV using only safely finalized US daily bars."""
from __future__ import annotations

import pandas as pd

import update_production
from us_market_finalization import finalized_through

_original_normalize_history = update_production.normalize_history
_stats = {"calls": 0, "excluded_rows": 0, "excluded_dates": {}}


def normalize_finalized_history(*args, **kwargs):
    frame = _original_normalize_history(*args, **kwargs)
    cutoff = finalized_through()
    _stats["calls"] += 1
    if not frame.empty:
        dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
        mask = dates > cutoff
        dropped = int(mask.sum())
        if dropped:
            _stats["excluded_rows"] += dropped
            for value, count in dates.loc[mask].value_counts().items():
                key = str(value)
                _stats["excluded_dates"][key] = _stats["excluded_dates"].get(key, 0) + int(count)
            frame = frame.loc[~mask].copy()
    return frame


def main() -> None:
    update_production.normalize_history = normalize_finalized_history
    try:
        update_production.main()
    finally:
        print(
            "FINALIZATION_FILTER_SUMMARY: "
            f"finalized_through={finalized_through()} calls={_stats['calls']} "
            f"excluded_rows={_stats['excluded_rows']} excluded_dates={_stats['excluded_dates']}",
            flush=True,
        )


if __name__ == "__main__":
    main()

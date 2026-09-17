"""Run production OHLCV using only completed New York calendar days.

Yahoo can expose a same-day interval=1d bar with inconsistent cross-symbol
availability even hours after the US close. Therefore the current New York date
is never ingested. The just-closed session becomes eligible after NY midnight.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import update_production

NY = ZoneInfo("America/New_York")
_original_normalize_history = update_production.normalize_history


def normalize_finalized_history(*args, **kwargs):
    frame = _original_normalize_history(*args, **kwargs)
    ny_today = datetime.now(tz=NY).date()
    if not frame.empty:
        dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
        dropped = int((dates >= ny_today).sum())
        if dropped:
            print(f"FINALIZATION_FILTER: excluded {dropped} non-finalized {ny_today} daily bar(s)", flush=True)
            frame = frame.loc[dates < ny_today].copy()
    return frame


def main() -> None:
    update_production.normalize_history = normalize_finalized_history
    update_production.main()


if __name__ == "__main__":
    main()

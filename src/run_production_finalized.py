"""Run production OHLCV while excluding an unfinished current US daily candle.

This keeps normal production/patch workflows usable during US market hours: Yahoo may
return today's interval=1d candle while it is still forming, so only that date is
filtered out. Previously closed dates remain eligible for ingestion.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

import update_production

NY = ZoneInfo("America/New_York")
SAFE_AFTER = time(18, 0)
_original_normalize_history = update_production.normalize_history


def _today_is_unfinished() -> tuple[bool, object]:
    now = datetime.now(tz=NY)
    # On a US weekday, conservatively treat today's Yahoo daily candle as unfinished
    # until 18:00 New York time. Weekends have no regular-session daily candle.
    return now.weekday() < 5 and now.time().replace(tzinfo=None) < SAFE_AFTER, now.date()


def normalize_finalized_history(*args, **kwargs):
    frame = _original_normalize_history(*args, **kwargs)
    unfinished, ny_today = _today_is_unfinished()
    if unfinished and not frame.empty:
        dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
        dropped = int((dates == ny_today).sum())
        if dropped:
            print(f"FINALIZATION_FILTER: excluded {dropped} unfinished {ny_today} daily bar(s)")
            frame = frame.loc[dates != ny_today].copy()
    return frame


def main() -> None:
    update_production.normalize_history = normalize_finalized_history
    update_production.main()


if __name__ == "__main__":
    main()

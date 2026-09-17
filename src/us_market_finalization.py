"""Shared eligibility policy for US daily bars.

A same-day Yahoo daily bar becomes eligible only after the regular US session
close plus a conservative safety buffer. This intentionally avoids treating a
bar observed during the session as final while no longer waiting for NY
midnight. Early-close sessions remain conservative (eligible no later than the
regular-close rule); absence of a bar on holidays is handled by the data source.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
REGULAR_CLOSE = time(16, 0)
SAFETY_BUFFER = timedelta(minutes=90)


def finalized_through(now: datetime | None = None) -> date:
    """Latest calendar date whose regular session is safely past finalization."""
    current = now or datetime.now(tz=NY)
    if current.tzinfo is None:
        raise ValueError("finalization time must be timezone-aware")
    current = current.astimezone(NY)
    close = datetime.combine(current.date(), REGULAR_CLOSE, tzinfo=NY) + SAFETY_BUFFER
    if current >= close:
        return current.date()
    return current.date() - timedelta(days=1)


def is_finalized_bar_date(bar_date: date, now: datetime | None = None) -> bool:
    return bar_date <= finalized_through(now)

"""Fail closed when a production OHLCV run could ingest an unfinished US daily bar.

Yahoo exposes the current 1d candle while the US session is still in progress. The
production updater is append-only by date, so accepting that candle can permanently
freeze a partial OHLCV/volume bar. Production writes are therefore blocked on US
trading weekdays from 09:00 through 17:59 America/New_York. The normal 03:07 UTC
schedule is outside this window.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BLOCK_START = time(9, 0)
SAFE_AFTER = time(18, 0)


def is_unsafe_us_session(now: datetime) -> bool:
    local = now.astimezone(NY)
    return local.weekday() < 5 and BLOCK_START <= local.time().replace(tzinfo=None) < SAFE_AFTER


def main() -> None:
    now = datetime.now(tz=NY)
    if is_unsafe_us_session(now):
        raise SystemExit(
            "BLOCKED: production OHLCV update is inside the US same-day session/finalization "
            f"window ({now.isoformat()}). Current-day Yahoo 1d bars may be partial. "
            "Run after 18:00 America/New_York or use the normal next-day schedule."
        )
    print(f"US market-finalization guard PASS: {now.isoformat()}")


if __name__ == "__main__":
    main()

"""Fail closed while the current US daily bar is not safely finalized."""
from __future__ import annotations

from datetime import datetime

from us_market_finalization import NY, finalized_through


def is_unsafe_us_session(now: datetime) -> bool:
    local = now.astimezone(NY)
    return local.weekday() < 5 and finalized_through(local) < local.date()


def main() -> None:
    now = datetime.now(tz=NY)
    if is_unsafe_us_session(now):
        raise SystemExit(
            "BLOCKED: current US daily bar is not past the shared regular-close + 90m "
            f"finalization buffer ({now.isoformat()})."
        )
    print(f"US market-finalization guard PASS: {now.isoformat()} finalized_through={finalized_through(now)}", flush=True)


if __name__ == "__main__":
    main()

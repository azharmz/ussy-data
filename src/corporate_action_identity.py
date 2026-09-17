"""Fail-closed identity lineage for corporate-action facts.

This module never infers identity from ticker alone. Historical aliases must be
explicitly reviewed, security-scoped, and date-bounded before an event can be
attached to the canonical security_id.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ProviderAlias:
    security_id: str
    provider: str
    provider_ticker: str
    valid_from: date | None
    valid_to: date | None
    evidence: str

    def contains(self, event_date: date) -> bool:
        return (self.valid_from is None or self.valid_from <= event_date) and (
            self.valid_to is None or event_date <= self.valid_to
        )


# Reviewed lifecycle evidence only. EVTV -> AZIO is the first explicit case:
# Nasdaq/company evidence says AZIO began trading at market open 2026-07-13,
# while the security identity remained US29414V3087.
REVIEWED_PROVIDER_ALIASES: tuple[ProviderAlias, ...] = (
    ProviderAlias(
        security_id="US29414V3087",
        provider="tiingo_eod",
        provider_ticker="EVTV",
        valid_from=None,
        valid_to=date(2026, 7, 12),
        evidence="Nasdaq/company ticker change: AZIO effective 2026-07-13",
    ),
    ProviderAlias(
        security_id="US29414V3087",
        provider="tiingo_eod",
        provider_ticker="AZIO",
        valid_from=date(2026, 7, 13),
        valid_to=None,
        evidence="Nasdaq/company ticker change: AZIO effective 2026-07-13",
    ),
)


def resolve_historical_identity(
    provider: str,
    provider_ticker: str,
    event_date: date,
    aliases: tuple[ProviderAlias, ...] = REVIEWED_PROVIDER_ALIASES,
) -> str:
    """Resolve one provider alias at event_date; fail closed otherwise."""
    p = provider.strip().lower()
    t = provider_ticker.strip().upper()
    matches = {
        a.security_id
        for a in aliases
        if a.provider.lower() == p and a.provider_ticker.upper() == t and a.contains(event_date)
    }
    if len(matches) != 1:
        raise ValueError("historical identity mapping must be unique and date-valid")
    return next(iter(matches))


def validate_alias_registry(aliases: tuple[ProviderAlias, ...] = REVIEWED_PROVIDER_ALIASES) -> None:
    """Reject malformed/overlapping aliases that could create ambiguity."""
    for a in aliases:
        if not a.security_id or not a.provider or not a.provider_ticker or not a.evidence:
            raise ValueError("alias lineage fields required")
        if a.valid_from is not None and a.valid_to is not None and a.valid_from > a.valid_to:
            raise ValueError("invalid alias date range")

    for i, left in enumerate(aliases):
        for right in aliases[i + 1 :]:
            if left.provider.lower() != right.provider.lower():
                continue
            if left.provider_ticker.upper() != right.provider_ticker.upper():
                continue
            start = max(left.valid_from or date.min, right.valid_from or date.min)
            end = min(left.valid_to or date.max, right.valid_to or date.max)
            if start <= end and left.security_id != right.security_id:
                raise ValueError("overlapping provider alias maps to multiple securities")

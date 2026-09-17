"""Deterministic validation harness for corporate-action split contract v1.

No network and no R2 writes. This validates arithmetic, temporal, identity,
conflict, malformed-source, and pointer-last publication semantics before pilot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SplitEvent:
    security_id: str
    effective_date: date
    split_factor: float
    provider_ticker: str


def validate_event(e: SplitEvent) -> None:
    if not e.security_id or not e.provider_ticker:
        raise ValueError("identity fields required")
    if not math.isfinite(e.split_factor) or e.split_factor <= 0 or e.split_factor == 1:
        raise ValueError("invalid split factor")


def cumulative_factor(events: list[SplitEvent], security_id: str, row_date: date, as_of: date) -> float:
    """Product for row_date < effective_date <= as_of; future events excluded."""
    factor = 1.0
    for e in events:
        validate_event(e)
        if e.security_id == security_id and row_date < e.effective_date <= as_of:
            factor *= e.split_factor
    return factor


def normalize_raw_price(value: float, factor: float) -> float:
    return value / factor


def normalize_share_volume(value: float, factor: float) -> float:
    return value * factor


def resolve_identity(provider_ticker: str, aliases: dict[str, list[str]]) -> str:
    ids = aliases.get(provider_ticker.upper(), [])
    if len(ids) != 1:
        raise ValueError("identity mapping must be unique")
    return ids[0]


def dedupe_events(events: list[SplitEvent]) -> list[SplitEvent]:
    by_key: dict[tuple[str, date], SplitEvent] = {}
    for e in events:
        validate_event(e)
        key = (e.security_id, e.effective_date)
        prior = by_key.get(key)
        if prior is not None and not math.isclose(prior.split_factor, e.split_factor, rel_tol=0, abs_tol=1e-12):
            raise ValueError("conflicting split facts")
        by_key.setdefault(key, e)
    return list(by_key.values())


def parse_source_row(row: dict) -> float | None:
    if "splitFactor" not in row:
        raise ValueError("malformed source row")
    value = row["splitFactor"]
    if value is None:
        raise ValueError("null splitFactor")
    try:
        factor = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("non-numeric splitFactor") from exc
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("invalid source splitFactor")
    return None if factor == 1.0 else factor


class FakeObjectStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.write_order: list[str] = []

    def put(self, key: str, body: bytes) -> None:
        self.objects[key] = body
        self.write_order.append(key)


def publish_validated_run(store: FakeObjectStore, run_id: str, events_body: bytes, manifest_body: bytes, *, validation_passed: bool) -> None:
    """Minimal publication-order model: immutable artifacts first, pointer LAST."""
    if not validation_passed:
        raise ValueError("validation must pass before publication")
    prefix = f"corporate_actions/splits/runs/{run_id}"
    store.put(f"{prefix}/events.parquet", events_body)
    store.put(f"{prefix}/manifest.json", manifest_body)
    store.put("corporate_actions/splits/current.json", (prefix + "\n").encode())


def run_checks() -> dict[str, str]:
    sid = "US67066G1040"
    nvda = SplitEvent(sid, date(2024, 6, 10), 10.0, "NVDA")
    reverse = SplitEvent("US0000000001", date(2024, 5, 1), 0.1, "REV")

    assert cumulative_factor([nvda], sid, date(2024, 6, 7), date(2024, 6, 10)) == 10.0
    assert normalize_raw_price(1000.0, 10.0) == 100.0
    assert normalize_share_volume(100.0, 10.0) == 1000.0
    assert normalize_raw_price(10.0, 0.1) == 100.0
    assert normalize_share_volume(1000.0, 0.1) == 100.0

    # Effective-date event cannot leak into an earlier as-of date.
    assert cumulative_factor([nvda], sid, date(2024, 6, 7), date(2024, 6, 9)) == 1.0
    # Event does not adjust rows on/after its effective date.
    assert cumulative_factor([nvda], sid, date(2024, 6, 10), date(2024, 6, 10)) == 1.0
    assert cumulative_factor([reverse], reverse.security_id, date(2024, 4, 30), date(2024, 5, 1)) == 0.1

    assert resolve_identity("nvda", {"NVDA": [sid]}) == sid
    for aliases in ({}, {"NVDA": [sid, "OTHER"]}):
        try:
            resolve_identity("NVDA", aliases)
            raise AssertionError("identity failure expected")
        except ValueError:
            pass

    assert len(dedupe_events([nvda, nvda])) == 1
    try:
        dedupe_events([nvda, SplitEvent(sid, nvda.effective_date, 5.0, "NVDA")])
        raise AssertionError("conflict failure expected")
    except ValueError:
        pass

    assert parse_source_row({"splitFactor": 1}) is None
    assert parse_source_row({"splitFactor": 10}) == 10.0
    for bad in ({}, {"splitFactor": None}, {"splitFactor": "x"}, {"splitFactor": 0}, {"splitFactor": float("nan")}):
        try:
            parse_source_row(bad)
            raise AssertionError("malformed source failure expected")
        except ValueError:
            pass

    store = FakeObjectStore()
    publish_validated_run(store, "validation", b"events", b"manifest", validation_passed=True)
    assert store.write_order[-1] == "corporate_actions/splits/current.json"
    assert len(store.write_order) == 3
    failed = FakeObjectStore()
    try:
        publish_validated_run(failed, "bad", b"events", b"manifest", validation_passed=False)
        raise AssertionError("validation gate expected")
    except ValueError:
        pass
    assert failed.write_order == []

    return {
        "arithmetic_forward": "PASS",
        "arithmetic_reverse": "PASS",
        "future_event_exclusion": "PASS",
        "identity_fail_closed": "PASS",
        "duplicate_conflict": "PASS",
        "malformed_source": "PASS",
        "pointer_last": "PASS",
        "canonical_ohlcv_writes": "ZERO_BY_HARNESS",
    }


if __name__ == "__main__":
    import json
    print(json.dumps({"status": "PASS", "checks": run_checks()}, indent=2))

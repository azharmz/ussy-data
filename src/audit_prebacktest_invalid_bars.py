"""Capture read-only provider evidence for the three failed pre-backtest bars."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ohlcv_qc import FIELDS, issues

TARGETS = (
    {"security_id": "IL0010828585", "ticker": "WILC", "position": 3731,
     "expected_rule": "low_above_open_close_or_high"},
    {"security_id": "US4435106079", "ticker": "HUBB", "position": 12215,
     "expected_rule": "low_above_open_close_or_high"},
    {"security_id": "US82981J8514", "ticker": "SITC", "position": 7115,
     "expected_rule": "high_below_open_close_or_low"},
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def values(row: dict) -> dict:
    return {field: float(row[field]) for field in FIELDS}


def compare(stored: dict, provider: dict) -> dict:
    return {field: values(provider)[field] - values(stored)[field] for field in FIELDS}


def target_row(frame, target: dict) -> dict:
    if len(frame) <= target["position"]:
        raise ValueError("Target position is outside history")
    row = frame.iloc[target["position"]].to_dict()
    if str(row.get("security_id")) != target["security_id"] or str(row.get("ticker")) != target["ticker"]:
        raise ValueError("Target identity differs from gate evidence")
    found = issues(row)
    if target["expected_rule"] not in found:
        raise ValueError("Target bar is no longer the expected invalid bar")
    return row


def main() -> int:
    import pandas as pd
    import yfinance as yf
    from bootstrap_ohlcv import make_s3_client, normalize_history
    from provider_symbols import yahoo_symbol
    from update_production import extract_symbol

    out = Path("diagnostics/prebacktest-invalid-bars")
    out.mkdir(parents=True, exist_ok=False)
    report = {"started_at": now(), "status": "capturing", "mode": "r2_read_only_plus_yahoo_evidence",
              "r2_writes": 0, "targets": [], "errors": [],
              "limitations": ["Provider refetch is current evidence, not the original Yahoo response.",
                              "This workflow never repairs R2 or republishes ready data."]}

    def save():
        (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    save()
    logging.disable(logging.CRITICAL)
    try:
        s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
        captured = {}
        for target in TARGETS:
            sid, ticker = target["security_id"], target["ticker"]
            item = {**target, "status": "failed"}
            report["targets"].append(item)
            key = f"backtest/ohlcv/{sid}.parquet"
            obj = s3.get_object(Bucket=bucket, Key=key)
            body, etag = obj["Body"].read(), obj["ETag"]
            captured[key] = etag
            (out / f"{sid}-stored.parquet").write_bytes(body)
            frame = pd.read_parquet(io.BytesIO(body))
            stored = target_row(frame, target)
            day = pd.to_datetime(stored["date"], utc=True).date()
            item.update(stored_date=day.isoformat(), stored_ohlcv=values(stored), stored_qc=issues(stored),
                        history_etag=etag, history_sha256=hashlib.sha256(body).hexdigest())
            start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
            symbol = yahoo_symbol(ticker, sid)
            item["provider_request"] = {"symbol": symbol, "start": start, "end": end,
                "interval": "1d", "auto_adjust": False, "repair": False, "prepost": False}
            raw = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=False,
                              actions=False, repair=False, prepost=False, progress=False, threads=False, timeout=30)
            raw.to_parquet(out / f"{sid}-yahoo-raw.parquet", index=True)
            if raw.empty:
                raise ValueError("Provider returned no bar")
            normalized = normalize_history(extract_symbol(raw, symbol, 1), sid, ticker)
            selected = normalized[pd.to_datetime(normalized["date"], utc=True).dt.date == day]
            if len(selected) != 1:
                raise ValueError("Provider returned missing or duplicate target date")
            provider = selected.iloc[0].to_dict()
            provider_issues = issues(provider)
            item.update(provider_ohlcv=values(provider), provider_qc=provider_issues,
                        delta_provider_minus_stored=compare(stored, provider),
                        repair_evidence_confirmed=not provider_issues)
            item["status"] = "provider_valid" if not provider_issues else "provider_invalid"
            save()
        for key, etag in captured.items():
            if s3.head_object(Bucket=bucket, Key=key)["ETag"] != etag:
                report["errors"].append({"stage": "atomicity", "key": key, "error": "etag_changed"})
        report["status"] = "complete" if not report["errors"] and all(x["status"] == "provider_valid" for x in report["targets"]) else "review_required"
    except Exception as exc:
        report["status"] = "review_required"
        report["errors"].append({"exception_type": type(exc).__name__})
    report["finished_at"] = now()
    save()
    print("Targeted evidence captured; inspect report.json. No R2 repair was attempted.")
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

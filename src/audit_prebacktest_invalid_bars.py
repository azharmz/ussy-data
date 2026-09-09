"""Capture read-only provider evidence for the three failed pre-backtest bars."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
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


def tiingo_row(symbol: str, day, token: str) -> dict:
    query = urllib.parse.urlencode({"startDate": day.isoformat(), "endDate": day.isoformat(), "token": token})
    request = urllib.request.Request(
        f"https://api.tiingo.com/tiingo/daily/{symbol}/prices?{query}", headers={"User-Agent": "ussy-data-audit/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200].replace(token, "[REDACTED]")
        raise ValueError(f"Tiingo HTTP {exc.code}: {detail}") from exc
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("Tiingo returned missing or duplicate target date")
    source = data[0]
    result = {"date": str(source.get("date", ""))[:10]}
    for field in ("open", "high", "low", "close", "volume"):
        result[field] = float(source[field])
    result["adj_close"] = float(source["adjClose"])
    if result["date"] != day.isoformat():
        raise ValueError("Tiingo returned a different date")
    if issues(result):
        raise ValueError(f"Tiingo QC rejected bar: {issues(result)}")
    return result


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
        tiingo_token = os.environ["TIINGO_API_KEY"]
        captured = {}
        for target in TARGETS:
            sid, ticker = target["security_id"], target["ticker"]
            item = {**target, "status": "failed"}
            report["targets"].append(item)
            try:
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
                try:
                    raw = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=False,
                                      actions=False, repair=False, prepost=False, progress=False, threads=False, timeout=30)
                    raw.to_parquet(out / f"{sid}-yahoo-raw.parquet", index=True)
                    if raw.empty:
                        raise ValueError("Yahoo returned no bar")
                    normalized = normalize_history(extract_symbol(raw, symbol, 1), sid, ticker)
                    selected = normalized[pd.to_datetime(normalized["date"], utc=True).dt.date == day]
                    if len(selected) != 1:
                        raise ValueError("Yahoo returned missing or duplicate target date")
                    provider = selected.iloc[0].to_dict()
                    item.update(yahoo_ohlcv=values(provider), yahoo_qc=issues(provider),
                                delta_yahoo_minus_stored=compare(stored, provider))
                except Exception as exc:
                    item.update(yahoo_error_type=type(exc).__name__, yahoo_error=str(exc)[:200])
                tiingo = tiingo_row(ticker, day, tiingo_token)
                item.update(tiingo_ohlcv=values(tiingo), tiingo_adj_close=float(tiingo["adj_close"]),
                            tiingo_qc=issues(tiingo), delta_tiingo_minus_stored=compare(stored, tiingo),
                            tiingo_bar_valid=True, repair_evidence_confirmed=False,
                            repair_review_reason="A valid Tiingo bar is independent evidence, not automatic authorization to overwrite Yahoo-basis history.",
                            status="tiingo_valid")
            except Exception as exc:
                item.update(status="tiingo_unavailable", error_type=type(exc).__name__,
                            error=str(exc)[:200])
            save()
        for key, etag in captured.items():
            if s3.head_object(Bucket=bucket, Key=key)["ETag"] != etag:
                report["errors"].append({"stage": "atomicity", "key": key, "error": "etag_changed"})
        report["status"] = "complete" if not report["errors"] and all(x["status"] == "tiingo_valid" for x in report["targets"]) else "review_required"
    except Exception as exc:
        report["status"] = "review_required"
        report["errors"].append({"exception_type": type(exc).__name__})
    report["finished_at"] = now()
    save()
    print("Targeted evidence captured; inspect report.json. No R2 repair was attempted.")
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

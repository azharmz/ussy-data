"""Compare Twelve Data and Yahoo daily OHLCV without writing production data."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


DEFAULT_SYMBOLS = ("SPY", "BHP", "FSI", "NVS", "AMAT", "PODD")
FIELDS = ("open", "high", "low", "close", "volume")
API_URL = "https://api.twelvedata.com/time_series"
SYMBOL_RE = re.compile(r"^[A-Z0-9.^=-]{1,24}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--request-delay", type=float, default=9.0)
    return parser.parse_args()


def parse_symbols(raw: str) -> list[str]:
    symbols = [value.strip().upper() for value in raw.split(",") if value.strip()]
    if not symbols or len(symbols) > 20 or len(set(symbols)) != len(symbols):
        raise ValueError("Provide 1-20 unique symbols")
    if any(not SYMBOL_RE.fullmatch(symbol) for symbol in symbols):
        raise ValueError("Invalid symbol")
    return symbols


def parse_twelve_payload(payload: dict, requested_symbol: str) -> tuple[dict, list[dict]]:
    if payload.get("status") == "error" or "values" not in payload:
        raise ValueError(f"Twelve Data response error code={payload.get('code', 'unknown')}")
    meta = payload.get("meta")
    if not isinstance(meta, dict) or str(meta.get("symbol", "")).upper() != requested_symbol:
        raise ValueError("Twelve Data symbol identity mismatch")
    rows = []
    for source in payload["values"]:
        try:
            row = {"date": str(source["datetime"])[:10]}
            row.update({field: float(source[field]) for field in FIELDS})
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Twelve Data returned incomplete or nonnumeric OHLCV") from exc
        if row_issues(row):
            raise ValueError(f"Twelve Data QC failed date={row['date']} rules={row_issues(row)}")
        rows.append(row)
    rows.sort(key=lambda row: row["date"])
    if not rows or len({row["date"] for row in rows}) != len(rows):
        raise ValueError("Twelve Data returned empty or duplicate dates")
    safe_meta = {key: meta.get(key) for key in
                 ("symbol", "interval", "currency", "exchange", "mic_code", "type", "exchange_timezone")}
    return safe_meta, rows


def row_issues(row: dict) -> list[str]:
    try:
        values = {field: float(row[field]) for field in FIELDS}
    except (KeyError, TypeError, ValueError):
        return ["missing_or_nonnumeric"]
    if not all(math.isfinite(value) for value in values.values()):
        return ["nonfinite"]
    issues = []
    if any(values[field] <= 0 for field in FIELDS[:-1]):
        issues.append("nonpositive_price")
    if values["volume"] < 0:
        issues.append("negative_volume")
    if values["high"] < max(values["open"], values["close"], values["low"]):
        issues.append("high_below_open_close_or_low")
    if values["low"] > min(values["open"], values["close"], values["high"]):
        issues.append("low_above_open_close_or_high")
    return issues


def compare_rows(twelve_rows: list[dict], yahoo_rows: list[dict]) -> dict:
    twelve = {row["date"]: row for row in twelve_rows}
    yahoo_observed = {row["date"]: row for row in yahoo_rows}
    yahoo_invalid = {day: row.get("issues", []) for day, row in yahoo_observed.items()
                     if row.get("issues")}
    yahoo = {day: row for day, row in yahoo_observed.items() if not row.get("issues")}
    common = sorted(set(twelve) & set(yahoo))
    comparisons = []
    for day in common:
        comparisons.append({
            "date": day,
            "absolute_delta": {field: twelve[day][field] - yahoo[day][field] for field in FIELDS},
            "relative_delta": {
                field: None if yahoo[day][field] == 0 else
                (twelve[day][field] - yahoo[day][field]) / yahoo[day][field]
                for field in FIELDS
            },
        })
    return {
        "twelve_latest_date": max(twelve) if twelve else None,
        "yahoo_latest_valid_date": max(yahoo) if yahoo else None,
        "yahoo_latest_observed_date": max(yahoo_observed) if yahoo_observed else None,
        "yahoo_observed_dates_with_issues": yahoo_invalid,
        "dates_only_in_twelve": sorted(set(twelve) - set(yahoo)),
        "dates_only_in_yahoo": sorted(set(yahoo) - set(twelve)),
        "common_date_count": len(common),
        "overlap": comparisons,
    }


def fetch_twelve(symbol: str, start: str, end: str, api_key: str) -> tuple[dict, list[dict]]:
    params = urllib.parse.urlencode({"symbol": symbol, "interval": "1day", "start_date": start,
                                     "end_date": end, "adjust": "none", "apikey": api_key})
    request = urllib.request.Request(API_URL + "?" + params, headers={"User-Agent": "ussy-data-audit/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    return parse_twelve_payload(payload, symbol)


def fetch_yahoo(symbol: str, start: str, end: str) -> list[dict]:
    import pandas as pd
    import yfinance as yf
    raw = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=False,
                      actions=False, repair=False, prepost=False, progress=False,
                      threads=False, timeout=30)
    if isinstance(raw.columns, pd.MultiIndex):
        if symbol in raw.columns.get_level_values(1):
            raw = raw.xs(symbol, axis=1, level=1)
        elif symbol in raw.columns.get_level_values(0):
            raw = raw[symbol]
        else:
            raise ValueError("Unexpected Yahoo column layout")
    rows = []
    for stamp, source in raw.iterrows():
        row = {"date": pd.Timestamp(stamp).strftime("%Y-%m-%d")}
        for field, column in (("open", "Open"), ("high", "High"), ("low", "Low"),
                              ("close", "Close"), ("volume", "Volume")):
            row[field] = float(source[column])
        row["issues"] = row_issues(row)
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("date", *FIELDS, "issues"), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "issues": "|".join(row.get("issues", []))})


def main() -> int:
    args = parse_args()
    symbols = parse_symbols(args.symbols)
    if args.lookback_days < 5 or not 8.5 <= args.request_delay <= 60:
        raise ValueError("lookback-days must be >=5 and request-delay must be 8.5-60 seconds")
    api_key = os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key:
        raise ValueError("TWELVE_DATA_API_KEY is required")
    out = Path("diagnostics/twelve-data")
    out.mkdir(parents=True, exist_ok=False)
    end_day = date.today() + timedelta(days=1)
    start_day = date.today() - timedelta(days=args.lookback_days)
    start, end = start_day.isoformat(), end_day.isoformat()
    report = {
        "started_at": datetime.now(UTC).isoformat(), "status": "running",
        "mode": "audit_only", "r2_reads": 0, "r2_writes": 0,
        "symbols": symbols, "parameters": {"start_date": start, "end_date": end,
        "interval": "1day", "adjust": "none", "request_delay_seconds": args.request_delay},
        "results": [], "errors": [],
        "limitations": ["Provider values may differ without either provider being corrupt.",
                        "This audit does not authorize filling or publishing bars.",
                        "Twelve Data daily timestamps use exchange-local dates."],
    }
    report_path = out / "report.json"
    def save() -> None:
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    save()
    for index, symbol in enumerate(symbols):
        if index:
            time.sleep(args.request_delay)
        result = {"symbol": symbol}
        report["results"].append(result)
        save()
        try:
            meta, twelve_rows = fetch_twelve(symbol, start, end, api_key)
            yahoo_rows = fetch_yahoo(symbol, start, end)
            write_csv(out / f"{symbol}-twelve.csv", twelve_rows)
            write_csv(out / f"{symbol}-yahoo.csv", yahoo_rows)
            result.update(twelve_meta=meta, twelve_rows=len(twelve_rows), yahoo_rows=len(yahoo_rows),
                          comparison=compare_rows(twelve_rows, yahoo_rows))
        except Exception as exc:
            result["status"] = "failed"
            result["error_type"] = type(exc).__name__
            result["error"] = str(exc).replace(api_key, "[REDACTED]")
            report["errors"].append({"symbol": symbol, "error_type": type(exc).__name__})
        else:
            result["status"] = "complete"
        save()
    report["status"] = "complete" if not report["errors"] else "complete_with_errors"
    report["finished_at"] = datetime.now(UTC).isoformat()
    save()
    print(f"Twelve Data audit {report['status']}; no R2 access or writes performed.")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

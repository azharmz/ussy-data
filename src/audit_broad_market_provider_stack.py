"""Read-only #54 audit of a Tiingo -> Yahoo broad-market adjusted-close stack.

Uses the same deterministic #53 membership sample as the Tiingo-only audit.
Tiingo is tried first. Yahoo/yfinance is used only when Tiingo errors or lacks
252 adjusted-close bars. No R2 writes and no leader labels.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from audit_broad_market_tiingo import (
    REQUIRED_BARS,
    deterministic_sample,
    eligible_symbols,
    evaluate_rows,
    load_membership,
)
from audit_tiingo import fetch as fetch_tiingo
from bootstrap_ohlcv import make_s3_client

VERSION = "54-cycle1-broad-market-provider-stack-audit-v1"


def yahoo_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


def fetch_yahoo_adjusted(symbol: str, start: str, end: str) -> list[dict]:
    provider_symbol = yahoo_symbol(symbol)
    raw = yf.download(
        provider_symbol,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=False,
        actions=False,
        repair=False,
        prepost=False,
        progress=False,
        threads=False,
        timeout=30,
    )
    if raw.empty:
        raise ValueError("Yahoo returned no rows")
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = raw.columns.get_level_values(0)
        level1 = raw.columns.get_level_values(1)
        if provider_symbol in level0:
            raw = raw[provider_symbol]
        elif provider_symbol in level1:
            raw = raw.xs(provider_symbol, axis=1, level=1)
        else:
            raw.columns = raw.columns.get_level_values(0)
    adj_col = "Adj Close"
    if adj_col not in raw.columns:
        raise ValueError("Yahoo adjusted close missing")
    out = []
    for stamp, row in raw.iterrows():
        value = pd.to_numeric(row[adj_col], errors="coerce")
        if pd.notna(value) and float(value) > 0:
            out.append({"date": pd.Timestamp(stamp).strftime("%Y-%m-%d"), "adj_close": float(value)})
    if not out:
        raise ValueError("Yahoo has no positive adjusted-close rows")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample-size", type=int, default=100)
    p.add_argument("--lookback-days", type=int, default=550)
    p.add_argument("--request-delay", type=float, default=0.25)
    args = p.parse_args()

    token = os.environ.get("TIINGO_API_KEY")
    if not token:
        raise ValueError("TIINGO_API_KEY is required")
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    pointer, _, membership = load_membership(s3, bucket)
    symbols = eligible_symbols(membership)
    sample = deterministic_sample(symbols, args.sample_size, str(pointer["run_id"]))
    start = (date.today() - timedelta(days=args.lookback_days)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()

    report = {
        "version": VERSION,
        "started_at": datetime.now(UTC).isoformat(),
        "mode": "READ_ONLY_SOURCE_STACK_AUDIT",
        "membership_run_id": pointer["run_id"],
        "membership_manifest_sha256": pointer["manifest_sha256"],
        "eligible_non_etf_non_test_symbols": len(symbols),
        "sample_size_actual": len(sample),
        "required_adjusted_close_bars": REQUIRED_BARS,
        "provider_order": ["TIINGO", "YAHOO_YFINANCE"],
        "results": [],
    }

    for i, symbol in enumerate(sample):
        if i and args.request_delay:
            time.sleep(args.request_delay)
        result = {"symbol": symbol, "selected_provider": None}
        tiingo_eval = None
        try:
            tiingo_eval = evaluate_rows(fetch_tiingo(symbol, start, end, token))
            result["tiingo"] = {"status": "OK", **tiingo_eval}
        except Exception as exc:
            result["tiingo"] = {"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)[:300]}

        if tiingo_eval and tiingo_eval["has_252_adjusted_close_bars"]:
            result["selected_provider"] = "TIINGO"
            result["has_252_adjusted_close_bars"] = True
        else:
            try:
                yahoo_eval = evaluate_rows(fetch_yahoo_adjusted(symbol, start, end))
                result["yahoo"] = {"status": "OK", **yahoo_eval}
                if yahoo_eval["has_252_adjusted_close_bars"]:
                    result["selected_provider"] = "YAHOO_YFINANCE"
                    result["has_252_adjusted_close_bars"] = True
                else:
                    result["has_252_adjusted_close_bars"] = False
            except Exception as exc:
                result["yahoo"] = {"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)[:300]}
                result["has_252_adjusted_close_bars"] = False
        report["results"].append(result)

    ready = [r for r in report["results"] if r["has_252_adjusted_close_bars"]]
    tiingo_selected = [r for r in ready if r["selected_provider"] == "TIINGO"]
    yahoo_selected = [r for r in ready if r["selected_provider"] == "YAHOO_YFINANCE"]
    report["summary"] = {
        "symbols_with_252_adjusted_close_bars": len(ready),
        "coverage_fraction_252": None if not sample else len(ready) / len(sample),
        "selected_tiingo": len(tiingo_selected),
        "selected_yahoo_fallback": len(yahoo_selected),
        "unresolved": len(sample) - len(ready),
    }
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["interpretation"] = (
        "Descriptive provider-stack audit only. Tiingo and Yahoo are not interchangeable silently: "
        "any later publisher must preserve selected provider, source symbol, fetch timestamp, and object provenance per security."
    )
    out = Path("diagnostics/54-broad-market-provider-stack")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded read-only Alpaca parity probe against canonical READY.

No R2 writes. Fetches Alpaca SIP daily bars with adjustment=all for a bounded
sample of securities missing a target session in READY, then compares Alpaca's
adjusted close with canonical Yahoo adj_close on overlapping dates.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from datetime import timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from bootstrap_ohlcv import make_s3_client

READY_POINTER = "production/ready/current.json"
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/bars"


def parity_metrics(canonical: pd.DataFrame, alpaca: pd.DataFrame) -> dict:
    left = canonical[["ticker", "date", "adj_close"]].copy()
    right = alpaca[["ticker", "date", "alpaca_adjusted_close"]].copy()
    left["date"] = pd.to_datetime(left["date"]).dt.date
    right["date"] = pd.to_datetime(right["date"]).dt.date
    merged = left.merge(right, on=["ticker", "date"], how="inner")
    if merged.empty:
        return {"overlap_rows": 0, "median_abs_pct_error": None,
                "p95_abs_pct_error": None, "max_abs_pct_error": None}
    denom = merged["adj_close"].abs()
    err = ((merged["alpaca_adjusted_close"] - merged["adj_close"]).abs() / denom)
    err = err[denom > 0]
    return {
        "overlap_rows": int(len(err)),
        "median_abs_pct_error": float(err.median()) if len(err) else None,
        "p95_abs_pct_error": float(err.quantile(0.95)) if len(err) else None,
        "max_abs_pct_error": float(err.max()) if len(err) else None,
    }


def fetch_alpaca(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    key = os.environ["ALPACA_API_KEY_ID"]
    secret = os.environ["ALPACA_API_SECRET_KEY"]
    rows: list[dict] = []
    page_token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "adjustment": "all",
            "feed": "sip",
            "limit": 10000,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        req = Request(
            ALPACA_URL + "?" + urlencode(params),
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                     "Accept": "application/json"},
        )
        with urlopen(req, timeout=60) as response:
            payload = json.load(response)
        for ticker, bars in payload.get("bars", {}).items():
            for bar in bars:
                rows.append({
                    "ticker": ticker,
                    "date": pd.Timestamp(bar["t"]).date(),
                    "alpaca_adjusted_close": float(bar["c"]),
                })
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return pd.DataFrame(rows, columns=["ticker", "date", "alpaca_adjusted_close"])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target-date", required=True)
    p.add_argument("--sample-size", type=int, default=50)
    p.add_argument("--lookback-days", type=int, default=60)
    args = p.parse_args()

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    manifest = json.loads(s3.get_object(Bucket=bucket, Key=READY_POINTER)["Body"].read())
    raw = s3.get_object(Bucket=bucket, Key=manifest["parquet_key"])["Body"].read()
    frame = pd.read_parquet(io.BytesIO(raw), columns=["security_id", "ticker", "date", "adj_close"])
    frame["date"] = pd.to_datetime(frame["date"]).dt.date

    target = pd.Timestamp(args.target_date).date()
    before = frame[frame["date"] < target].groupby("security_id")["date"].max()
    after = frame[frame["date"] > target].groupby("security_id")["date"].min()
    present = set(frame.loc[frame["date"] == target, "security_id"].astype(str))
    eligible = sorted((set(before.index.astype(str)) & set(after.index.astype(str))) - present)

    latest_ticker = (frame.sort_values("date").groupby("security_id")["ticker"].last().astype(str))
    sample_ids = eligible[: args.sample_size]
    symbols = [latest_ticker.loc[sid] for sid in sample_ids if sid in latest_ticker.index]
    if not symbols:
        raise RuntimeError("No bounded sample found for target-date gap")

    start = (target - timedelta(days=args.lookback_days)).isoformat()
    end = (target + timedelta(days=2)).isoformat()
    alpaca = fetch_alpaca(symbols, start, end)
    canonical = frame[frame["ticker"].isin(symbols) & (frame["date"] >= pd.Timestamp(start).date())]

    metrics = parity_metrics(canonical, alpaca)
    alpaca_target = set(alpaca.loc[alpaca["date"] == target, "ticker"].astype(str))
    output = {
        "status": "READ_ONLY",
        "ready_as_of_date": manifest["as_of_date"],
        "ready_sha256": manifest["sha256"],
        "target_date": args.target_date,
        "gap_population": len(eligible),
        "sample_size": len(symbols),
        "alpaca_target_coverage": len(alpaca_target),
        "alpaca_target_coverage_pct": round(100 * len(alpaca_target) / len(symbols), 2),
        "alpaca_missing_target": sorted(set(symbols) - alpaca_target),
        "request": {"feed": "sip", "adjustment": "all", "timeframe": "1Day"},
        "parity": metrics,
    }
    print("ALPACA_PARITY_PROBE=" + json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()

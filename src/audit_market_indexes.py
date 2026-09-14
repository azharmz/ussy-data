"""Audit Yahoo/yfinance usability for CAN SLIM major-index inputs.

Diagnostics only: does not publish R2 pointers or modify benchmark histories.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

INDEXES = {
    "NASDAQ_COMPOSITE": "^IXIC",
    "SP500": "^GSPC",
    "DJIA": "^DJI",
}
RECENT_WINDOW = 60


def _flatten(raw, symbol):
    import pandas as pd
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        levels = frame.columns.get_level_values(-1)
        if symbol in levels:
            frame = frame.xs(symbol, axis=1, level=-1)
        elif symbol in frame.columns.get_level_values(0):
            frame = frame.xs(symbol, axis=1, level=0)
        else:
            raise ValueError(f"Unexpected yfinance column layout for {symbol}")
    return frame.reset_index()


def audit_symbol(index_id, symbol, out):
    import pandas as pd
    import yfinance as yf

    today = datetime.now(UTC).date()
    params = dict(
        start=(today - timedelta(days=800)).isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
        repair=False,
        prepost=False,
        progress=False,
        threads=False,
        timeout=30,
    )
    raw = yf.download(symbol, **params)
    raw.to_parquet(out / f"{index_id.lower()}-source.parquet")
    result = {
        "index_id": index_id,
        "source_provider": "yfinance/Yahoo Finance",
        "source_symbol": symbol,
        "parameters": params,
        "status": "FAIL",
        "checks": {},
    }
    if raw.empty:
        result["checks"]["nonempty"] = False
        return result
    result["checks"]["nonempty"] = True

    df = _flatten(raw, symbol)
    rename = {"Date":"date","Datetime":"date","Open":"open","High":"high","Low":"low","Close":"close","Adj Close":"adj_close","Volume":"volume"}
    df = df.rename(columns=rename)
    required = ["date","open","high","low","close","volume"]
    result["checks"]["required_columns"] = not bool(set(required) - set(df.columns))
    if not result["checks"]["required_columns"]:
        result["missing_columns"] = sorted(set(required) - set(df.columns))
        return result

    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    recent = df.tail(RECENT_WINDOW).copy()

    result["rows"] = int(len(df))
    result["sample_rows"] = int(len(recent))
    result["first_date"] = None if df["date"].isna().all() else df["date"].min().date().isoformat()
    result["last_date"] = None if df["date"].isna().all() else df["date"].max().date().isoformat()
    result["checks"]["dates_parseable"] = not df["date"].isna().any()
    result["checks"]["dates_unique"] = not df["date"].duplicated().any()
    result["checks"]["dates_monotonic"] = df["date"].is_monotonic_increasing
    result["checks"]["recent_ohlc_complete"] = not recent[["open","high","low","close"]].isna().any().any()

    envelope = (
        (recent["high"] >= recent[["open","close","low"]].max(axis=1)) &
        (recent["low"] <= recent[["open","close","high"]].min(axis=1)) &
        (recent[["open","high","low","close"]] > 0).all(axis=1)
    )
    result["checks"]["recent_ohlc_valid"] = bool(envelope.all())
    result["checks"]["recent_volume_complete"] = not recent["volume"].isna().any()
    result["checks"]["recent_volume_nonnegative"] = bool((recent["volume"].dropna() >= 0).all())
    result["checks"]["recent_volume_not_all_zero"] = bool((recent["volume"].fillna(0) > 0).any())
    result["checks"]["recent_volume_varies"] = int(recent["volume"].dropna().nunique()) >= 2
    result["volume_recent_min"] = None if recent["volume"].dropna().empty else float(recent["volume"].min())
    result["volume_recent_max"] = None if recent["volume"].dropna().empty else float(recent["volume"].max())
    result["volume_recent_unique"] = int(recent["volume"].dropna().nunique())
    result["status"] = "PASS" if all(result["checks"].values()) else "FAIL"
    return result


def main():
    out = Path("diagnostics/market-index-source-audit")
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "audit_version": "48-major-index-source-audit-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "source_candidate": "yfinance/Yahoo Finance",
        "versions": {p: version(p) for p in ("yfinance", "pandas", "pyarrow")},
        "results": [],
    }
    try:
        for index_id, symbol in INDEXES.items():
            report["results"].append(audit_symbol(index_id, symbol, out))
        report["overall_status"] = "PASS" if all(r["status"] == "PASS" for r in report["results"]) else "FAIL"
        report["finished_at"] = datetime.now(UTC).isoformat()
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["overall_status"] == "PASS" else 1
    except Exception as exc:
        report.update(overall_status="ERROR", error_type=type(exc).__name__, error=str(exc), finished_at=datetime.now(UTC).isoformat())
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

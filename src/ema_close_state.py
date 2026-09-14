from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

PERIODS = (20, 50, 150, 200)
PRICE_BASIS = "close"
STATE_COLUMNS = ["security_id", "ticker", "as_of_date", "last_price", "ema20", "ema50", "ema150", "ema200"]
MIN_BOOTSTRAP_BARS = max(PERIODS)


class StateNeedsRebuild(ValueError):
    pass


def _clean_prices(frame: pd.DataFrame, security_id: str | None = None) -> pd.DataFrame:
    required = {"date", "security_id", "ticker", PRICE_BASIS}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"EMA close source lacks columns: {sorted(missing)}")
    data = frame[["date", "security_id", "ticker", PRICE_BASIS]].copy()
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    data[PRICE_BASIS] = pd.to_numeric(data[PRICE_BASIS], errors="raise").astype("float64")
    if data["date"].isna().any() or data[PRICE_BASIS].isna().any():
        raise ValueError("EMA close source has null date/price")
    if not np.isfinite(data[PRICE_BASIS]).all() or (data[PRICE_BASIS] <= 0).any():
        raise ValueError("EMA close source price must be finite and positive")
    if data.duplicated(["security_id", "date"]).any():
        raise ValueError("EMA close source has duplicate security/date rows")
    ids = {str(v) for v in data["security_id"]}
    if security_id is not None and ids != {str(security_id)}:
        raise ValueError("EMA close source security_id mismatch")
    if len(ids) != 1:
        raise ValueError("EMA close source must contain exactly one security_id")
    return data.sort_values("date").reset_index(drop=True)


def ema_values(prices: Iterable[float]) -> dict[int, float]:
    values = [float(v) for v in prices]
    if not values:
        raise ValueError("Cannot compute EMA from empty prices")
    state = {period: values[0] for period in PERIODS}
    for price in values[1:]:
        for period in PERIODS:
            alpha = 2.0 / (period + 1.0)
            state[period] = alpha * price + (1.0 - alpha) * state[period]
    return state


def bootstrap_state(frame: pd.DataFrame, security_id: str | None = None) -> dict[str, object]:
    data = _clean_prices(frame, security_id)
    if len(data) < MIN_BOOTSTRAP_BARS:
        raise ValueError(f"EMA close bootstrap requires at least {MIN_BOOTSTRAP_BARS} bars")
    values = ema_values(data[PRICE_BASIS].tolist())
    last = data.iloc[-1]
    row = {
        "security_id": str(last["security_id"]),
        "ticker": str(last["ticker"]),
        "as_of_date": last["date"],
        "last_price": float(last[PRICE_BASIS]),
    }
    row.update({f"ema{period}": float(values[period]) for period in PERIODS})
    return row


def validate_state_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(STATE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"EMA close state lacks columns: {sorted(missing)}")
    state = frame[STATE_COLUMNS].copy()
    if state.empty:
        raise ValueError("EMA close state is empty")
    state["security_id"] = state["security_id"].astype(str)
    state["ticker"] = state["ticker"].astype(str)
    state["as_of_date"] = pd.to_datetime(state["as_of_date"], errors="raise").dt.tz_localize(None).dt.normalize()
    numeric = ["last_price", *(f"ema{p}" for p in PERIODS)]
    for column in numeric:
        state[column] = pd.to_numeric(state[column], errors="raise").astype("float64")
    if state["as_of_date"].isna().any() or state.duplicated("security_id").any():
        raise ValueError("EMA close state has invalid dates or duplicate security_id")
    if not np.isfinite(state[numeric].to_numpy()).all() or (state[numeric] <= 0).any().any():
        raise ValueError("EMA close state contains invalid numeric values")
    return state.sort_values("security_id").reset_index(drop=True)


def advance_state(previous, ready_rows: pd.DataFrame):
    sid = str(previous["security_id"])
    data = _clean_prices(ready_rows, sid)
    previous_date = pd.Timestamp(previous["as_of_date"]).tz_localize(None).normalize()
    matches = data.loc[data["date"] == previous_date]
    if len(matches) != 1:
        raise StateNeedsRebuild("Persisted EMA close date outside current ready window")
    if not np.isclose(float(previous["last_price"]), float(matches[PRICE_BASIS].iloc[0]), rtol=1e-10, atol=1e-10):
        raise StateNeedsRebuild("Persisted EMA close last_price disagrees with ready source")
    values = {p: float(previous[f"ema{p}"]) for p in PERIODS}
    newer = data.loc[data["date"] > previous_date]
    for price in newer[PRICE_BASIS].tolist():
        for period in PERIODS:
            alpha = 2.0 / (period + 1.0)
            values[period] = alpha * float(price) + (1.0 - alpha) * values[period]
    latest = data.iloc[-1]
    row = {
        "security_id": sid,
        "ticker": str(latest["ticker"]),
        "as_of_date": latest["date"],
        "last_price": float(latest[PRICE_BASIS]),
    }
    row.update({f"ema{p}": float(values[p]) for p in PERIODS})
    return row, len(newer)


def classify_trend(row) -> str:
    price = float(row["last_price"])
    e20, e50, e150, e200 = (float(row[f"ema{p}"]) for p in PERIODS)
    if price > e20 > e50 > e150 > e200:
        return "STACKED_UP"
    if price < e20 < e50 < e150 < e200:
        return "STACKED_DOWN"
    return "MIXED"

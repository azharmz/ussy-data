from src.audit_broad_market_tiingo import deterministic_sample, eligible_symbols, evaluate_rows

import pandas as pd


def test_eligible_symbols_excludes_etf_and_test_issue_only():
    frame = pd.DataFrame([
        {"symbol": "AAPL", "is_etf": "N", "test_issue": "N"},
        {"symbol": "SPY", "is_etf": "Y", "test_issue": "N"},
        {"symbol": "TEST", "is_etf": "N", "test_issue": "Y"},
        {"symbol": " brk.b ", "is_etf": None, "test_issue": None},
    ])
    assert eligible_symbols(frame) == ["AAPL", "BRK.B"]


def test_deterministic_sample_is_reproducible_and_bounded():
    symbols = ["A", "B", "C", "D"]
    first = deterministic_sample(symbols, 3, "run-1")
    second = deterministic_sample(list(reversed(symbols)), 3, "run-1")
    assert first == second
    assert len(first) == 3
    assert set(first).issubset(set(symbols))


def test_deterministic_sample_returns_all_when_small_population():
    assert set(deterministic_sample(["A", "B"], 100, "seed")) == {"A", "B"}


def test_evaluate_rows_requires_252_positive_adjusted_close_rows():
    rows = [
        {"date": f"2026-01-{(i % 28) + 1:02d}", "adj_close": 10 + i}
        for i in range(252)
    ]
    out = evaluate_rows(rows)
    assert out["adjusted_close_rows"] == 252
    assert out["has_252_adjusted_close_bars"] is True


def test_evaluate_rows_preserves_insufficient_history():
    rows = [{"date": "2026-01-01", "adj_close": 10.0}] * 251
    out = evaluate_rows(rows)
    assert out["adjusted_close_rows"] == 251
    assert out["has_252_adjusted_close_bars"] is False

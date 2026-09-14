from src.publish_broad_market_membership import normalize_membership


def test_normalize_membership_preserves_both_sources_and_provenance():
    nasdaq = (
        b"Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
        b"AAPL|Apple Inc. - Common Stock|Q|N|N|40|N|N\n"
        b"QQQ|Invesco QQQ Trust|G|N|N|100|Y|N\n"
        b"File Creation Time: 0914202620:00|||||||\n"
    )
    other = (
        b"ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        b"IBM|International Business Machines Corporation Common Stock|N|IBM|N|100|N|IBM\n"
        b"SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
        b"File Creation Time: 0914202620:01|||||||\n"
    )
    frame, meta = normalize_membership(nasdaq, other, "2026-09-14T20:05:00+00:00")
    assert set(frame["symbol"]) == {"AAPL", "QQQ", "IBM", "SPY"}
    assert set(frame["source_file"]) == {"nasdaqlisted.txt", "otherlisted.txt"}
    assert set(frame["source_provider"]) == {"NASDAQ_TRADER_SYMBOL_DIRECTORY"}
    assert set(frame["source_contract_version"]) == {"53-broad-market-membership-publisher-v1"}
    assert meta["combined_rows"] == 4
    assert meta["nasdaqlisted_file_creation_time"] == "0914202620:00"


def test_normalize_membership_keeps_etf_and_test_flags_instead_of_silently_filtering():
    nasdaq = (
        b"Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
        b"TEST|Test Security|S|Y|N|100|N|N\n"
        b"File Creation Time: 0914202620:00|||||||\n"
    )
    other = (
        b"ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        b"SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
        b"File Creation Time: 0914202620:01|||||||\n"
    )
    frame, _ = normalize_membership(nasdaq, other, "2026-09-14T20:05:00+00:00")
    test = frame.loc[frame["symbol"].eq("TEST")].iloc[0]
    spy = frame.loc[frame["symbol"].eq("SPY")].iloc[0]
    assert test["test_issue"] == "Y"
    assert spy["is_etf"] == "Y"


def test_normalize_membership_rejects_duplicate_symbol_within_same_source():
    nasdaq = (
        b"Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
        b"AAPL|Apple Inc.|Q|N|N|40|N|N\n"
        b"AAPL|Apple Inc.|Q|N|N|40|N|N\n"
        b"File Creation Time: 0914202620:00|||||||\n"
    )
    other = (
        b"ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        b"IBM|IBM|N|IBM|N|100|N|IBM\n"
        b"File Creation Time: 0914202620:01|||||||\n"
    )
    try:
        normalize_membership(nasdaq, other, "2026-09-14T20:05:00+00:00")
    except ValueError as exc:
        assert "Duplicate symbol" in str(exc)
    else:
        raise AssertionError("expected duplicate rejection")

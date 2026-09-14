import pandas as pd

from src.audit_broad_market_provider_stack import yahoo_market_symbol


def test_yahoo_symbol_maps_dot_share_class():
    assert yahoo_market_symbol("brk.b") == "BRK-B"


def test_yahoo_symbol_preserves_simple_ticker():
    assert yahoo_market_symbol("aapl") == "AAPL"

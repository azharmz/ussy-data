import pandas as pd

from src.audit_broad_market_provider_stack import yahoo_symbol


def test_yahoo_symbol_maps_dot_share_class():
    assert yahoo_symbol("brk.b") == "BRK-B"


def test_yahoo_symbol_preserves_simple_ticker():
    assert yahoo_symbol("aapl") == "AAPL"

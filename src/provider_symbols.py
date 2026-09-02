"""Reviewed provider aliases; never mutate membership, compliance, or security IDs."""

# Keyed by BOTH source security ID and ticker to prevent reused-ticker collisions.
YAHOO_ALIASES = {
    ('US60744M1062', 'MBGL WI'): 'MBGL',
    ('US20459V1052', 'CMPO'): 'GPGI',
    ('US74624M1027', 'PSTG'): 'P',
    ('US8248891090', 'SCVL'): 'SHOE',
    ('US9118053076', 'USEG'): 'BSIN',
}


def yahoo_symbol(ticker: str, security_id: str | None = None) -> str:
    source = ticker.strip().upper()
    return YAHOO_ALIASES.get((str(security_id), source), source.replace('.', '-'))

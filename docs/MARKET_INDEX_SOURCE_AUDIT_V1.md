# Major Index Source Audit v1

Date: 2026-09-14
Status: PREREGISTERED BEFORE AUDIT RUN
Consumer contract: `azharmz/ussy-canslim-research` #47 `47-market-input-data-contract-v1`

## Purpose

Audit whether Yahoo/yfinance can serve as a reviewed production source candidate for the three frozen #47 canonical major-index identities:

- `NASDAQ_COMPOSITE` -> candidate Yahoo symbol `^IXIC`
- `SP500` -> candidate Yahoo symbol `^GSPC`
- `DJIA` -> candidate Yahoo symbol `^DJI`

This audit does not publish production pointers and does not modify existing SPY/QQQ benchmark data.

## Pass criteria per index

A candidate symbol passes only when all of the following are true on a fresh retrieval:

1. returned dataset is non-empty;
2. source identity/symbol is exactly the preregistered mapping;
3. daily OHLC columns are present and valid;
4. dates are parseable, unique and strictly increasing after normalization;
5. a recent sample window has no missing OHLC;
6. volume exists as an explicit source field;
7. recent completed sessions do not have missing or negative volume;
8. recent volume is not uniformly zero;
9. at least two recent completed sessions have distinct volume values, establishing that volume is not a constant placeholder;
10. last completed source date is recorded for freshness review;
11. yfinance/pandas versions and fetch parameters are recorded.

## Audit horizon

The audit requests approximately two years of daily history through the current date, then evaluates the most recent 60 returned completed rows for source usability. The full downloaded source frame is retained in the workflow artifact for review.

The 60-row window is an audit sample size only, not a CAN SLIM/O'Neil threshold.

## Important boundary

Passing this audit means Yahoo/yfinance is technically usable as the initial source for the declared index/symbol pair under the tested retrieval semantics. It does not prove Yahoo is an exchange-authoritative data vendor and does not authorize ETF substitution.

If any index fails, that index remains NOT_EVALUABLE for production #46 until an alternative provider/index pair is independently audited.

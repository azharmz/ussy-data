-- USSY web serving schema (D1 PoC)
-- R2 remains authoritative. D1 is a rebuildable read/query index.

CREATE TABLE IF NOT EXISTS securities (
  security_id TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  company_name TEXT,
  sharia_compliance TEXT NOT NULL DEFAULT 'COMPLIANT',
  universe_snapshot_date TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_securities_ticker ON securities(ticker);
CREATE INDEX IF NOT EXISTS idx_securities_compliance ON securities(sharia_compliance);

CREATE TABLE IF NOT EXISTS fundamentals_current (
  security_id TEXT PRIMARY KEY REFERENCES securities(security_id) ON DELETE CASCADE,
  ticker TEXT NOT NULL,
  status TEXT,
  as_of_date TEXT,
  accepted_at TEXT,
  fiscal_period_end TEXT,
  eps_yoy_latest REAL,
  eps_yoy_prior REAL,
  revenue_yoy_latest REAL,
  revenue_yoy_prior REAL,
  annual_eps_growth REAL,
  source_key TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fund_ticker ON fundamentals_current(ticker);
CREATE INDEX IF NOT EXISTS idx_fund_status ON fundamentals_current(status);
CREATE INDEX IF NOT EXISTS idx_fund_eps_yoy ON fundamentals_current(eps_yoy_latest);
CREATE INDEX IF NOT EXISTS idx_fund_rev_yoy ON fundamentals_current(revenue_yoy_latest);
CREATE INDEX IF NOT EXISTS idx_fund_annual_growth ON fundamentals_current(annual_eps_growth);

CREATE VIEW IF NOT EXISTS stock_explorer AS
SELECT
  s.security_id, s.ticker, s.company_name, s.sharia_compliance,
  s.universe_snapshot_date,
  f.status AS fundamental_status, f.as_of_date AS fundamental_as_of,
  f.accepted_at, f.fiscal_period_end,
  f.eps_yoy_latest, f.eps_yoy_prior,
  f.revenue_yoy_latest, f.revenue_yoy_prior,
  f.annual_eps_growth
FROM securities s
LEFT JOIN fundamentals_current f ON f.security_id = s.security_id;

# USSY Web Serving PoC

Scope is deliberately narrow:

- **Universe** from `ussy-data`
- **Fundamentals** from `ussy-fundamentals`
- **R2 storage monitor**
- Cloudflare D1 is a **rebuildable serving/index database**, not source of truth.

## Bindings

Configure the Cloudflare deployment with:

- D1 binding: `DB`
- R2 binding: `R2_BUCKET` -> existing `ussy-data` bucket
- Static assets: existing `web/` assets if deployed as a Worker/Pages Worker

## D1 schema

Apply `web/d1/schema.sql`.

The serving model intentionally keeps universe and fundamentals separate and exposes a joined `stock_explorer` view keyed by `security_id`.

## API

`GET /api/stocks`

Supported parameters:

- `q`
- `compliance`
- `fundamental_status`
- `eps_yoy_min`, `eps_yoy_max`
- `revenue_yoy_min`, `revenue_yoy_max`
- `annual_growth_min`, `annual_growth_max`
- `sort=ticker|company_name|eps_yoy_latest|revenue_yoy_latest|annual_eps_growth|fundamental_as_of`
- `order=asc|desc`
- `page`, `limit` (max 200)

Example:

`/api/stocks?compliance=COMPLIANT&eps_yoy_min=25&revenue_yoy_min=20&sort=eps_yoy_latest&order=desc`

`GET /api/r2-usage` returns read-only live bucket totals and top-level prefix breakdown.

## Data loading

No production producer is changed by this commit. The next stage is to add an idempotent sync that reads the existing authoritative R2 universe/fundamental artifacts and upserts only the current serving rows into D1. It must preserve PIT/as-of lineage and fail closed if the expected source contracts are missing.

## Safety

Do not expose R2 credentials to the browser. Browser requests only hit Worker endpoints. All filter values are bound parameters; sortable columns are allow-listed.

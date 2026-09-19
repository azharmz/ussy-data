const SORT_COLUMNS = new Set([
  "ticker", "company_name", "eps_yoy_latest", "revenue_yoy_latest",
  "annual_eps_growth", "fundamental_as_of"
]);

function json(data, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

function num(v) {
  if (v === null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

async function getR2Json(bucket, key) {
  const object = await bucket.get(key);
  if (!object) throw new Error(`Required R2 object not found: ${key}`);
  return object.json();
}

function snapshotDate(current) {
  const value = current.snapshot_date || current.active_snapshot_date || current.date;
  if (!value) throw new Error("universe/current.json has no snapshot date");
  return String(value);
}

async function ensureRefreshTable(db) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS serving_refresh_state (
      dataset TEXT PRIMARY KEY,
      source_key TEXT NOT NULL,
      source_as_of TEXT,
      row_count INTEGER NOT NULL,
      refreshed_at TEXT NOT NULL,
      status TEXT NOT NULL
    )
  `).run();
}

async function syncUniverseIfNeeded(env) {
  if (!env.DB) throw new Error("DB binding is not configured");
  if (!env.R2_BUCKET) throw new Error("R2_BUCKET binding is not configured");

  await ensureRefreshTable(env.DB);
  const current = await getR2Json(env.R2_BUCKET, "universe/current.json");
  const date = snapshotDate(current);
  const sourceKey = `universe/membership/${date}.json`;

  const state = await env.DB.prepare(
    "SELECT source_as_of, status FROM serving_refresh_state WHERE dataset = ?"
  ).bind("universe").first();

  if (state?.source_as_of === date && state?.status === "READY") {
    return { changed: false, snapshot_date: date };
  }

  const membership = await getR2Json(env.R2_BUCKET, sourceKey);
  if (!Array.isArray(membership.records)) {
    throw new Error(`${sourceKey} does not contain records[]`);
  }
  if (String(membership.snapshot_date || date) !== date) {
    throw new Error("Universe pointer and membership snapshot date disagree");
  }

  const records = membership.records.filter(r => r?.security_id && r?.ticker);
  if (!records.length) throw new Error("Universe membership contains no usable records");

  const seen = new Set();
  for (const r of records) {
    if (seen.has(String(r.security_id))) throw new Error(`Duplicate security_id: ${r.security_id}`);
    seen.add(String(r.security_id));
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO serving_refresh_state(dataset, source_key, source_as_of, row_count, refreshed_at, status)
     VALUES (?, ?, ?, ?, ?, 'SYNCING')
     ON CONFLICT(dataset) DO UPDATE SET
       source_key=excluded.source_key, source_as_of=excluded.source_as_of,
       row_count=excluded.row_count, refreshed_at=excluded.refreshed_at, status='SYNCING'`
  ).bind("universe", sourceKey, date, records.length, now).run();

  const statements = records.map(r => env.DB.prepare(
    `INSERT INTO securities(
       security_id, ticker, company_name, sharia_compliance, universe_snapshot_date, updated_at
     ) VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(security_id) DO UPDATE SET
       ticker=excluded.ticker,
       company_name=excluded.company_name,
       sharia_compliance=excluded.sharia_compliance,
       universe_snapshot_date=excluded.universe_snapshot_date,
       updated_at=excluded.updated_at`
  ).bind(
    String(r.security_id),
    String(r.ticker).toUpperCase(),
    r.name == null ? null : String(r.name),
    String(r.sharia_compliance || r.musaffaHalalRating || "UNKNOWN").toUpperCase(),
    date,
    now
  ));

  for (let i = 0; i < statements.length; i += 100) {
    await env.DB.batch(statements.slice(i, i + 100));
  }

  // Remove stale universe rows only after the new snapshot has been fully upserted.
  // Fundamentals cascade for removed securities by the schema contract.
  await env.DB.prepare(
    "DELETE FROM securities WHERE universe_snapshot_date <> ?"
  ).bind(date).run();

  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM securities").first();
  if (Number(count?.n || 0) !== records.length) {
    await env.DB.prepare(
      "UPDATE serving_refresh_state SET status='FAILED', refreshed_at=? WHERE dataset='universe'"
    ).bind(new Date().toISOString()).run();
    throw new Error(`Universe row-count verification failed: expected ${records.length}, got ${count?.n || 0}`);
  }

  await env.DB.prepare(
    `UPDATE serving_refresh_state
     SET row_count=?, refreshed_at=?, status='READY'
     WHERE dataset='universe'`
  ).bind(records.length, new Date().toISOString()).run();

  return { changed: true, snapshot_date: date, row_count: records.length, source_key: sourceKey };
}

async function stockExplorer(url, env) {
  if (!env.DB) return json({ error: "DB binding is not configured" }, 503);
  try {
    await syncUniverseIfNeeded(env);
  } catch (error) {
    return json({ error: "Universe serving sync failed", detail: String(error?.message || error) }, 503);
  }

  const p = url.searchParams;
  const where = [], binds = [];
  const q = (p.get("q") || "").trim();
  if (q) {
    where.push("(ticker LIKE ? OR company_name LIKE ?)");
    binds.push(q.toUpperCase() + "%", "%" + q + "%");
  }
  const compliance = p.get("compliance");
  if (compliance) { where.push("sharia_compliance = ?"); binds.push(compliance.toUpperCase()); }
  const status = p.get("fundamental_status");
  if (status) { where.push("fundamental_status = ?"); binds.push(status); }

  for (const [param, col, op] of [
    ["eps_yoy_min","eps_yoy_latest",">="], ["eps_yoy_max","eps_yoy_latest","<="],
    ["revenue_yoy_min","revenue_yoy_latest",">="], ["revenue_yoy_max","revenue_yoy_latest","<="],
    ["annual_growth_min","annual_eps_growth",">="], ["annual_growth_max","annual_eps_growth","<="]
  ]) {
    const value = num(p.get(param));
    if (value !== null) { where.push(`${col} ${op} ?`); binds.push(value); }
  }

  const sort = SORT_COLUMNS.has(p.get("sort")) ? p.get("sort") : "ticker";
  const order = p.get("order") === "desc" ? "DESC" : "ASC";
  const limit = Math.min(Math.max(Number(p.get("limit")) || 50, 1), 200);
  const page = Math.max(Number(p.get("page")) || 1, 1);
  const offset = (page - 1) * limit;
  const clause = where.length ? " WHERE " + where.join(" AND ") : "";

  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM stock_explorer" + clause).bind(...binds).first();
  const sql = `SELECT * FROM stock_explorer${clause} ORDER BY ${sort} ${order}, ticker ASC LIMIT ? OFFSET ?`;
  const rows = await env.DB.prepare(sql).bind(...binds, limit, offset).all();
  return json({ total: count?.n || 0, page, limit, sort, order: order.toLowerCase(), rows: rows.results || [] });
}

async function servingStatus(env) {
  if (!env.DB) return json({ error: "DB binding is not configured" }, 503);
  try {
    const sync = await syncUniverseIfNeeded(env);
    const state = await env.DB.prepare(
      "SELECT * FROM serving_refresh_state WHERE dataset='universe'"
    ).first();
    return json({ ok: true, universe: state, sync });
  } catch (error) {
    return json({ ok: false, error: String(error?.message || error) }, 503);
  }
}

async function r2Usage(env) {
  const bucket = env.R2_BUCKET;
  if (!bucket || typeof bucket.list !== "function") {
    return json({ error: "R2_BUCKET binding is not configured" }, 503);
  }
  let cursor, total_objects = 0, total_bytes = 0;
  const prefixes = new Map();
  do {
    const page = await bucket.list({ limit: 1000, ...(cursor ? { cursor } : {}) });
    for (const o of page.objects) {
      total_objects++;
      total_bytes += o.size;
      const top = o.key.includes("/") ? o.key.split("/")[0] + "/" : "(root)";
      const x = prefixes.get(top) || { prefix: top, objects: 0, bytes: 0 };
      x.objects++;
      x.bytes += o.size;
      prefixes.set(top, x);
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return json({
    total_objects,
    total_bytes,
    total_gib: total_bytes / (1024 ** 3),
    checked_at: new Date().toISOString(),
    prefixes: [...prefixes.values()].sort((a, b) => b.bytes - a.bytes)
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET") return new Response("Method Not Allowed", { status: 405 });
    if (url.pathname === "/api/stocks") return stockExplorer(url, env);
    if (url.pathname === "/api/serving-status") return servingStatus(env);
    if (url.pathname === "/api/r2-usage") return r2Usage(env);
    return env.ASSETS ? env.ASSETS.fetch(request) : new Response("Not Found", { status: 404 });
  }
};

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
    String(r.sharia_compliance || "UNKNOWN").toUpperCase(),
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

async function syncFundamentalsIfNeeded(env) {
  await ensureRefreshTable(env.DB);
  const pointer = await getR2Json(env.R2_BUCKET, "fundamentals/current.json");
  if (pointer.status !== "READY") throw new Error("fundamentals/current.json is not READY");
  const sourceKey = pointer.serving_current_key;
  if (!sourceKey) throw new Error("fundamentals/current.json has no serving_current_key");

  const state = await env.DB.prepare(
    "SELECT source_key, status FROM serving_refresh_state WHERE dataset = ?"
  ).bind("fundamentals").first();
  if (state?.source_key === sourceKey && state?.status === "READY") {
    return { changed: false, source_key: sourceKey };
  }

  const payload = await getR2Json(env.R2_BUCKET, sourceKey);
  if (!Array.isArray(payload.records)) throw new Error(sourceKey + " does not contain records[]");
  if (Number(payload.row_count) !== payload.records.length) {
    throw new Error("Fundamentals projection row_count mismatch");
  }

  const universeState = await env.DB.prepare(
    "SELECT source_as_of, status FROM serving_refresh_state WHERE dataset='universe'"
  ).first();
  if (universeState?.status !== "READY") throw new Error("Universe serving state is not READY");
  if (payload.universe_snapshot_date &&
      String(payload.universe_snapshot_date) !== String(universeState.source_as_of)) {
    throw new Error("Fundamentals and Universe snapshot dates disagree");
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO serving_refresh_state(dataset, source_key, source_as_of, row_count, refreshed_at, status)
     VALUES (?, ?, ?, ?, ?, 'SYNCING')
     ON CONFLICT(dataset) DO UPDATE SET source_key=excluded.source_key,
       source_as_of=excluded.source_as_of, row_count=excluded.row_count,
       refreshed_at=excluded.refreshed_at, status='SYNCING'`
  ).bind("fundamentals", sourceKey, pointer.snapshot_date || null, payload.records.length, now).run();

  const statements = payload.records.map(r => env.DB.prepare(
    `INSERT INTO fundamentals_current(
       security_id,ticker,status,as_of_date,accepted_at,fiscal_period_end,
       eps_yoy_latest,eps_yoy_prior,revenue_yoy_latest,revenue_yoy_prior,
       annual_eps_growth,source_key,updated_at
     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
     ON CONFLICT(security_id) DO UPDATE SET
       ticker=excluded.ticker,status=excluded.status,as_of_date=excluded.as_of_date,
       accepted_at=excluded.accepted_at,fiscal_period_end=excluded.fiscal_period_end,
       eps_yoy_latest=excluded.eps_yoy_latest,eps_yoy_prior=excluded.eps_yoy_prior,
       revenue_yoy_latest=excluded.revenue_yoy_latest,revenue_yoy_prior=excluded.revenue_yoy_prior,
       annual_eps_growth=excluded.annual_eps_growth,source_key=excluded.source_key,
       updated_at=excluded.updated_at`
  ).bind(
    String(r.security_id), String(r.ticker).toUpperCase(), r.status ?? null,
    r.as_of_date ?? null, r.accepted_at ?? null, r.fiscal_period_end ?? null,
    r.eps_yoy_latest ?? null, r.eps_yoy_prior ?? null,
    r.revenue_yoy_latest ?? null, r.revenue_yoy_prior ?? null,
    r.annual_eps_growth ?? null, sourceKey, now
  ));

  for (let i = 0; i < statements.length; i += 100) {
    await env.DB.batch(statements.slice(i, i + 100));
  }
  await env.DB.prepare("DELETE FROM fundamentals_current WHERE source_key <> ?").bind(sourceKey).run();

  const count = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM fundamentals_current WHERE source_key = ?"
  ).bind(sourceKey).first();
  if (Number(count?.n || 0) !== payload.records.length) {
    await env.DB.prepare(
      "UPDATE serving_refresh_state SET status='FAILED', refreshed_at=? WHERE dataset='fundamentals'"
    ).bind(new Date().toISOString()).run();
    throw new Error(`Fundamentals row-count verification failed: expected ${payload.records.length}, got ${count?.n || 0}`);
  }

  await env.DB.prepare(
    "UPDATE serving_refresh_state SET row_count=?, refreshed_at=?, status='READY' WHERE dataset='fundamentals'"
  ).bind(payload.records.length, new Date().toISOString()).run();
  return { changed: true, source_key: sourceKey, row_count: payload.records.length };
}


async function ensureDashboardTables(db) {
  await db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS dashboard_status (
      id INTEGER PRIMARY KEY CHECK (id=1), snapshot_date TEXT NOT NULL,
      pipeline_status TEXT NOT NULL, generated_at TEXT NOT NULL,
      universe_json TEXT NOT NULL, changes_json TEXT NOT NULL, freshness_json TEXT NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS dashboard_review_queue (
      security_id TEXT NOT NULL, ticker TEXT, category TEXT NOT NULL, reason TEXT,
      screening_updated_at TEXT, recorded_earnings_date TEXT, last_market_date TEXT,
      PRIMARY KEY (security_id, category)
    )`)
  ]);
}

async function syncDashboardIfNeeded(env) {
  await ensureRefreshTable(env.DB);
  await ensureDashboardTables(env.DB);
  const current = await getR2Json(env.R2_BUCKET, "universe/current.json");
  const date = snapshotDate(current);
  const membershipKey = `universe/membership/${date}.json`;
  const changesKey = `universe/changes/${date}.json`;
  const readinessKey = "production/rolling/readiness.json";
  const freshnessKey = "universe/freshness/latest.json";
  const heads = await Promise.all([
    env.R2_BUCKET.head(readinessKey), env.R2_BUCKET.head(freshnessKey), env.R2_BUCKET.head(changesKey)
  ]);
  if (!heads[0]) throw new Error("Required R2 object not found: " + readinessKey);
  const sourceKey = [date, ...heads.map(x => x?.etag || "missing")].join(":");
  const state = await env.DB.prepare(
    "SELECT source_key,status FROM serving_refresh_state WHERE dataset='dashboard'"
  ).first();
  if (state?.source_key === sourceKey && state?.status === "READY") return {changed:false,snapshot_date:date};

  const [membership, readiness, changes] = await Promise.all([
    getR2Json(env.R2_BUCKET, membershipKey),
    getR2Json(env.R2_BUCKET, readinessKey),
    heads[2] ? getR2Json(env.R2_BUCKET, changesKey) : Promise.resolve({}),
  ]);
  let freshness = null;
  if (heads[1]) {
    const candidate = await getR2Json(env.R2_BUCKET, freshnessKey);
    if (String(candidate.snapshot_date || "") === date) freshness = candidate;
  }
  if (String(readiness.snapshot_date || "") !== date) throw new Error("Readiness and active universe snapshot disagree");

  const members = Array.isArray(membership.records) ? membership.records : [];
  const tickerById = new Map(members.filter(x=>x?.security_id).map(x=>[String(x.security_id),x.ticker||null]));
  const secs = Array.isArray(readiness.securities) ? readiness.securities : [];
  const secById = new Map(secs.filter(x=>x?.security_id).map(x=>[String(x.security_id),x]));
  const queue = [];
  if (freshness && Array.isArray(freshness.records)) {
    for (const r of freshness.records) {
      const category=String(r.freshness_status||"").toUpperCase();
      if (!["POTENTIALLY_STALE","UNKNOWN"].includes(category)) continue;
      const id=String(r.security_id||""); const sec=secById.get(id)||{};
      queue.push({security_id:id,ticker:r.ticker||tickerById.get(id),category,reason:r.reason||null,
        screening_updated_at:r.screening_updated_at||r.screening_updated_date||null,
        recorded_earnings_date:r.recorded_earnings_date||null,last_market_date:sec.last_date||null});
    }
  }
  // Review categories are source-native production states, not the retired web/status.json taxonomy.
  // Freshness uses freshness_status verbatim; readiness list names/update_status define readiness categories.
  const addIds=(ids,category,reasonFn)=>{
    for(const raw of (Array.isArray(ids)?ids:[])){const id=String(raw),sec=secById.get(id)||{};
      queue.push({security_id:id,ticker:sec.ticker||tickerById.get(id),category,
        reason:typeof reasonFn==="function"?reasonFn(sec):reasonFn,
        screening_updated_at:null,recorded_earnings_date:null,last_market_date:sec.last_date||null});}
  };
  addIds(readiness.insufficient_history_security_ids,"INSUFFICIENT_HISTORY",sec =>
    `${sec.rolling_bars??"?"} of ${readiness.minimum_ready_bars} minimum bars (${readiness.rolling_bars_target} target)`);
  addIds(readiness.reviewed_no_retry_security_ids,"REVIEWED_NO_RETRY",null);
  addIds(readiness.data_unavailable_security_ids,"DATA_UNAVAILABLE",null);
  for(const sec of secs){const st=String(sec.update_status||"");
    if(st==="lifecycle_excluded") queue.push({security_id:String(sec.security_id),ticker:sec.ticker,category:"LIFECYCLE_EXCLUDED",reason:sec.error||null,screening_updated_at:null,recorded_earnings_date:null,last_market_date:sec.last_date||null});
    if(st==="stale_after_failure") queue.push({security_id:String(sec.security_id),ticker:sec.ticker,category:"STALE_AFTER_FAILURE",reason:sec.error||null,screening_updated_at:null,recorded_earnings_date:null,last_market_date:sec.last_date||null});
  }
  const universe={
    confirmed_compliant:readiness.confirmed_compliant,included_in_rolling:readiness.included_in_rolling,
    ready:readiness.ready,insufficient_history:readiness.insufficient_history,
    reviewed_no_retry:readiness.reviewed_no_retry,data_unavailable:readiness.data_unavailable,
    rolling_rows:readiness.rolling_rows,rolling_bars_target:readiness.rolling_bars_target,
    minimum_ready_bars:readiness.minimum_ready_bars
  };
  const freshnessSummary=freshness?{
    available:true,post_earnings_refreshed:freshness.post_earnings_refreshed,
    awaiting_next_earnings:freshness.awaiting_next_earnings,potentially_stale:freshness.potentially_stale,
    unknown:freshness.unknown,created_at:freshness.created_at,
    live_earnings_verified:freshness.live_earnings_verified||false
  }:{available:false,post_earnings_refreshed:null,awaiting_next_earnings:null,potentially_stale:null,unknown:null};
  const pipelineStatus=Number(readiness.update_failures||0)>0?"DEGRADED":"OPERATIONAL";
  const now=new Date().toISOString();
  await env.DB.prepare(`INSERT INTO serving_refresh_state(dataset,source_key,source_as_of,row_count,refreshed_at,status)
    VALUES('dashboard',?,?,?,?, 'SYNCING')
    ON CONFLICT(dataset) DO UPDATE SET source_key=excluded.source_key,source_as_of=excluded.source_as_of,row_count=excluded.row_count,refreshed_at=excluded.refreshed_at,status='SYNCING'`)
    .bind(sourceKey,date,queue.length,now).run();
  await env.DB.prepare("DELETE FROM dashboard_review_queue").run();
  const stmts=queue.map(r=>env.DB.prepare(`INSERT OR REPLACE INTO dashboard_review_queue
    (security_id,ticker,category,reason,screening_updated_at,recorded_earnings_date,last_market_date)
    VALUES(?,?,?,?,?,?,?)`).bind(r.security_id,r.ticker??null,r.category,r.reason??null,r.screening_updated_at??null,r.recorded_earnings_date??null,r.last_market_date??null));
  for(let i=0;i<stmts.length;i+=100) await env.DB.batch(stmts.slice(i,i+100));
  await env.DB.prepare(`INSERT OR REPLACE INTO dashboard_status
    (id,snapshot_date,pipeline_status,generated_at,universe_json,changes_json,freshness_json)
    VALUES(1,?,?,?,?,?,?)`).bind(date,pipelineStatus,now,JSON.stringify(universe),JSON.stringify({
      added_count:(changes.added||[]).length,removed_count:(changes.removed||[]).length,
      ticker_changes_count:(changes.ticker_changes||[]).length,previous_snapshot_date:changes.previous_snapshot_date||null
    }),JSON.stringify(freshnessSummary)).run();
  await env.DB.prepare("UPDATE serving_refresh_state SET status='READY',refreshed_at=? WHERE dataset='dashboard'").bind(new Date().toISOString()).run();
  return {changed:true,snapshot_date:date,row_count:queue.length};
}

async function dashboardStatus(env) {
  try {
    await syncUniverseIfNeeded(env);
    await syncDashboardIfNeeded(env);
    const s=await env.DB.prepare("SELECT * FROM dashboard_status WHERE id=1").first();
    const q=await env.DB.prepare("SELECT * FROM dashboard_review_queue ORDER BY category,ticker,security_id").all();
    return json({snapshot_date:s.snapshot_date,pipeline_status:s.pipeline_status,generated_at:s.generated_at,
      universe:JSON.parse(s.universe_json),changes:JSON.parse(s.changes_json),
      freshness:JSON.parse(s.freshness_json),review_queue:q.results||[]});
  } catch(error) {
    return json({error:"Universe status unavailable",detail:String(error?.message||error)},503);
  }
}

async function stockExplorer(url, env) {
  if (!env.DB) return json({ error: "DB binding is not configured" }, 503);
  try {
    await syncUniverseIfNeeded(env);
    await syncFundamentalsIfNeeded(env);
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
    const universeSync = await syncUniverseIfNeeded(env);
    const fundamentalsSync = await syncFundamentalsIfNeeded(env);
    const states = await env.DB.prepare(
      "SELECT * FROM serving_refresh_state WHERE dataset IN ('universe','fundamentals') ORDER BY dataset"
    ).all();
    const byDataset = Object.fromEntries((states.results || []).map(x => [x.dataset, x]));
    return json({
      ok: true,
      universe: byDataset.universe || null,
      fundamentals: byDataset.fundamentals || null,
      sync: { universe: universeSync, fundamentals: fundamentalsSync }
    });
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
    if (url.pathname === "/api/universe-status") return dashboardStatus(env);
    if (url.pathname === "/api/stocks") return stockExplorer(url, env);
    if (url.pathname === "/api/serving-status") return servingStatus(env);
    if (url.pathname === "/api/r2-usage") return r2Usage(env);
    return env.ASSETS ? env.ASSETS.fetch(request) : new Response("Not Found", { status: 404 });
  }
};

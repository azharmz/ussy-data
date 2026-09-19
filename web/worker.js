const SORT_COLUMNS = new Set([
  "ticker","company_name","eps_yoy_latest","revenue_yoy_latest",
  "annual_eps_growth","fundamental_as_of"
]);

function json(data, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
}
function num(v) {
  if (v === null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

async function stockExplorer(url, env) {
  if (!env.DB) return json({ error: "DB binding is not configured" }, 503);
  const p = url.searchParams;
  const where = [], binds = [];
  const q = (p.get("q") || "").trim();
  if (q) {
    where.push("(ticker LIKE ? OR company_name LIKE ?)");
    binds.push(q.toUpperCase() + "%", "%" + q + "%");
  }
  const compliance = p.get("compliance");
  if (compliance) { where.push("sharia_compliance = ?"); binds.push(compliance); }
  const status = p.get("fundamental_status");
  if (status) { where.push("fundamental_status = ?"); binds.push(status); }

  for (const [param, col, op] of [
    ["eps_yoy_min","eps_yoy_latest",">="],["eps_yoy_max","eps_yoy_latest","<="],
    ["revenue_yoy_min","revenue_yoy_latest",">="],["revenue_yoy_max","revenue_yoy_latest","<="],
    ["annual_growth_min","annual_eps_growth",">="],["annual_growth_max","annual_eps_growth","<="]
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

async function r2Usage(env) {
  const bucket = env.R2_BUCKET;
  if (!bucket || typeof bucket.list !== "function") return json({ error: "R2_BUCKET binding is not configured" }, 503);
  let cursor, total_objects = 0, total_bytes = 0;
  const prefixes = new Map();
  do {
    const page = await bucket.list({ limit: 1000, ...(cursor ? { cursor } : {}) });
    for (const o of page.objects) {
      total_objects++; total_bytes += o.size;
      const top = o.key.includes("/") ? o.key.split("/")[0] + "/" : "(root)";
      const x = prefixes.get(top) || { prefix: top, objects: 0, bytes: 0 };
      x.objects++; x.bytes += o.size; prefixes.set(top, x);
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return json({
    total_objects, total_bytes, total_gib: total_bytes / (1024 ** 3),
    checked_at: new Date().toISOString(),
    prefixes: [...prefixes.values()].sort((a,b) => b.bytes - a.bytes)
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET") return new Response("Method Not Allowed", { status: 405 });
    if (url.pathname === "/api/stocks") return stockExplorer(url, env);
    if (url.pathname === "/api/r2-usage") return r2Usage(env);
    return env.ASSETS ? env.ASSETS.fetch(request) : new Response("Not Found", { status: 404 });
  }
};

const CACHE_TTL_MS = 60_000;
let cached = null;
let cachedAt = 0;
let inFlight = null;

async function scanBucket(bucket) {
  let cursor;
  let totalObjects = 0;
  let totalBytes = 0;
  do {
    const options = { limit: 1000 };
    if (cursor) options.cursor = cursor;
    const page = await bucket.list(options);
    for (const object of page.objects) {
      totalObjects += 1;
      totalBytes += object.size;
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);

  return {
    contract: "ussy-r2-live-usage-v1",
    source: "cloudflare-pages-r2-binding",
    scope: "ENTIRE_BUCKET",
    total_bytes: totalBytes,
    total_gib: totalBytes / (1024 ** 3),
    total_objects: totalObjects,
    checked_at: new Date().toISOString(),
  };
}

async function liveUsage(env) {
  const bucket = env.R2_BUCKET;
  if (!bucket || typeof bucket.list !== "function") {
    return Response.json(
      { error: "R2_BUCKET binding is not configured" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  const now = Date.now();
  if (cached && now - cachedAt < CACHE_TTL_MS) {
    return Response.json({ ...cached, cached: true }, {
      headers: { "Cache-Control": "public, max-age=30" },
    });
  }

  try {
    if (!inFlight) inFlight = scanBucket(bucket);
    const result = await inFlight;
    cached = result;
    cachedAt = Date.now();
    return Response.json({ ...result, cached: false }, {
      headers: { "Cache-Control": "public, max-age=30" },
    });
  } catch (error) {
    return Response.json(
      { error: "Unable to scan R2 bucket", detail: String(error?.message || error) },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  } finally {
    inFlight = null;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/r2-usage") {
      if (request.method !== "GET") {
        return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
      }
      return liveUsage(env);
    }
    return env.ASSETS.fetch(request);
  },
};

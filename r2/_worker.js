const CACHE_TTL_MS = 60_000;
let cached = null;
let cachedAt = 0;
let inFlight = null;

function newNode(name, path) {
  return { name, path, bytes: 0, objects: 0, children: new Map() };
}

function addObject(root, key, size) {
  root.bytes += size;
  root.objects += 1;
  const parts = key.split('/').filter(Boolean);
  let node = root;
  let path = '';
  // Only directory/prefix nodes are represented. File bytes still roll up into parents.
  for (const part of parts.slice(0, -1)) {
    path = path ? `${path}/${part}` : part;
    if (!node.children.has(part)) node.children.set(part, newNode(part, path));
    node = node.children.get(part);
    node.bytes += size;
    node.objects += 1;
  }
}

function serializeNode(node) {
  return {
    name: node.name,
    path: node.path,
    bytes: node.bytes,
    gib: node.bytes / (1024 ** 3),
    objects: node.objects,
    children: [...node.children.values()]
      .sort((a, b) => b.bytes - a.bytes || a.name.localeCompare(b.name))
      .map(serializeNode),
  };
}

async function scanBucket(bucket) {
  let cursor;
  let totalObjects = 0;
  let totalBytes = 0;
  const root = newNode('/', '');
  do {
    const options = { limit: 1000 };
    if (cursor) options.cursor = cursor;
    const page = await bucket.list(options);
    for (const object of page.objects) {
      totalObjects += 1;
      totalBytes += object.size;
      addObject(root, object.key, object.size);
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);

  return {
    contract: "ussy-r2-live-usage-v2",
    source: "cloudflare-pages-r2-binding",
    scope: "ENTIRE_BUCKET",
    total_bytes: totalBytes,
    total_gib: totalBytes / (1024 ** 3),
    total_objects: totalObjects,
    checked_at: new Date().toISOString(),
    tree: serializeNode(root).children,
  };
}

async function liveUsage(env) {
  const bucket = env.R2_BUCKET;
  if (!bucket || typeof bucket.list !== "function") {
    return Response.json({ error: "R2_BUCKET binding is not configured" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
  const now = Date.now();
  if (cached && now - cachedAt < CACHE_TTL_MS) {
    return Response.json({ ...cached, cached: true }, { headers: { "Cache-Control": "public, max-age=30" } });
  }
  try {
    if (!inFlight) inFlight = scanBucket(bucket);
    const result = await inFlight;
    cached = result;
    cachedAt = Date.now();
    return Response.json({ ...result, cached: false }, { headers: { "Cache-Control": "public, max-age=30" } });
  } catch (error) {
    return Response.json({ error: "Unable to scan R2 bucket", detail: String(error?.message || error) }, { status: 500, headers: { "Cache-Control": "no-store" } });
  } finally { inFlight = null; }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/r2-usage") {
      if (request.method !== "GET") return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET" } });
      return liveUsage(env);
    }
    return env.ASSETS.fetch(request);
  },
};

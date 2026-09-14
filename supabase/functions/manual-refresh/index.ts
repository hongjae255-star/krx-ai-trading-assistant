const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type, x-refresh-key",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "content-type": "application/json; charset=utf-8" },
  });
}

async function digest(text: string): Promise<Uint8Array> {
  const data = new TextEncoder().encode(text);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", data));
}

async function secureEqual(a: string, b: string): Promise<boolean> {
  const [da, db] = await Promise.all([digest(a), digest(b)]);
  if (da.length !== db.length) return false;
  let diff = 0;
  for (let i = 0; i < da.length; i++) diff |= da[i] ^ db[i];
  return diff === 0;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const expectedKey = Deno.env.get("MANUAL_REFRESH_KEY") || "";
  const githubToken = Deno.env.get("GITHUB_ACTIONS_TOKEN") || "";
  const owner = Deno.env.get("GITHUB_OWNER") || "hongjae255-star";
  const repo = Deno.env.get("GITHUB_REPO") || "krx-ai-trading-assistant";
  const ref = Deno.env.get("GITHUB_REF") || "main";

  if (!expectedKey || !githubToken) {
    return json({ error: "server_not_configured" }, 503);
  }

  const suppliedKey = req.headers.get("x-refresh-key") || "";
  if (!suppliedKey || !(await secureEqual(suppliedKey, expectedKey))) {
    return json({ error: "unauthorized" }, 401);
  }

  let body: { market?: string } = {};
  try { body = await req.json(); } catch (_) { /* default KR */ }
  const market = String(body.market || "KR").toUpperCase() === "US" ? "US" : "KR";
  const job = market === "US" ? "refresh-us" : "refresh-kr";

  const url = `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions/workflows/cloud-manual.yml/dispatches`;
  const gh = await fetch(url, {
    method: "POST",
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${githubToken}`,
      "X-GitHub-Api-Version": "2026-03-10",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref, inputs: { job, full_scan: true } }),
  });

  if (!gh.ok) {
    const detail = (await gh.text()).slice(0, 500);
    return json({ error: "github_dispatch_failed", status: gh.status, detail }, 502);
  }

  return json({ accepted: true, market, job }, 202);
});

import type { NextRequest } from "next/server";

// Read-only reverse proxy to local claude-fleet. Keep this route narrow:
// Next middleware excludes api/*, so only explicitly allow endpoints the UI
// renders today.
const FLEET_ORIGIN = process.env.FLEET_ORIGIN ?? "http://127.0.0.1:7878";
const FLEET_ENABLED = ["1", "true", "yes"].includes(
  (process.env.EXP_FLEET_ENABLED ?? "").toLowerCase(),
);

export const dynamic = "force-dynamic";

function json(status: number, payload: Record<string, unknown>): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
    },
  });
}

function allowedFleetPath(path: string[]): string | null {
  if (path.length === 1 && path[0] === "windows") return "windows";
  if (path.length === 3 && path[0] === "windows" && /^\d+$/.test(path[1]) && path[2] === "timeline") {
    return `windows/${path[1]}/timeline`;
  }
  return null;
}

async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  if (!FLEET_ENABLED) {
    return json(404, { error: "fleet disabled" });
  }
  const safePath = allowedFleetPath(path);
  if (!safePath) {
    return json(404, { error: "fleet endpoint not allowed" });
  }

  const search = new URL(req.url).search;
  const target = `${FLEET_ORIGIN}/api/${safePath}${search}`;
  const init: RequestInit = {
    method: req.method,
    headers: { accept: req.headers.get("accept") ?? "*/*" },
  };
  try {
    const r = await fetch(target, init);
    return new Response(r.body, {
      status: r.status,
      headers: {
        "content-type": r.headers.get("content-type") ?? "application/json",
        "cache-control": "no-cache",
      },
    });
  } catch (e) {
    return json(502, { error: "fleet unreachable", detail: String(e) });
  }
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await ctx.params).path);
}

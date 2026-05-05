import { NextRequest, NextResponse } from "next/server";

const DEFAULT_REVIEWER = process.env.EXP_DEFAULT_REVIEWER ?? "alice";
const SESSION_COOKIE = "exp_session";

// When the UI runs behind a path-prefix proxy (vscode/code-server
// `/ws-.../proxy/3002/...`), Next.js sees clean paths because the proxy
// strips the prefix — but redirect Location headers must INCLUDE the
// prefix, otherwise the browser resolves them against the bare host
// and ends up off the proxy. NEXT_PUBLIC_UI_BASE is set in next.config
// from EXP_UI_PUBLIC_URL's path part.
const BASE_PATH = (process.env.NEXT_PUBLIC_UI_BASE || "").replace(/\/$/, "");

// Pages that don't require login. Everything else redirects to /login
// when no session cookie is present.
const PUBLIC_PATHS = new Set<string>([
  "/login",
  "/register",
]);

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname);
}

function loginRedirect(req: NextRequest, path: string): NextResponse {
  // Build an ABSOLUTE redirect URL using the browser-facing host. The
  // upstream Host header is 127.0.0.1:3002 (because the gateway rewrites
  // it), so we trust X-Forwarded-Host / -Proto when set.
  const proto =
    req.headers.get("x-forwarded-proto") ??
    (req.nextUrl.protocol.replace(":", "") || "http");
  const host =
    req.headers.get("x-forwarded-host") ??
    req.headers.get("host") ??
    req.nextUrl.host;
  let target = `${proto}://${host}${BASE_PATH}/login`;
  if (path !== "/") {
    target += `?next=${encodeURIComponent(path)}`;
  }
  return NextResponse.redirect(target, 307);
}

export function middleware(req: NextRequest) {
  const hasSession = !!req.cookies.get(SESSION_COOKIE)?.value;
  const path = req.nextUrl.pathname;

  if (!hasSession && !isPublic(path)) {
    return loginRedirect(req, path);
  }

  const res = NextResponse.next();
  if (!hasSession && !req.cookies.get("X-Reviewer-Name")) {
    res.cookies.set("X-Reviewer-Name", DEFAULT_REVIEWER, {
      httpOnly: false,
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24 * 30,
    });
  }
  return res;
}

export const config = {
  matcher: ["/((?!_next/|api/|favicon.ico).*)"],
};

import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "exp_session";

// When the UI runs behind a path-prefix proxy, Next.js sees clean paths
// because the proxy strips the prefix. Redirect Location headers still
// need that browser-facing prefix, otherwise users land outside the proxy.
function configuredBasePath(): string {
  const publicUrl = process.env.EXP_UI_PUBLIC_URL?.replace(/\/$/, "");
  if (publicUrl) {
    try {
      return new URL(publicUrl).pathname.replace(/\/$/, "");
    } catch {
      // Fall through to NEXT_PUBLIC_UI_BASE below.
    }
  }
  return (process.env.NEXT_PUBLIC_UI_BASE || "").replace(/\/$/, "");
}

const BASE_PATH = configuredBasePath();

// Pages that don't require login. Everything else redirects to /login
// when no session cookie is present.
const PUBLIC_PATHS = new Set<string>([
  "/login",
  "/register",
  "/api-docs",
  "/plugins",
]);

function inferBasePath(pathname: string): string {
  if (BASE_PATH) return BASE_PATH;
  const m = pathname.match(/^(.*\/proxy\/3002)(?:\/|$)/);
  return m?.[1] ?? "";
}

function stripBasePath(pathname: string, basePath: string): string {
  if (!basePath) return pathname;
  if (pathname === basePath) return "/";
  if (pathname.startsWith(basePath + "/")) return pathname.slice(basePath.length);
  return pathname;
}

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname);
}

function loginRedirect(req: NextRequest, path: string, basePath: string): NextResponse {
  // Build an absolute redirect URL using the browser-facing host. The
  // upstream Host header is often local because a gateway rewrites it, so
  // trust X-Forwarded-Host / -Proto when set.
  const proto =
    req.headers.get("x-forwarded-proto") ??
    (req.nextUrl.protocol.replace(":", "") || "http");
  const host =
    req.headers.get("x-forwarded-host") ??
    req.headers.get("host") ??
    req.nextUrl.host;
  let target = `${proto}://${host}${basePath}/login`;
  if (path !== "/") {
    target += `?next=${encodeURIComponent(path)}`;
  }
  return NextResponse.redirect(target, 307);
}

export function proxy(req: NextRequest) {
  const hasSession = !!req.cookies.get(SESSION_COOKIE)?.value;
  const basePath = inferBasePath(req.nextUrl.pathname);
  const path = stripBasePath(req.nextUrl.pathname, basePath);

  if (!hasSession && !isPublic(path)) {
    return loginRedirect(req, path, basePath);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/|api/|favicon.ico).*)"],
};

const ROUTE_SEGMENTS = new Set([
  "admin",
  "api",
  "api-docs",
  "clusters",
  "community",
  "experiences",
  "fleet",
  "login",
  "me",
  "plugins",
  "projects",
  "register",
  "recall",
  "rewards",
  "search",
  "sessions",
  "skills",
]);

function normalizeBase(value: string): string {
  if (!value || value === "/") return "";
  return value.replace(/\/+$/, "");
}

function baseFromPublicUrl(): string {
  const explicit =
    process.env.EXP_UI_PUBLIC_URL ||
    process.env.EXP_PUBLIC_BASE_URL ||
    process.env.NEXT_PUBLIC_UI_BASE ||
    "";
  if (!explicit) return "";
  if (explicit.startsWith("/")) return normalizeBase(explicit);
  try {
    return normalizeBase(new URL(explicit).pathname);
  } catch {
    return "";
  }
}

function baseFromBrowserPath(): string {
  if (typeof window === "undefined") return "";
  const path = normalizeBase(window.location.pathname);
  if (!path) return "";

  const segments = path.split("/").filter(Boolean);
  for (let i = 0; i < segments.length; i += 1) {
    if (ROUTE_SEGMENTS.has(segments[i])) {
      return i === 0 ? "" : `/${segments.slice(0, i).join("/")}`;
    }
  }

  const proxyIndex = segments.findIndex((segment) => segment === "proxy");
  if (proxyIndex >= 0 && segments[proxyIndex + 1]) {
    return `/${segments.slice(0, proxyIndex + 2).join("/")}`;
  }
  return "";
}

export function basePath(): string {
  return baseFromPublicUrl() || baseFromBrowserPath();
}

export const BASE = basePath();

function publicBaseUrl(): string {
  const explicit =
    process.env.EXP_UI_PUBLIC_URL ||
    process.env.EXP_PUBLIC_BASE_URL ||
    "";
  if (!explicit || explicit.startsWith("/")) return "";
  try {
    const url = new URL(explicit);
    return `${url.origin}${normalizeBase(url.pathname)}`;
  } catch {
    return "";
  }
}

function stripBase(path: string): string {
  const base = basePath();
  if (!base) return path;
  if (path === base) return "/";
  if (path.startsWith(base + "/")) return path.slice(base.length);
  return path;
}

export function withBase(path: string): string {
  const base = basePath();
  if (!base) return path;
  if (!path.startsWith("/")) return path;
  if (path.startsWith(base + "/") || path === base) return path;
  return base + path;
}

export function withPublicBase(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  const publicBase = publicBaseUrl();
  if (!publicBase) return withBase(path);
  const relativePath = stripBase(path);
  return `${publicBase}${relativePath === "/" ? "/" : relativePath}`;
}

export const BASE = (process.env.NEXT_PUBLIC_UI_BASE ?? "").replace(/\/$/, "");

export function withBase(path: string): string {
  if (!BASE) return path;
  if (!path.startsWith("/")) return path;
  if (path.startsWith(BASE + "/") || path === BASE) return path;
  return BASE + path;
}

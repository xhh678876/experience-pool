// Thin server-side wrapper around the FastAPI user-auth endpoints.
//
// The UI runs on Next.js (port 3000/3002). The FastAPI gateway runs on a
// different port (default 8081 internally, may be reverse-proxied externally).
// Browser cookies are scoped to the Next.js origin, so we always proxy
// through Next.js server actions / API routes — never expose the FastAPI
// URL to the browser fetch.

import { cookies } from "next/headers";

export const SESSION_COOKIE = "exp_session";

export function apiBase(): string {
  return (
    process.env.EXP_API_BASE ??
    process.env.NEXT_PUBLIC_EXP_API_BASE ??
    "http://127.0.0.1:8081"
  ).replace(/\/$/, "");
}

export interface RegisterResult {
  ok: boolean;
  status: number;
  message?: string;
  user_id?: string;
  email?: string;
  default_agent_name?: string;
  agent_id?: string;
  bind_command?: string;
}

export interface LoginResult {
  ok: boolean;
  status: number;
  message?: string;
  user_id?: string;
  email?: string;
  default_agent_name?: string;
}

export interface MeResult {
  user_id: string;
  email: string;
  default_agent_name: string;
  display_name: string | null;
}

export interface BindScriptResult {
  agent_name: string;
  agent_id: string;
  team: string;
  secret_hint: string;
  bind_command: string;
  /** Standalone one-liner that downloads + runs session-extractor; uploads
   * always acl=private (hardcoded in the Python). */
  extract_command?: string;
  base_url: string;
}

/** Extract Set-Cookie value(s) for our session cookie and re-set them on the
 * Next.js cookie store so the user's browser holds them. */
async function forwardSessionCookie(setCookie: string | null): Promise<string | null> {
  if (!setCookie) return null;
  const re = new RegExp(`${SESSION_COOKIE}=([^;]+)`, "i");
  const m = re.exec(setCookie);
  if (!m) return null;
  const token = m[1];
  // Parse Max-Age if present.
  const ageMatch = /max-age=(\d+)/i.exec(setCookie);
  const maxAge = ageMatch ? parseInt(ageMatch[1], 10) : 60 * 60 * 24 * 30;
  const c = await cookies();
  c.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge,
  });
  return token;
}

export async function apiRegister(input: {
  email: string;
  password: string;
  display_name?: string;
}): Promise<RegisterResult> {
  const resp = await fetch(`${apiBase()}/v1/users/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
    redirect: "manual",
    cache: "no-store",
  });
  const setCookie = resp.headers.get("set-cookie");
  let body: any = {};
  try {
    body = await resp.json();
  } catch {
    body = {};
  }
  if (!resp.ok) {
    return { ok: false, status: resp.status, message: body?.detail ?? "register failed" };
  }
  await forwardSessionCookie(setCookie);
  return {
    ok: true,
    status: resp.status,
    user_id: body.user_id,
    email: body.email,
    default_agent_name: body.default_agent_name,
    agent_id: body.agent_id,
    bind_command: body.bind_command,
  };
}

export async function apiLogin(input: {
  email: string;
  password: string;
}): Promise<LoginResult> {
  const resp = await fetch(`${apiBase()}/v1/users/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
    redirect: "manual",
    cache: "no-store",
  });
  const setCookie = resp.headers.get("set-cookie");
  let body: any = {};
  try {
    body = await resp.json();
  } catch {
    body = {};
  }
  if (!resp.ok) {
    return { ok: false, status: resp.status, message: body?.detail ?? "login failed" };
  }
  await forwardSessionCookie(setCookie);
  return {
    ok: true,
    status: resp.status,
    user_id: body.user_id,
    email: body.email,
    default_agent_name: body.default_agent_name,
  };
}

export async function apiLogout(): Promise<void> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (tok) {
    try {
      await fetch(`${apiBase()}/v1/users/logout`, {
        method: "POST",
        headers: { cookie: `${SESSION_COOKIE}=${tok}` },
        cache: "no-store",
      });
    } catch {
      // Best-effort: even if the server can't be reached, we still want
      // the local cookie cleared so the user is logged out from the UI.
    }
  }
  c.delete(SESSION_COOKIE);
}

async function withSession<T>(path: string): Promise<T | null> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (!tok) return null;
  try {
    const resp = await fetch(`${apiBase()}${path}`, {
      headers: { cookie: `${SESSION_COOKIE}=${tok}` },
      cache: "no-store",
    });
    if (!resp.ok) return null;
    return (await resp.json()) as T;
  } catch {
    return null;
  }
}

export async function apiMe(): Promise<MeResult | null> {
  return withSession<MeResult>("/v1/users/me");
}

export async function apiBindScript(): Promise<BindScriptResult | null> {
  return withSession<BindScriptResult>("/v1/users/me/bind-script");
}

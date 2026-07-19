// Thin server-side wrapper around the FastAPI user-auth endpoints.
//
// The UI runs on Next.js (port 3000). The FastAPI gateway runs on a
// different port (default 8080, may be reverse-proxied externally).
// Browser cookies are scoped to the Next.js origin, so we always proxy
// through Next.js server actions / API routes — never expose the FastAPI
// URL to the browser fetch.

import { cookies } from "next/headers";

export const SESSION_COOKIE = "exp_session";

export function apiBase(): string {
  return (
    process.env.EXP_API_BASE ??
    process.env.NEXT_PUBLIC_EXP_API_BASE ??
    "http://127.0.0.1:8080"
  ).replace(/\/$/, "");
}

function envTruthy(value: string | undefined): boolean {
  return ["1", "true", "yes", "on"].includes((value ?? "").toLowerCase());
}

function envFalsey(value: string | undefined): boolean {
  return ["0", "false", "no", "off"].includes((value ?? "").toLowerCase());
}

function secureSessionCookie(): boolean {
  const configured = process.env.EXP_SESSION_COOKIE_SECURE;
  if (envTruthy(configured)) return true;
  if (envFalsey(configured)) return false;
  const publicUrl =
    process.env.EXP_UI_PUBLIC_URL ||
    process.env.EXP_PUBLIC_BASE_URL ||
    process.env.EXP_BIND_BASE_URL ||
    "";
  return publicUrl.startsWith("https://");
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

export interface PluginPackageResult {
  name: string;
  version: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  mtime: string;
  download_url: string;
  install_script_url: string;
  install_script_command: string;
  install_command: string;
}

export interface RagChunkResult {
  chunk_id: string;
  experience_id: string;
  chunk_type: string;
  text: string;
  turn_start: number | null;
  turn_end: number | null;
  meta: Record<string, unknown>;
  token_count: number;
  similarity: number;
  keyword: number;
  fts: number;
  lexical: number;
  coverage: number;
  action_lexical: number;
  situation_lexical: number;
  outcome_lexical: number;
  quality: number;
  score: number;
  source: string;
  owner: string;
  agent_name: string;
  team: string;
  task_type: string;
  sensitivity: string;
  created_at: string;
  session_id: string | null;
  parent_session_id: string | null;
}

export interface RagExperienceResult {
  experience_id: string;
  query: string | null;
  intent_text: string | null;
  outcome: string | null;
  summary: string | null;
  task_type: string;
  sensitivity: string;
  created_at: string;
  session_id: string | null;
  source_agent_type: string | null;
  parent_session_id: string | null;
  segment_id: string | null;
  source_byte_start: number | null;
  source_byte_end: number | null;
  task_status: string | null;
  agent_name: string;
}

export interface RagContextResult {
  event_id: string | null;
  context: string;
  chunks: RagChunkResult[];
  experiences: RagExperienceResult[];
  scope: string;
  scope_meta: {
    viewer: string;
    viewer_owner: string;
    project: Record<string, unknown> | null;
    community_unlocked: boolean;
  };
  retrieval_meta: {
    query_terms: string[];
    vector_index_size: number;
    prefetched_vectors: number;
    acl_candidates: number;
    reranked_candidates: number;
    accepted_candidates: number;
    returned_chunks: number;
    max_chunks_per_experience: number;
    min_score: number;
  };
}

export interface RagContextCallResult {
  ok: boolean;
  status: number;
  message?: string;
  data?: RagContextResult;
}

export async function apiPluginPackage(): Promise<PluginPackageResult | null> {
  try {
    const resp = await fetch(`${apiBase()}/v1/plugin/package`, {
      cache: "no-store",
    });
    if (!resp.ok) return null;
    return (await resp.json()) as PluginPackageResult;
  } catch {
    return null;
  }
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
    secure: secureSessionCookie(),
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
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (process.env.EXP_USER_REGISTER_TOKEN) {
    headers["x-user-register-token"] = process.env.EXP_USER_REGISTER_TOKEN;
  }
  const resp = await fetch(`${apiBase()}/v1/users/register`, {
    method: "POST",
    headers,
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

export async function apiRagContext(input: {
  q: string;
  top_k?: number;
  task_type?: string;
  scope?: string;
  project?: string;
  record_event?: boolean;
}): Promise<RagContextCallResult> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (!tok) return { ok: false, status: 401, message: "not logged in" };
  try {
    const resp = await fetch(`${apiBase()}/v1/rag/context`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        cookie: `${SESSION_COOKIE}=${tok}`,
      },
      body: JSON.stringify(input),
      cache: "no-store",
    });
    let body: unknown = {};
    try {
      body = await resp.json();
    } catch {
      body = {};
    }
    if (!resp.ok) {
      const errorBody = body as { detail?: string; error?: string };
      return {
        ok: false,
        status: resp.status,
        message: errorBody.detail ?? errorBody.error ?? "RAG search failed",
      };
    }
    return { ok: true, status: resp.status, data: body as RagContextResult };
  } catch (error) {
    return {
      ok: false,
      status: 503,
      message: error instanceof Error ? error.message : "RAG service unavailable",
    };
  }
}

// ---- API keys ----

export interface ApiKeyRow {
  key_id: string;
  user_id: string;
  agent_name: string;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
  revoked_at: string | null;
}

export interface ApiKeyListResult {
  keys: ApiKeyRow[];
}

export interface ApiKeyCreateResult extends ApiKeyRow {
  api_key: string;
  warning: string;
}

export interface PairingCodeCreateResult {
  pair_id: string;
  user_id: string;
  agent_name: string;
  name: string;
  code_prefix: string;
  created_at: string;
  expires_at: string;
  claimed: boolean;
  claimed_at: string | null;
  claimed_key_id: string | null;
  code: string;
  warning: string;
}

export async function apiListApiKeys(): Promise<ApiKeyListResult | null> {
  return withSession<ApiKeyListResult>("/v1/users/me/api-keys");
}

export interface CreateApiKeyResult {
  ok: boolean;
  status: number;
  message?: string;
  data?: ApiKeyCreateResult;
}

export async function apiCreateApiKey(name: string): Promise<CreateApiKeyResult> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (!tok) return { ok: false, status: 401, message: "not logged in" };
  const resp = await fetch(`${apiBase()}/v1/users/me/api-keys`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      cookie: `${SESSION_COOKIE}=${tok}`,
    },
    body: JSON.stringify({ name }),
    cache: "no-store",
  });
  let body: any = {};
  try {
    body = await resp.json();
  } catch {
    body = {};
  }
  if (!resp.ok) {
    return { ok: false, status: resp.status, message: body?.detail ?? "create failed" };
  }
  return { ok: true, status: resp.status, data: body as ApiKeyCreateResult };
}

export interface CreatePairingCodeResult {
  ok: boolean;
  status: number;
  message?: string;
  data?: PairingCodeCreateResult;
}

export async function apiCreatePairingCode(
  name: string,
  ttlSeconds = 10 * 60,
): Promise<CreatePairingCodeResult> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (!tok) return { ok: false, status: 401, message: "not logged in" };
  const resp = await fetch(`${apiBase()}/v1/users/me/pairing-codes`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      cookie: `${SESSION_COOKIE}=${tok}`,
    },
    body: JSON.stringify({ name, ttl_seconds: ttlSeconds }),
    cache: "no-store",
  });
  let body: any = {};
  try {
    body = await resp.json();
  } catch {
    body = {};
  }
  if (!resp.ok) {
    return { ok: false, status: resp.status, message: body?.detail ?? "create failed" };
  }
  return { ok: true, status: resp.status, data: body as PairingCodeCreateResult };
}

export async function apiRevokeApiKey(keyId: string): Promise<{ ok: boolean; status: number; message?: string }> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (!tok) return { ok: false, status: 401, message: "not logged in" };
  const resp = await fetch(`${apiBase()}/v1/users/me/api-keys/${encodeURIComponent(keyId)}`, {
    method: "DELETE",
    headers: { cookie: `${SESSION_COOKIE}=${tok}` },
    cache: "no-store",
  });
  if (!resp.ok) {
    let body: any = {};
    try { body = await resp.json(); } catch { /* ignore */ }
    return { ok: false, status: resp.status, message: body?.detail ?? "revoke failed" };
  }
  return { ok: true, status: resp.status };
}

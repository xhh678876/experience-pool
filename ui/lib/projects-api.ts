import { cookies } from "next/headers";
import { apiBase, SESSION_COOKIE } from "./users-api";

type ApiResult<T> = {
  ok: boolean;
  status: number;
  data?: T;
  message?: string;
};

export type ProjectSummary = {
  project_id: string;
  slug: string;
  name: string;
  created_by_owner: string;
  created_at: string;
  role?: string;
  relation?: string;
  shared_owners?: number;
  member_count?: number;
  include_high_sensitivity?: number;
};

export type ProjectDetail = {
  project_id: string;
  slug: string;
  name: string;
  members: {
    user_id: string;
    email: string;
    display_name: string | null;
    role: string;
    status: string;
    joined_at: string;
  }[];
  grants: {
    owner: string;
    include_high_sensitivity: number;
    created_at: string;
    revoked_at: string | null;
  }[];
  invites: {
    invite_id: string;
    email: string;
    role: string;
    created_at: string;
    expires_at: string;
    accepted_at: string | null;
    revoked_at: string | null;
  }[];
};

export type ProjectInvite = {
  invite_id: string;
  project_id: string;
  project_slug: string;
  email: string;
  role: string;
  expires_at: string;
  token: string;
};

export type RagChunk = {
  chunk_id: string;
  experience_id: string;
  chunk_type: string;
  text: string;
  similarity: number;
  score: number;
  source: string;
  owner: string;
  agent_name: string;
  task_type: string;
  sensitivity: string;
};

export type RagContext = {
  context: string;
  chunks: RagChunk[];
  experiences: {
    experience_id: string;
    query: string | null;
    intent_text: string | null;
    outcome: string | null;
    summary: string | null;
    task_type: string;
    sensitivity: string;
    created_at: string;
    agent_name: string;
  }[];
  scope: string;
  scope_meta: {
    viewer: string;
    viewer_owner: string;
    project: null | {
      project_id: string;
      slug: string;
      name: string;
      shared_owners: string[];
    };
    community_unlocked: boolean;
  };
};

async function sessionHeaders(): Promise<Record<string, string> | null> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (!tok) return null;
  return { cookie: `${SESSION_COOKIE}=${tok}` };
}

async function parse<T>(resp: Response): Promise<ApiResult<T>> {
  let body: any = {};
  try {
    body = await resp.json();
  } catch {
    body = {};
  }
  if (!resp.ok) {
    return {
      ok: false,
      status: resp.status,
      message: body?.detail ?? body?.error ?? "request failed",
    };
  }
  return { ok: true, status: resp.status, data: body as T };
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiResult<T>> {
  const headers = await sessionHeaders();
  if (!headers) return { ok: false, status: 401, message: "not logged in" };
  try {
    const resp = await fetch(`${apiBase()}${path}`, {
      ...init,
      headers: {
        ...headers,
        ...(init.headers ?? {}),
      },
      cache: "no-store",
    });
    return parse<T>(resp);
  } catch (err) {
    return {
      ok: false,
      status: 500,
      message: err instanceof Error ? err.message : "request failed",
    };
  }
}

export async function apiListProjects(): Promise<ApiResult<{ projects: ProjectSummary[] }>> {
  return request<{ projects: ProjectSummary[] }>("/v1/projects");
}

export async function apiCreateProject(input: {
  name: string;
  slug?: string;
}): Promise<ApiResult<ProjectDetail>> {
  return request<ProjectDetail>("/v1/projects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function apiGetProject(slug: string): Promise<ApiResult<ProjectDetail>> {
  return request<ProjectDetail>(`/v1/projects/${encodeURIComponent(slug)}`);
}

export async function apiCreateProjectInvite(
  slug: string,
  input: { email: string; role: string; ttl_seconds?: number },
): Promise<ApiResult<ProjectInvite>> {
  return request<ProjectInvite>(`/v1/projects/${encodeURIComponent(slug)}/invites`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function apiAcceptProjectInvite(token: string): Promise<ApiResult<{
  ok: boolean;
  project_id: string;
  project_slug: string;
  project_name: string;
  role: string;
}>> {
  return request("/v1/projects/invites/accept", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  });
}

export async function apiGrantMyPool(
  slug: string,
  includeHighSensitivity: boolean,
): Promise<ApiResult<{ ok: boolean; project_id: string; owner: string }>> {
  return request(`/v1/projects/${encodeURIComponent(slug)}/grants/me`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ include_high_sensitivity: includeHighSensitivity }),
  });
}

export async function apiRevokeProjectGrant(
  slug: string,
  owner: string,
): Promise<ApiResult<{ ok: boolean; project_id: string; owner: string }>> {
  return request(
    `/v1/projects/${encodeURIComponent(slug)}/grants/${encodeURIComponent(owner)}`,
    { method: "DELETE" },
  );
}

export async function apiRagContext(input: {
  q: string;
  scope: string;
  project?: string;
  top_k?: number;
  task_type?: string;
}): Promise<ApiResult<RagContext>> {
  return request<RagContext>("/v1/rag/context", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

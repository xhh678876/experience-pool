import { cookies } from "next/headers";
import { getDb } from "./db";

const SESSION_COOKIE = "exp_session";

export interface CurrentUser {
  email: string;
  default_agent_name: string;
  user_id: string;
}

/** Resolve the logged-in user from the session cookie, by checking the
 * shared pool.db's user_sessions table directly. Returns null if there
 * is no valid (non-revoked, non-expired) session. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  const c = await cookies();
  const tok = c.get(SESSION_COOKIE)?.value;
  if (!tok) return null;
  try {
    const db = getDb();
    const row = db
      .prepare(
        `SELECT u.user_id, u.email, u.default_agent_name
         FROM user_sessions s
         JOIN users u ON u.user_id = s.user_id
         WHERE s.token = ?
           AND s.revoked = 0
           AND s.expires_at > datetime('now')`
      )
      .get(tok) as
      | { user_id: string; email: string; default_agent_name: string }
      | undefined;
    return row ?? null;
  } catch {
    // users / user_sessions tables may not exist yet on a fresh pool.db;
    // treat as anonymous rather than 500.
    return null;
  }
}

/** What identity should DB queries (e.g. listMyExperiences) treat as
 * "the current viewer"? Real session wins; otherwise legacy reviewer
 * cookie; otherwise "anonymous". */
export async function getReviewerName(): Promise<string> {
  const me = await getCurrentUser();
  if (me) return me.default_agent_name;
  return "anonymous";
}

export async function requireReviewerName(): Promise<string> {
  const me = await getCurrentUser();
  if (!me) {
    throw new Error("login required");
  }
  return me.default_agent_name;
}

export function actorString(reviewer: string): string {
  return `reviewer:${reviewer}`;
}
